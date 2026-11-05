"""Sender photos from each account's Google Contacts: saved contacts, and "other contacts" (people
the user has emailed). Synced once a day and stored, so the browser never asks Google for them."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx2 as httpx
from sqlalchemy import delete, func, or_, select

from kanso.db.models import Account, ContactPhoto
from kanso.db.session import Session
from kanso.gmail.client import Gmail, GmailError

log = logging.getLogger(__name__)

PEOPLE = "https://people.googleapis.com/v1"
EVERY = timedelta(days=1)
LOOP_SECONDS = 15 * 60
SIZE = 64  # px: sharp for the 20px avatar on a 3x screen
DOWNLOADS = 8  # at a time


async def _people(gmail: Gmail, path: str, key: str, params: dict[str, Any]) -> AsyncIterator[Any]:
    params = {**params, "pageSize": 1000}
    while True:
        page = await gmail.request("GET", path, base=PEOPLE, params=params)
        for person in page.get(key, []):
            yield person
        if "nextPageToken" not in page:
            return
        params["pageToken"] = page["nextPageToken"]


def _photo(person: dict[str, Any]) -> str | None:
    """Google's generated letter avatars are marked default: no photo."""
    photos = [p for p in person.get("photos", []) if not p.get("default") and p.get("url")]
    photos.sort(key=lambda p: not p.get("metadata", {}).get("primary"))
    return re.sub(r"=s\d+(-c)?$", f"=s{SIZE}-c", photos[0]["url"]) if photos else None


async def photo_urls(gmail: Gmail) -> dict[str, str]:
    """Email address (lowercase) -> photo URL. A saved contact's photo wins."""
    urls: dict[str, str] = {}
    other = {
        "readMask": "emailAddresses,photos",
        # PROFILE: the person's own Google photo, which "other contacts" rarely have otherwise.
        "sources": ["READ_SOURCE_TYPE_CONTACT", "READ_SOURCE_TYPE_PROFILE"],
    }
    saved = {"personFields": "emailAddresses,photos"}
    for path, key, params in [
        ("/otherContacts", "otherContacts", other),
        ("/people/me/connections", "connections", saved),
    ]:
        async for person in _people(gmail, path, key, params):
            if url := _photo(person):
                for address in person.get("emailAddresses", []):
                    if address.get("value"):
                        urls[address["value"].lower()] = url
    return urls


async def sync(account_id: int, http: httpx.AsyncClient) -> None:
    async with Session() as db:
        account = await db.get(Account, account_id)
        assert account is not None
        account.photos_synced_at = func.now()  # also after a failure: try again tomorrow
        await db.commit()
        try:
            urls = await photo_urls(Gmail(account, http))
        except GmailError as error:
            if error.status != 403:
                raise
            # Connected before Kanso asked for contacts, or the People API isn't enabled.
            log.info("account %s: no access to Google Contacts, no sender photos", account_id)
            return
        rows = await db.execute(
            select(ContactPhoto.email, ContactPhoto.source_url).where(
                ContactPhoto.account_id == account_id
            )
        )
        stored = {email: url for email, url in rows}
        gone = [email for email, url in stored.items() if urls.get(email) != url]
        await db.execute(
            delete(ContactPhoto).where(
                ContactPhoto.account_id == account_id, ContactPhoto.email.in_(gone)
            )
        )
        wanted = {email: url for email, url in urls.items() if stored.get(email) != url}
        images = await _download(http, set(wanted.values()))
        for email, url in wanted.items():
            if image := images.get(url):
                db.add(ContactPhoto(account_id=account_id, email=email, source_url=url, **image))
        await db.commit()
        log.info("account %s: %d sender photos", account_id, len(urls))


async def _download(http: httpx.AsyncClient, urls: set[str]) -> dict[str, dict[str, Any]]:
    slots = asyncio.Semaphore(DOWNLOADS)

    async def one(url: str) -> tuple[str, dict[str, Any] | None]:
        async with slots:
            try:
                response = await http.get(url)
            except httpx.HTTPError as error:
                log.info("could not download a contact photo: %s", error)
                return url, None
        mime_type = response.headers.get("content-type", "").split(";")[0]
        if response.is_error or not mime_type.startswith("image/"):
            return url, None
        return url, {"content": response.content, "mime_type": mime_type}

    return {url: image for url, image in await asyncio.gather(*map(one, urls)) if image}


async def run(http: httpx.AsyncClient) -> None:
    while True:
        try:
            async with Session() as db:
                due = list(
                    await db.scalars(
                        select(Account.id).where(
                            Account.demo.is_(False),
                            or_(
                                Account.photos_synced_at.is_(None),
                                Account.photos_synced_at < func.now() - EVERY,
                            ),
                        )
                    )
                )
            for account_id in due:
                try:
                    await sync(account_id, http)
                except Exception:
                    log.exception("account %s: sender photos sync failed", account_id)
        except Exception:
            log.exception("sender photos: could not load accounts")
        await asyncio.sleep(LOOP_SECONDS)
