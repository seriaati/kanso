import html
import logging
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db import contacts
from kanso.db.models import Draft, Thread
from kanso.db.session import get_session
from kanso.gmail import client as gmail_client
from kanso.gmail import oauth
from kanso.sync import drafts

log = logging.getLogger(__name__)
router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


class Address(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(pattern=r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+$", max_length=320)


class NewDraft(BaseModel):
    mode: Literal["new", "reply", "reply_all", "forward"]
    message_id: int | None = None  # what is replied to or forwarded
    account_id: int | None = None  # for a new message: who sends it (default: the first account)
    tz: str | None = Field(default=None, max_length=64)  # for the date in the quote line


class OpenDraft(BaseModel):
    message_id: int  # a mirrored draft message


class DraftIn(BaseModel):
    account_id: int
    to: list[Address] = Field(default=[], max_length=500)
    cc: list[Address] = Field(default=[], max_length=500)
    bcc: list[Address] = Field(default=[], max_length=500)
    subject: str = Field(default="", max_length=900)
    body_html: str = Field(default="", max_length=5_000_000)


class AttachmentOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    content_id: str | None  # an image shown in the body (cid:)


class DraftOut(BaseModel):
    id: int
    account_id: int
    mode: str
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    subject: str
    body_html: str
    quote_html: str | None
    attachments: list[AttachmentOut]
    saved: bool  # written to Gmail (or, for the demo account, to the mirror)
    sending: bool
    thread_id: int | None  # the Kanso thread it is in, once there is one


class ReplyIn(BaseModel):
    """A plain-text reply to a message's sender (the Android app's notification)."""

    message_id: int
    text: str = Field(min_length=1, max_length=100_000)
    tz: str | None = Field(default=None, max_length=64)  # for the date in the quote line


class SendOut(BaseModel):
    # "pending": Gmail's answer didn't arrive; Kanso retries and never sends the message twice.
    status: Literal["sent", "pending"]
    thread_id: int | None


async def _out(db: AsyncSession, draft: Draft) -> DraftOut:
    thread_id = None
    if draft.gmail_thread_id and draft.gmail_message_id:
        thread_id = await db.scalar(
            select(Thread.id).where(
                Thread.account_id == draft.account_id, Thread.gmail_id == draft.gmail_thread_id
            )
        )
    return DraftOut(
        id=draft.id,
        account_id=draft.account_id,
        mode=draft.mode,
        to=[Address(**a) for a in draft.to_addrs],
        cc=[Address(**a) for a in draft.cc_addrs],
        bcc=[Address(**a) for a in draft.bcc_addrs],
        subject=draft.subject,
        body_html=draft.body_html,
        quote_html=draft.quote_html,
        attachments=[
            AttachmentOut(**{k: a[k] for k in AttachmentOut.model_fields})
            for a in draft.attachments
        ],
        saved=draft.gmail_message_id is not None,
        sending=draft.sending_since is not None,
        thread_id=thread_id,
    )


async def _get(db: AsyncSession, draft_id: int) -> Draft:
    draft = await db.get(Draft, draft_id, populate_existing=True)
    if draft is None:
        raise HTTPException(status_code=404, detail="This draft no longer exists.")
    return draft


def _error(error: Exception) -> HTTPException:
    if isinstance(error, drafts.DraftError):
        return HTTPException(status_code=error.status, detail=str(error))
    return HTTPException(status_code=502, detail=f"Couldn’t save the draft to Gmail: {error}")


# Errors talking to Gmail that the user should see (the draft itself is kept either way).
GMAIL_ERRORS = (gmail_client.GmailError, oauth.OAuthError, drafts.DraftError)


@router.get("/contacts")
async def search_contacts(
    db: DB, q: str = "", limit: Annotated[int, Query(ge=1, le=20)] = 8
) -> list[Address]:
    return [Address(**c) for c in await contacts.search(db, q, limit)]


@router.post("/drafts")
async def create_draft(body: NewDraft, db: DB) -> DraftOut:
    await drafts.sweep(db)
    try:
        draft = await drafts.create(db, body.mode, body.message_id, body.account_id, body.tz)
    except drafts.DraftError as error:
        raise _error(error) from error
    await db.commit()
    return await _out(db, draft)


@router.post("/drafts/open")
async def open_draft(body: OpenDraft, db: DB) -> DraftOut:
    """The editable draft behind a draft message in the mirror (also one written in Gmail)."""
    async with gmail_client.http_client() as http:
        try:
            draft = await drafts.open_message(db, body.message_id, http)
        except GMAIL_ERRORS as error:
            raise _error(error) from error
    await db.commit()
    return await _out(db, draft)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int, db: DB) -> DraftOut:
    return await _out(db, await _get(db, draft_id))


@router.put("/drafts/{draft_id}")
async def save_draft(draft_id: int, body: DraftIn, db: DB) -> DraftOut:
    async with drafts.lock(draft_id), gmail_client.http_client() as http:
        draft = await _get(db, draft_id)
        try:
            touched = await drafts.save(db, draft, body.model_dump(), http)
        except GMAIL_ERRORS as error:
            raise _error(error) from error
        await db.commit()
    await drafts.publish(draft.account_id, touched)
    return await _out(db, draft)


@router.delete("/drafts/{draft_id}", status_code=204)
async def discard_draft(draft_id: int, db: DB) -> Response:
    async with drafts.lock(draft_id), gmail_client.http_client() as http:
        draft = await _get(db, draft_id)
        account_id = draft.account_id
        try:
            touched = await drafts.discard(db, draft, http)
        except GMAIL_ERRORS as error:
            raise _error(error) from error
        await db.commit()
    await drafts.publish(account_id, touched)
    return Response(status_code=204)


@router.post("/drafts/{draft_id}/attachments")
async def add_attachment(
    draft_id: int,
    request: Request,
    db: DB,
    filename: Annotated[str, Query(max_length=255)],
    inline: bool = False,
) -> DraftOut:
    """The file is the request body; its type is the Content-Type. An inline image is shown in
    the body as cid:<content_id>."""
    async with drafts.lock(draft_id):
        draft = await _get(db, draft_id)
        room = drafts.ATTACHMENTS_LIMIT - sum(a["size"] for a in draft.attachments)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > room:
                raise HTTPException(
                    status_code=413, detail="Attachments can add up to 25 MB, as in Gmail."
                )
            chunks.append(chunk)
        mime_type = request.headers.get("content-type", "").split(";")[0].strip()
        try:
            await drafts.add_attachment(draft, filename, mime_type, b"".join(chunks), inline)
        except drafts.DraftError as error:
            raise _error(error) from error
        await db.commit()
    return await _out(db, draft)


@router.get("/drafts/{draft_id}/attachments/{attachment_id}")
async def get_attachment(draft_id: int, attachment_id: str, db: DB) -> Response:
    """What the composer shows for an inline image (and a download for anything else)."""
    draft = await _get(db, draft_id)
    async with gmail_client.http_client() as http:
        try:
            entry, content = await drafts.attachment_content(db, draft, attachment_id, http)
        except GMAIL_ERRORS as error:
            raise _error(error) from error
    await db.commit()
    image = entry["mime_type"] in drafts.IMAGE_TYPES
    return Response(
        content,
        media_type=entry["mime_type"] if image else "application/octet-stream",
        headers={
            "Content-Disposition": f"{'inline' if image else 'attachment'}; "
            f"filename*=UTF-8''{quote(entry['filename'])}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.delete("/drafts/{draft_id}/attachments/{attachment_id}")
async def remove_attachment(draft_id: int, attachment_id: str, db: DB) -> DraftOut:
    async with drafts.lock(draft_id):
        draft = await _get(db, draft_id)
        try:
            drafts.remove_attachment(draft, attachment_id)
        except drafts.DraftError as error:
            raise _error(error) from error
        await db.commit()
    return await _out(db, draft)


@router.post("/drafts/{draft_id}/send")
async def send_draft(draft_id: int, body: DraftIn, db: DB) -> SendOut:
    async with drafts.lock(draft_id), gmail_client.http_client() as http:
        draft = await _get(db, draft_id)
        if draft.sending_since is None:  # else: a retry, the final message is in Gmail already
            try:
                touched = await drafts.save(db, draft, body.model_dump(), http)
            except GMAIL_ERRORS as error:
                raise _error(error) from error
            await db.commit()
            await drafts.publish(draft.account_id, touched)
        try:
            thread_id = await drafts.send(db, draft, http)
        except drafts.DraftError as error:
            raise _error(error) from error
        except drafts.SendPending as error:
            log.warning("draft %s: send unconfirmed (%s); the worker resumes it", draft_id, error)
            return SendOut(status="pending", thread_id=None)
    return SendOut(status="sent", thread_id=thread_id)


@router.post("/reply")
async def reply(body: ReplyIn, db: DB, response: Response) -> SendOut:
    """Writes and sends a reply to the sender in one go, as a "reply" draft would be. 202 when
    Gmail's answer didn't arrive: the worker finishes it, and never sends it twice."""
    try:
        draft = await drafts.create(db, "reply", body.message_id, None, body.tz)
    except drafts.DraftError as error:
        raise _error(error) from error
    draft.body_html = html.escape(body.text).replace("\n", "<br>")
    async with drafts.lock(draft.id), gmail_client.http_client() as http:
        try:
            thread_id = await drafts.send(db, draft, http)
        except drafts.DraftError as error:
            raise _error(error) from error
        except drafts.SendPending as error:
            log.warning("draft %s: reply unconfirmed (%s); the worker resumes it", draft.id, error)
            response.status_code = 202
            return SendOut(status="pending", thread_id=None)
    return SendOut(status="sent", thread_id=thread_id)
