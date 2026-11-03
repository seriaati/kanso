"""Unsubscribe the way the sender's List-Unsubscribe header offers, preferring what kanso can do
itself: one-click (RFC 8058), then an email, then a page the user opens. The header isn't kept in
the mirror, so it is read from Gmail on demand."""

import asyncio
import ipaddress
import logging
import re
import socket
from email.message import EmailMessage
from typing import Annotated, Literal
from urllib.parse import parse_qs, unquote, urlsplit

import httpx2 as httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import Account, Message, Thread
from kanso.db.session import get_session
from kanso.gmail import client as gmail_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/threads")
DB = Annotated[AsyncSession, Depends(get_session)]


class UnsubscribeOut(BaseModel):
    method: Literal["one-click", "email", "link"]
    url: str | None = None  # "link": the page to open


async def _public(host: str, port: int) -> bool:
    """The one-click POST goes to a URL from a stranger's email: never into the user's network."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


async def _one_click(url: str) -> bool:
    parts = urlsplit(url)
    if not parts.hostname or not await _public(parts.hostname, parts.port or 443):
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.post(url, data={"List-Unsubscribe": "One-Click"})
    except httpx.HTTPError as error:
        log.info("one-click unsubscribe failed: %s", error)
        return False
    return response.status_code < 400


def _unsubscribe_email(uri: str) -> bytes:
    parts = urlsplit(uri)
    query = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
    msg = EmailMessage()
    msg["To"] = unquote(parts.path)
    msg["Subject"] = query.get("subject") or "unsubscribe"
    msg.set_content(query.get("body") or "unsubscribe")
    return msg.as_bytes()


@router.post("/{thread_id}/unsubscribe")
async def unsubscribe(thread_id: int, db: DB) -> UnsubscribeOut:
    thread = await db.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    account = await db.get(Account, thread.account_id)
    assert account is not None
    if account.demo:
        raise HTTPException(status_code=400, detail="The demo mailbox can’t unsubscribe.")
    # The newest message someone else sent (so never a draft).
    gmail_id = await db.scalar(
        select(Message.gmail_id)
        .where(
            Message.thread_id == thread_id,
            func.lower(Message.from_email) != account.email.lower(),
        )
        .order_by(Message.internal_date.desc(), Message.id.desc())
        .limit(1)
    )
    if gmail_id is None:
        raise HTTPException(status_code=400, detail="There’s nothing to unsubscribe from here.")

    async with gmail_client.http_client() as http:
        gmail = gmail_client.Gmail(account, http)
        try:
            headers = await gmail.message_headers(
                gmail_id, ["List-Unsubscribe", "List-Unsubscribe-Post"]
            )
        except gmail_client.NotFound as error:
            raise HTTPException(status_code=404, detail="Message is gone from Gmail") from error
        uris = [u.strip() for u in re.findall(r"<([^>]*)>", headers.get("list-unsubscribe", ""))]
        links = [u for u in uris if u.lower().startswith("https://")]
        mailto = next((u for u in uris if re.match(r"(?i)mailto:[^?]", u)), None)
        one_click = "one-click" in headers.get("list-unsubscribe-post", "").lower()
        if one_click and links and await _one_click(links[0]):
            return UnsubscribeOut(method="one-click")
        if mailto:
            try:
                await gmail.send_message(_unsubscribe_email(mailto))
            except gmail_client.GmailError as error:
                log.warning("unsubscribe email not sent: %s", error)
                raise HTTPException(status_code=502, detail="Gmail didn’t send it.") from error
            return UnsubscribeOut(method="email")
    links += [u for u in uris if u.lower().startswith("http://")]
    if links:
        return UnsubscribeOut(method="link", url=links[0])
    raise HTTPException(status_code=422, detail="This sender doesn’t offer a way to unsubscribe.")
