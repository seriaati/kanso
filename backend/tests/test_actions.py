import asyncio
import sys
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL, run
from fake_gmail import Crash, FakeGmail, make_raw
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso import demo_seed
from kanso.db import session
from kanso.db.models import Account, Label, Outbox, Thread
from kanso.db.session import Session
from kanso.gmail import client, oauth
from kanso.sync import engine, outbox

pytestmark = pytest.mark.usefixtures("db", "unpooled")


@pytest.fixture
def unpooled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some tests mix TestClient's event loop with their own; asyncpg connections can't cross."""
    maker = async_sessionmaker(
        create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool), expire_on_commit=False
    )
    for module in (session, outbox, engine, sys.modules[__name__]):
        monkeypatch.setattr(module, "Session", maker)


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    fake = FakeGmail()
    fake.add(
        "m1", "t1", ["INBOX", "UNREAD", "Label_1"], make_raw("Lease renewal", "Contract attached")
    )
    fake.add("m2", "t1", ["SENT"], make_raw("Re: Lease renewal", "Thanks!", "Me <me@gmail.com>"))
    fake.add("m3", "t2", ["INBOX", "STARRED"], make_raw("Flight to Lisbon", "Seats 14A"))
    fake.add("m4", "t3", ["Label_1"], make_raw("Q4 planning", "Hiring is open"))
    fake.add("m5", "t4", ["TRASH"], make_raw("Prize", "You won"))

    async def no_sleep(seconds: float) -> None:
        fake.sleeps.append(seconds)

    monkeypatch.setattr(client, "sleep", no_sleep)
    monkeypatch.setattr(client, "http_client", fake.client)  # label management in the API
    return fake


async def _create_account() -> int:
    async with Session() as db:
        account = Account(
            email="me@gmail.com", display_name="me", color="#2F4B6E", refresh_token=b"x"
        )
        db.add(account)
        await db.commit()
        oauth._access_tokens[account.id] = ("access-token", time.time() + 3600)
        return account.id


async def _sync(gmail: FakeGmail) -> None:
    async with gmail.client() as http:
        await engine.sync_account(1, http)


@pytest.fixture
def synced(gmail: FakeGmail, logged_in: TestClient) -> TestClient:
    run(_create_account())
    run(_sync(gmail))
    return logged_in


async def _threads() -> dict[str, Thread]:
    async with Session() as db:
        return {t.gmail_id: t for t in await db.scalars(select(Thread))}


def threads() -> dict[str, Thread]:
    return run(_threads())


async def _entries() -> list[Outbox]:
    async with Session() as db:
        return list(await db.scalars(select(Outbox).order_by(Outbox.id)))


def entries() -> list[Outbox]:
    return run(_entries())


def act(api: TestClient, action: str, *gmail_threads: str, label: str | None = None) -> list[int]:
    ids = [threads()[t].id for t in gmail_threads]
    response = api.post(
        "/api/threads/actions", json={"action": action, "thread_ids": ids, "label": label}
    )
    assert response.status_code == 200, response.text
    return response.json()["entries"]


async def _drain(gmail: FakeGmail) -> int:
    count = 0
    async with gmail.client() as http:
        while await outbox.process_one(http):
            count += 1
    return count


def drain(gmail: FakeGmail) -> int:
    return run(_drain(gmail))


def sync(gmail: FakeGmail) -> None:
    run(_sync(gmail))


def gmail_labels(gmail: FakeGmail, message: str) -> set[str]:
    return set(gmail.messages[message]["labelIds"])


# action, thread, label, thread check, outbox (messages, add, remove), Gmail request
CASES: list[tuple[str, str, str | None, Any, tuple[list[str], list[str], list[str]], str]] = [
    ("done", "t1", None, lambda t: not t.in_inbox, (["m1"], [], ["INBOX"]), "batchModify"),
    ("inbox", "t3", None, lambda t: t.in_inbox, (["m4"], ["INBOX"], []), "batchModify"),
    ("delete", "t2", None, lambda t: t.in_trash, (["m3"], ["TRASH"], []), "m3/trash"),
    ("untrash", "t4", None, lambda t: not t.in_trash, (["m5"], [], ["TRASH"]), "m5/untrash"),
    # Stars and "unread" go on the latest message only, as in Gmail.
    ("star", "t1", None, lambda t: t.starred, (["m2"], ["STARRED"], []), "batchModify"),
    ("unstar", "t2", None, lambda t: not t.starred, (["m3"], [], ["STARRED"]), "batchModify"),
    (
        "important",
        "t1",
        None,
        lambda t: t.important,
        (["m1", "m2"], ["IMPORTANT"], []),
        "batchModify",
    ),
    ("unimportant", "t2", None, lambda t: not t.important, ([], [], []), ""),  # already so
    ("read", "t1", None, lambda t: not t.unread, (["m1"], [], ["UNREAD"]), "batchModify"),
    ("unread", "t2", None, lambda t: t.unread, (["m3"], ["UNREAD"], []), "batchModify"),
    ("label", "t2", "Work", lambda t: len(t.label_ids) == 3, (["m3"], ["Label_1"], []), "batch"),
    ("unlabel", "t1", "Work", lambda t: len(t.label_ids) == 3, (["m1"], [], ["Label_1"]), "batch"),
]


@pytest.mark.parametrize(("action", "thread", "label", "check", "queued", "call"), CASES)
def test_action_applies_locally_queues_and_sends(
    synced: TestClient,
    gmail: FakeGmail,
    action: str,
    thread: str,
    label: str | None,
    check: Any,
    queued: tuple[list[str], list[str], list[str]],
    call: str,
) -> None:
    ids = act(synced, action, thread, label=label)
    assert check(threads()[thread])  # the mirror changed at once
    pending = entries()
    if not queued[0]:
        assert ids == [] and pending == []
        return
    [entry] = pending
    assert (entry.id, entry.action, entry.status) == (ids[0], action, "pending")
    assert (entry.message_ids, entry.add_labels, entry.remove_labels) == queued
    # Gmail is untouched until the worker sends the entry.
    assert gmail.writes == []

    assert drain(gmail) == 1
    [entry] = entries()
    assert (entry.status, entry.attempts) == ("sent", 1)
    assert all(call in key for key, _ in gmail.writes)
    messages, add, remove = queued
    for message in messages:
        assert set(add) <= gmail_labels(gmail, message)
        assert not set(remove) & gmail_labels(gmail, message)
    # The sync that follows (Gmail's history of the change) agrees with the mirror.
    sync(gmail)
    assert check(threads()[thread])


def test_actions_publish_and_wake_the_worker(synced: TestClient, fake_redis: Any) -> None:
    async def listen() -> list[tuple[bytes, bytes]]:
        pubsub = fake_redis.pubsub()
        await pubsub.subscribe("kanso:events", "kanso:outbox")
        await asyncio.to_thread(act, synced, "done", "t1")
        got = []
        for _ in range(10):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message:
                got.append((message["channel"], message["data"]))
        return got

    got = run(listen())
    assert (b"kanso:outbox", b"1") in got
    assert any(channel == b"kanso:events" and b"threads_changed" in data for channel, data in got)


def test_unknown_label_and_thread(synced: TestClient) -> None:
    t1 = threads()["t1"].id
    body = {"action": "label", "thread_ids": [t1], "label": "Nope"}
    assert synced.post("/api/threads/actions", json=body).status_code == 400
    body = {"action": "done", "thread_ids": [9999]}
    assert synced.post("/api/threads/actions", json=body).status_code == 404
    assert entries() == []


def test_bulk_across_accounts_including_demo(synced: TestClient, gmail: FakeGmail) -> None:
    run(demo_seed.seed())
    demo_inbox = [
        t["id"]
        for t in synced.get("/api/threads", params={"mailbox": "inbox", "limit": 200}).json()[
            "threads"
        ]
        if t["account_id"] == 2
    ]
    ids = demo_inbox + [threads()["t1"].id, threads()["t2"].id]
    response = synced.post("/api/threads/actions", json={"action": "done", "thread_ids": ids})
    assert response.status_code == 200
    real, demo = sorted(entries(), key=lambda e: e.account_id)
    assert (real.account_id, sorted(real.message_ids)) == (1, ["m1", "m3"])
    assert demo.account_id == 2 and len(demo.message_ids) >= len(demo_inbox)
    inbox = synced.get("/api/threads", params={"mailbox": "inbox"}).json()["threads"]
    assert inbox == []

    assert drain(gmail) == 2
    assert [e.status for e in entries()] == ["sent", "sent"]
    # One batchModify for the real account; nothing goes to Gmail for the demo one.
    assert [key for key, _ in gmail.writes] == ["POST /messages/batchModify"]
    assert sorted(gmail.writes[0][1]["ids"]) == ["m1", "m3"]


def test_retries_with_backoff_then_sends(synced: TestClient, gmail: FakeGmail) -> None:
    act(synced, "done", "t1")
    act(synced, "star", "t1")
    # Gmail is down beyond the client's own retries (7 attempts).
    gmail.failures["POST /messages/batchModify"] = [503] * 7
    assert drain(gmail) == 1
    first, second = entries()
    assert (first.status, first.attempts) == ("pending", 1)
    assert "503" in (first.last_error or "")
    assert first.next_attempt_at > datetime.now(UTC)
    # Later entries of the account wait behind it, so they can't overtake it.
    assert second.attempts == 0

    run(_make_due())
    assert drain(gmail) == 2
    assert [(e.status, e.attempts) for e in entries()] == [("sent", 2), ("sent", 1)]
    assert "INBOX" not in gmail_labels(gmail, "m1")
    assert "STARRED" in gmail_labels(gmail, "m2")


async def _make_due() -> None:
    async with Session() as db:
        await db.execute(update(Outbox).values(next_attempt_at=datetime.now(UTC)))
        await db.commit()


def test_gives_up_after_max_attempts_and_restores_gmail_state(
    synced: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    monkeypatch.setattr(outbox, "MAX_ATTEMPTS", 2)
    act(synced, "done", "t1")
    gmail.failures["POST /messages/batchModify"] = [503] * 14
    drain(gmail)
    run(_make_due())
    drain(gmail)
    [entry] = entries()
    assert (entry.status, entry.attempts) == ("failed", 2)
    # Gmail never got it, so the thread is back in the inbox rather than silently diverging.
    assert threads()["t1"].in_inbox
    run(_make_due())
    assert drain(gmail) == 0  # a failed entry is never sent again


def test_permanent_error_fails_at_once(synced: TestClient, gmail: FakeGmail) -> None:
    act(synced, "done", "t2")
    gmail.failures["POST /messages/batchModify"] = [400]
    drain(gmail)
    [entry] = entries()
    assert (entry.status, entry.attempts) == ("failed", 1)
    assert threads()["t2"].in_inbox


def test_crash_after_gmail_applied_is_resent_once_without_duplicating(
    synced: TestClient, gmail: FakeGmail
) -> None:
    act(synced, "done", "t1")
    gmail.crash_after.add("POST /messages/batchModify")
    with pytest.raises(Crash):
        drain(gmail)
    # Gmail has the change, but the worker died before recording it: still pending.
    assert "INBOX" not in gmail_labels(gmail, "m1")
    [entry] = entries()
    assert (entry.status, entry.attempts) == ("pending", 0)
    history = len(gmail.history)

    assert drain(gmail) == 1  # the restarted worker
    [entry] = entries()
    assert (entry.status, entry.attempts) == ("sent", 1)
    assert gmail_labels(gmail, "m1") == {"UNREAD", "Label_1"}
    assert len(gmail.history) == history  # the repeat changed nothing in Gmail
    assert drain(gmail) == 0


def test_crash_before_gmail_call_loses_nothing(synced: TestClient, gmail: FakeGmail) -> None:
    act(synced, "delete", "t2")
    gmail.crash_before.add("POST /messages/m3/trash")
    with pytest.raises(Crash):
        drain(gmail)
    assert "TRASH" not in gmail_labels(gmail, "m3")
    assert entries()[0].status == "pending"
    assert drain(gmail) == 1
    assert "TRASH" in gmail_labels(gmail, "m3")
    assert [key for key, _ in gmail.writes].count("POST /messages/m3/trash") == 1


def test_an_entry_being_sent_is_not_claimed_twice(synced: TestClient, gmail: FakeGmail) -> None:
    act(synced, "done", "t1")

    async def concurrently() -> tuple[bool, bool]:
        async with Session() as db, gmail.client() as http:
            # Another worker holds the entry (FOR UPDATE), as while it waits for Gmail.
            await db.execute(select(Outbox).with_for_update())
            busy = await outbox.process_one(http)
            await db.rollback()
            free = await outbox.process_one(http)
            return busy, free

    assert run(concurrently()) == (False, True)


def test_worker_loop_wakes_on_new_entries(
    synced: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outbox, "FALLBACK_SECONDS", 60)

    async def scenario() -> list[str]:
        async with gmail.client() as http:
            task = asyncio.create_task(outbox.run(http))
            await asyncio.sleep(0.2)  # idle, subscribed
            await asyncio.to_thread(act, synced, "done", "t1")
            async with asyncio.timeout(3):
                while not (await _entries())[0].status == "sent":  # noqa: ASYNC110
                    await asyncio.sleep(0.05)
            task.cancel()
            return [e.status for e in await _entries()]

    assert run(scenario()) == ["sent"]


def test_history_replay_does_not_bounce_a_pending_change(
    synced: TestClient, gmail: FakeGmail
) -> None:
    # Gmail's history has changes kanso hasn't synced: m1 left the inbox and came back.
    gmail.relabel("m1", remove=("INBOX",))
    gmail.relabel("m1", add=("INBOX",))
    act(synced, "done", "t1")
    act(synced, "star", "t3")
    sync(gmail)  # replays INBOX being added to m1
    assert not threads()["t1"].in_inbox
    assert threads()["t3"].starred

    drain(gmail)
    sync(gmail)
    assert not threads()["t1"].in_inbox and threads()["t3"].starred
    # A new message in the thread brings it back, as it should.
    gmail.add("m6", "t1", ["INBOX", "UNREAD"], make_raw("Re: Lease renewal", "One more thing"))
    sync(gmail)
    assert threads()["t1"].in_inbox


def test_full_resync_does_not_bounce_a_pending_change(synced: TestClient, gmail: FakeGmail) -> None:
    act(synced, "delete", "t2")
    act(synced, "read", "t1")
    gmail.oldest_history = gmail.history_id + 1  # history expired: full resync with Gmail's labels
    sync(gmail)
    assert threads()["t2"].in_trash
    assert not threads()["t1"].unread
    assert "GET /messages/m3 minimal" in gmail.requests  # it did refresh the labels


def test_undo_before_send_cancels_the_entry(synced: TestClient, gmail: FakeGmail) -> None:
    ids = act(synced, "done", "t1")
    response = synced.post("/api/threads/undo", json={"entries": ids})
    assert response.status_code == 200
    assert response.json() == {"thread_ids": [threads()["t1"].id]}
    assert threads()["t1"].in_inbox
    assert entries() == []
    assert drain(gmail) == 0 and gmail.writes == []


def test_undo_after_send_queues_the_inverse(synced: TestClient, gmail: FakeGmail) -> None:
    ids = act(synced, "delete", "t2")
    drain(gmail)
    assert "TRASH" in gmail_labels(gmail, "m3")
    synced.post("/api/threads/undo", json={"entries": ids})
    assert not threads()["t2"].in_trash
    original, inverse = entries()
    assert original.status == "sent"
    assert (inverse.action, inverse.add_labels, inverse.remove_labels) == ("undo", [], ["TRASH"])

    drain(gmail)
    assert "TRASH" not in gmail_labels(gmail, "m3")
    sync(gmail)
    assert not threads()["t2"].in_trash and threads()["t2"].in_inbox


def test_undo_while_sending_queues_the_inverse(synced: TestClient, gmail: FakeGmail) -> None:
    ids = act(synced, "done", "t1")

    async def undo_mid_send() -> None:
        async with Session() as db:
            await db.execute(select(Outbox).with_for_update())  # the processor holds it
            await asyncio.to_thread(synced.post, "/api/threads/undo", json={"entries": ids})
            await db.rollback()

    run(undo_mid_send())
    assert threads()["t1"].in_inbox
    assert [e.action for e in entries()] == ["done", "undo"]
    drain(gmail)
    assert "INBOX" in gmail_labels(gmail, "m1")  # done, then undone, in order


def test_pending_undo_is_not_bounced_by_sync(synced: TestClient, gmail: FakeGmail) -> None:
    ids = act(synced, "done", "t1")
    drain(gmail)
    sync(gmail)
    synced.post("/api/threads/undo", json={"entries": ids})
    gmail.oldest_history = gmail.history_id + 1
    sync(gmail)  # a full resync: Gmail still says archived
    assert threads()["t1"].in_inbox


def test_drafts_only_threads_are_not_in_done(logged_in: TestClient) -> None:
    run(demo_seed.seed())
    done = logged_in.get("/api/threads", params={"mailbox": "done", "limit": 200}).json()
    assert "Notes on the contract" not in [t["subject"] for t in done["threads"]]
    drafts = logged_in.get("/api/threads", params={"mailbox": "drafts"}).json()["threads"]
    assert [t["subject"] for t in drafts] == ["Notes on the contract"]


async def _labels() -> dict[str, tuple[str, str | None]]:
    async with Session() as db:
        return {
            label.gmail_id: (label.name, label.color)
            for label in await db.scalars(select(Label).where(Label.type == "user"))
        }


def test_label_create_rename_recolor_delete(synced: TestClient, gmail: FakeGmail) -> None:
    response = synced.post("/api/labels", json={"name": "Travel", "color": "#556B3F"})
    assert response.status_code == 201, response.text
    [label] = response.json()
    gmail_label = next(x for x in gmail.labels if x["name"] == "Travel")
    # Gmail only takes its own palette: the nearest Gmail color goes out and is mirrored.
    assert gmail_label["color"] == {"backgroundColor": "#1a764d", "textColor": "#ffffff"}
    assert run(_labels())[gmail_label["id"]] == ("Travel", "#1a764d")

    same = synced.post("/api/labels", json={"name": "travel"})
    assert same.status_code == 409
    assert synced.post("/api/labels", json={"name": "inbox"}).status_code == 409

    act(synced, "label", "t2", label="Travel")
    drain(gmail)
    assert gmail_label["id"] in gmail_labels(gmail, "m3")

    trips = {"name": "Trips", "color": "#A35A3A"}
    response = synced.patch(f"/api/labels/{label['id']}", json=trips)
    assert response.status_code == 200
    assert gmail_label["name"] == "Trips"
    assert gmail_label["color"]["backgroundColor"] == "#cc3a21"
    assert run(_labels())[gmail_label["id"]] == ("Trips", "#cc3a21")
    sync(gmail)
    assert run(_labels())[gmail_label["id"]] == ("Trips", "#cc3a21")

    assert synced.delete(f"/api/labels/{label['id']}").status_code == 204
    assert gmail_label not in gmail.labels
    assert gmail_label["id"] not in run(_labels())
    assert len(threads()["t2"].label_ids) == 2  # INBOX, STARRED
    inbox_id = next(x["id"] for x in synced.get("/api/labels").json() if x["gmail_id"] == "INBOX")
    assert synced.patch(f"/api/labels/{inbox_id}", json={"name": "x"}).status_code == 400


def test_label_management_on_demo_is_local(synced: TestClient, gmail: FakeGmail) -> None:
    run(demo_seed.seed())
    response = synced.post("/api/labels", json={"name": "Garden", "account_ids": [2]})
    assert response.status_code == 201
    [label] = response.json()
    assert label["account_id"] == 2 and label["color"] is None
    assert synced.patch(f"/api/labels/{label['id']}", json={"color": "#A07338"}).status_code == 200
    assert synced.delete(f"/api/labels/{label['id']}").status_code == 204
    assert gmail.writes == []


def test_label_created_in_every_account(synced: TestClient, gmail: FakeGmail) -> None:
    run(demo_seed.seed())
    response = synced.post("/api/labels", json={"name": "Garden", "color": "#B8B3A2"})
    assert sorted(x["account_id"] for x in response.json()) == [1, 2]
    assert gmail.writes[0][1]["color"] == {"backgroundColor": "#c2c2c2", "textColor": "#000000"}
