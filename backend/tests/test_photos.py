from typing import Any
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest
from conftest import run
from fake_gmail import FakeGmail, make_raw
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_sync import _create_account, sync

from kanso.db.models import Account, ContactPhoto
from kanso.db.session import Session
from kanso.sync import photos

pytestmark = pytest.mark.usefixtures("db")

DANIEL = "https://lh3.googleusercontent.com/cm/daniel=s100"


def person(emails: list[str], *photo_list: dict[str, Any]) -> dict[str, Any]:
    return {"emailAddresses": [{"value": e} for e in emails], "photos": list(photo_list)}


class FakePeople:
    def __init__(self) -> None:
        self.other = [
            person(["Daniel@Example.com"], {"url": DANIEL}),
            person(["ana@example.com"], {"url": "https://lh3/ana-letter=s100", "default": True}),
            person(["sam@example.com"], {"url": "https://lh3/sam-old=s100"}),
        ]
        self.saved = [person(["sam@example.com"], {"url": "https://lh3/sam=s100"})]
        self.status = 200
        self.downloads: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "people.googleapis.com":
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"message": "denied"}})
            params = parse_qs(request.url.query.decode())
            if request.url.path == "/v1/otherContacts":
                assert params["sources"] == ["READ_SOURCE_TYPE_CONTACT", "READ_SOURCE_TYPE_PROFILE"]
                # Two pages, one person each.
                index = int(params.get("pageToken", ["0"])[0])
                page: dict[str, Any] = {"otherContacts": self.other[index : index + 1]}
                if index + 1 < len(self.other):
                    page["nextPageToken"] = str(index + 1)
                return httpx.Response(200, json=page)
            assert request.url.path == "/v1/people/me/connections"
            return httpx.Response(200, json={"connections": self.saved})
        self.downloads.append(str(request.url))
        return httpx.Response(
            200, content=str(request.url).encode(), headers={"content-type": "image/jpeg"}
        )


def sync_photos(people: FakePeople, account_id: int = 1) -> None:
    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(people.handle)) as http:
            await photos.sync(account_id, http)

    run(go())


def stored() -> dict[str, tuple[int, bytes]]:
    async def go() -> dict[str, tuple[int, bytes]]:
        async with Session() as db:
            rows = await db.scalars(select(ContactPhoto).order_by(ContactPhoto.email))
            return {p.email: (p.id, p.content) for p in rows}

    return run(go())


def test_sync_stores_photos() -> None:
    run(_create_account())
    people = FakePeople()
    sync_photos(people)
    # Lowercased, sized down, letter avatars skipped, and a saved contact's photo wins.
    assert {email: content for email, (_, content) in stored().items()} == {
        "daniel@example.com": b"https://lh3.googleusercontent.com/cm/daniel=s64-c",
        "sam@example.com": b"https://lh3/sam=s64-c",
    }

    async def synced_at() -> Any:
        async with Session() as db:
            return (await db.get(Account, 1)).photos_synced_at  # type: ignore[union-attr]

    assert run(synced_at()) is not None


def test_sync_keeps_unchanged_and_replaces_changed() -> None:
    run(_create_account())
    people = FakePeople()
    sync_photos(people)
    before = stored()
    people.downloads.clear()
    people.saved = [person(["sam@example.com"], {"url": "https://lh3/sam-new=s100"})]
    people.other = people.other[:1]
    sync_photos(people)
    after = stored()
    assert people.downloads == ["https://lh3/sam-new=s64-c"]  # Daniel's didn't change
    assert after["daniel@example.com"] == before["daniel@example.com"]
    assert after["sam@example.com"][0] != before["sam@example.com"][0]  # a new id: a new URL


def test_sync_without_contacts_access() -> None:
    run(_create_account())
    people = FakePeople()
    people.status = 403  # connected before kanso asked for contacts
    sync_photos(people)
    assert stored() == {}


def test_threads_show_the_senders_photo(logged_in: TestClient) -> None:
    gmail = FakeGmail()
    gmail.add("m1", "t1", ["INBOX"], make_raw("Lease renewal", "Contract attached"))
    gmail.add("m2", "t1", ["SENT"], make_raw("Re: Lease", "Thanks!", "Me <me@gmail.com>"))
    gmail.add("m3", "t2", ["INBOX"], make_raw("Hi", "Hello", "Ana <ana@example.com>"))
    run(_create_account())
    sync(gmail)
    sync_photos(FakePeople())
    threads = logged_in.get("/api/threads", params={"account_id": 1, "mailbox": "inbox"}).json()
    photo = {t["subject"]: t["sender_photo"] for t in threads["threads"]}
    # The newest message is the user's own: the photo is of Daniel, who wrote before. Ana has
    # only Google's letter avatar.
    daniel = f"/api/photos/{stored()['daniel@example.com'][0]}"
    assert photo == {"Lease renewal": daniel, "Hi": None}
    response = logged_in.get(daniel)
    assert response.content == b"https://lh3.googleusercontent.com/cm/daniel=s64-c"
    assert response.headers["content-type"] == "image/jpeg"
    assert logged_in.get("/api/photos/999").status_code == 404
