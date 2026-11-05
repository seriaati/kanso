import asyncio
import base64
import json
import time
from typing import Any

import pytest
from conftest import run
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_gmail import FakeGmail, make_raw
from sqlalchemy import func, select, update

from kanso.db.models import Account, Attachment, Label, Message, Thread
from kanso.db.session import Session
from kanso.gmail import client, oauth
from kanso.sync import engine, push, scheduler

pytestmark = pytest.mark.usefixtures("db")


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
    return fake


async def _create_account(email: str = "me@gmail.com") -> int:
    async with Session() as db:
        account = Account(email=email, display_name="me", color="#2F4B6E", refresh_token=b"x")
        db.add(account)
        await db.commit()
        oauth._access_tokens[account.id] = ("access-token", time.time() + 3600)
        return account.id


async def _sync(gmail: FakeGmail, account_id: int) -> None:
    async with gmail.client() as http:
        await engine.sync_account(account_id, http)


def sync(gmail: FakeGmail, account_id: int = 1) -> None:
    run(_sync(gmail, account_id))


async def _state() -> dict[str, Any]:
    async with Session() as db:
        account = await db.get(Account, 1)
        threads = {
            t.gmail_id: t for t in await db.scalars(select(Thread).order_by(Thread.gmail_id))
        }
        rows = await db.execute(
            select(Message.gmail_id, Message.subject).order_by(Message.gmail_id)
        )
        labels = {label.id: label.gmail_id for label in await db.scalars(select(Label))}
        return {
            "account": account,
            "threads": threads,
            "messages": dict(rows.all()),
            "labels": labels,
        }


def state() -> dict[str, Any]:
    return run(_state())


def thread_labels(s: dict[str, Any], gmail_thread: str) -> set[str]:
    return {s["labels"][i] for i in s["threads"][gmail_thread].label_ids}


def test_backfill_mirrors_the_whole_mailbox(gmail: FakeGmail, fake_redis: Any) -> None:
    gmail.page_size = 2
    run(_create_account())
    sync(gmail)
    s = state()
    assert set(s["messages"]) == {"m1", "m2", "m3", "m4", "m5"}
    assert s["account"].sync_state == "ready"
    assert s["account"].backfill_done == 5
    assert s["account"].history_id == gmail.history_id
    t1 = s["threads"]["t1"]
    assert (t1.message_count, t1.subject, t1.snippet) == (2, "Lease renewal", "Thanks!")
    assert t1.senders == ["Daniel", "Me"]
    assert (t1.in_inbox, t1.unread, t1.starred) == (True, True, False)
    assert thread_labels(s, "t1") == {"INBOX", "UNREAD", "Label_1", "SENT"}
    assert s["threads"]["t2"].starred and not s["threads"]["t2"].unread
    assert not s["threads"]["t3"].in_inbox
    assert s["threads"]["t4"].in_trash
    labels = run(_labels())
    assert labels["Label_1"] == ("Work", "user", "#6b6b66")
    assert labels["INBOX"] == ("INBOX", "system", None)


async def _list_ids() -> dict[str, str | None]:
    async with Session() as db:
        return dict((await db.execute(select(Message.gmail_id, Message.list_id))).all())


def test_list_id_is_kept_and_fetched_for_older_mail(gmail: FakeGmail) -> None:
    raw = make_raw("Weekly digest", "News", headers={"List-Id": "Digest <digest.example.com>"})
    gmail.add("m6", "t5", ["INBOX"], raw)
    run(_create_account())
    sync(gmail)
    ids = run(_list_ids())
    assert ids["m6"] == "Digest <digest.example.com>"
    assert ids["m1"] == ""  # none
    assert not any(r.endswith(" metadata") for r in gmail.requests)

    # Mirrored before Kanso kept List-Id: the next sync fetches the headers.
    run(_forget_list_ids())
    sync(gmail)
    assert run(_list_ids()) == ids
    assert gmail.requests.count("GET /messages/m6 metadata") == 1


async def _forget_list_ids() -> None:
    async with Session() as db:
        await db.execute(update(Message).values(list_id=None))
        await db.commit()


async def _labels() -> dict[str, tuple[str, str, str | None]]:
    async with Session() as db:
        return {
            label.gmail_id: (label.name, label.type, label.color)
            for label in await db.scalars(select(Label))
        }


def test_backfill_resumes_after_a_failure(gmail: FakeGmail) -> None:
    gmail.page_size = 2
    run(_create_account())
    # Page 2 (m3, m2: newest first is m5 m4 | m3 m2 | m1) fails hard.
    gmail.failures["GET /messages/m3 raw"] = [400]
    with pytest.raises(client.GmailError):
        sync(gmail)
    s = state()
    assert set(s["messages"]) == {"m5", "m4"}  # page 1 was committed
    assert s["account"].sync_state == "backfill"
    assert (s["account"].backfill_done, s["account"].backfill_page_token) == (2, "2")

    sync(gmail)  # the restart picks up at page 2
    s = state()
    assert set(s["messages"]) == {"m1", "m2", "m3", "m4", "m5"}
    assert s["account"].backfill_done == 5 and s["account"].sync_state == "ready"
    assert gmail.raw_fetches("m5") == 1 and gmail.raw_fetches("m4") == 1
    assert gmail.requests.count("GET /profile") == 1  # not started over


def test_backfill_keeps_chunks_saved_before_a_failure(
    gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "BACKFILL_CHUNK", 2)
    run(_create_account())
    # One page, newest first: m5 m4 | m3 m2 | m1. The second chunk fails hard.
    gmail.failures["GET /messages/m3 raw"] = [400]
    with pytest.raises(client.GmailError):
        sync(gmail)
    assert set(state()["messages"]) == {"m5", "m4"}  # the first chunk was committed

    sync(gmail)
    s = state()
    assert set(s["messages"]) == {"m1", "m2", "m3", "m4", "m5"}
    assert s["account"].sync_state == "ready" and s["account"].backfill_done == 5
    assert gmail.raw_fetches("m5") == 1 and gmail.raw_fetches("m4") == 1


def test_pacer_spaces_requests_and_holds_them_after_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(client, "pace_sleep", record)

    async def go() -> None:
        pacer = client.Pacer()
        for _ in range(3):
            await pacer.wait(9)
        pacer.pause(5)
        await pacer.wait(9)

    run(go())
    # The first request goes at once; the next two are spaced by their cost (9 units at 90 a
    # second = 0.1 s each); after the pause, about 5 s.
    assert len(waits) == 3
    assert waits[0] == pytest.approx(0.1, abs=0.01)
    assert waits[1] == pytest.approx(0.2, abs=0.01)
    assert waits[2] == pytest.approx(5, abs=0.05)


def test_quota_cost_of_each_call() -> None:
    assert client.cost("GET", "/messages/abc", {"format": "raw"}) == 60
    assert client.cost("GET", "/messages/abc", {"format": "minimal"}) == 20
    assert client.cost("GET", "/messages", {"maxResults": 500}) == 5
    assert client.cost("GET", "/history", {}) == 2
    assert client.cost("POST", "/messages/batchModify") == 50
    assert client.cost("POST", "/messages/abc/trash") == 20
    assert client.cost("PUT", "/drafts/r123") == 15
    assert client.cost("POST", "/drafts/send") == 100
    assert client.cost("POST", "/something/new") == client.DEFAULT_COST


def test_changes_during_backfill_are_replayed_from_history(gmail: FakeGmail) -> None:
    run(_create_account())
    gmail.page_size = 2
    gmail.failures["GET /messages/m3 raw"] = [400]
    with pytest.raises(client.GmailError):
        sync(gmail)
    # While Kanso was down: m5 (already mirrored) is read, m6 arrives.
    gmail.relabel("m5", add=("STARRED",))
    gmail.add("m6", "t5", ["INBOX", "UNREAD"], make_raw("New", "Hello"))
    sync(gmail)
    s = state()
    assert "m6" in s["messages"]
    assert s["threads"]["t4"].starred


def test_retries_rate_limits_and_server_errors(gmail: FakeGmail) -> None:
    run(_create_account())
    gmail.failures["GET /messages"] = [429, 503]
    gmail.failures["GET /messages/m1 raw"] = [500]
    sync(gmail)
    assert len(state()["messages"]) == 5
    assert len(gmail.sleeps) == 3


def test_history_adds_deletes_and_label_changes(gmail: FakeGmail, fake_redis: Any) -> None:
    run(_create_account())
    sync(gmail)
    gmail.add("m6", "t1", ["INBOX", "UNREAD"], make_raw("Re: Lease renewal", "Signed copy"))
    gmail.relabel("m1", remove=("UNREAD", "INBOX"))  # read and archived (Done)
    gmail.relabel("m3", add=("IMPORTANT",), remove=("STARRED",))
    gmail.relabel("m6", remove=("UNREAD",))  # added and changed in the same batch
    gmail.delete("m4")  # t3's only message
    gmail.relabel("m5", add=("INBOX",), remove=("TRASH",))  # restored from Trash
    sync(gmail)
    s = state()
    assert set(s["messages"]) == {"m1", "m2", "m3", "m5", "m6"}
    assert "t3" not in s["threads"]
    t1 = s["threads"]["t1"]
    assert (t1.message_count, t1.snippet, t1.unread, t1.in_inbox) == (3, "Signed copy", False, True)
    assert thread_labels(s, "t2") == {"INBOX", "IMPORTANT"}
    assert s["threads"]["t2"].important and not s["threads"]["t2"].starred
    assert s["threads"]["t4"].in_inbox and not s["threads"]["t4"].in_trash
    assert gmail.raw_fetches("m6") == 1
    assert s["account"].history_id == gmail.history_id


def test_history_label_change_for_unknown_message_fetches_it(gmail: FakeGmail) -> None:
    run(_create_account())
    sync(gmail)
    gmail.add("m7", "t7", ["INBOX"], make_raw("Missed", "Somehow"), record=False)
    gmail.relabel("m7", add=("STARRED",))
    sync(gmail)
    assert state()["threads"]["t7"].starred


def test_expired_history_falls_back_to_a_full_resync(gmail: FakeGmail) -> None:
    run(_create_account())
    sync(gmail)
    # Changes Gmail no longer has history for.
    gmail.delete("m4", record=False)
    gmail.relabel("m1", remove=("UNREAD",), record=False)
    gmail.add("m6", "t6", ["INBOX"], make_raw("While away", "..."), record=False)
    gmail.oldest_history = gmail.history_id + 1
    sync(gmail)
    s = state()
    assert set(s["messages"]) == {"m1", "m2", "m3", "m5", "m6"}
    assert "t3" not in s["threads"]
    assert not s["threads"]["t1"].unread
    assert s["account"].sync_state == "ready"
    assert gmail.raw_fetches("m1") == 1  # known messages only had their labels refreshed
    assert "GET /messages/m1 minimal" in gmail.requests


def test_label_sync_renames_and_removes(gmail: FakeGmail) -> None:
    run(_create_account())
    sync(gmail)
    gmail.labels = [label for label in gmail.labels if label["id"] != "Label_1"]
    gmail.labels.append({"id": "Label_2", "name": "Travel", "type": "user"})
    sync(gmail)
    labels = run(_labels())
    assert "Label_1" not in labels and labels["Label_2"][0] == "Travel"
    assert thread_labels(state(), "t1") == {"INBOX", "UNREAD", "SENT"}


def test_threads_changed_events_are_published(gmail: FakeGmail, fake_redis: Any) -> None:
    run(_create_account())

    async def collect() -> list[dict[str, Any]]:
        pubsub = fake_redis.pubsub()
        await pubsub.subscribe("kanso:events")
        async with gmail.client() as http:
            await engine.sync_account(1, http)
        events = []
        for _ in range(20):  # the subscribe confirmation comes back as None too
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message:
                events.append(json.loads(message["data"]))
        return events

    events = run(collect())
    changed = [e for e in events if e["type"] == "threads_changed"]
    assert changed and set(changed[0]["thread_ids"]) == {1, 2, 3, 4}
    assert {"type": "sync", "account_id": 1} in events


def test_attachments_are_metadata_only_and_downloadable(
    gmail: FakeGmail, logged_in: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg["Subject"] = "Contract"
    msg.set_content("See attached")
    msg.add_attachment(b"%PDF-1.4 contract", "application", "pdf", filename="contract.pdf")
    gmail.add("m9", "t9", ["INBOX"], msg.as_bytes())
    run(_create_account())
    sync(gmail)
    [attachment] = run(_attachments())
    assert (attachment.filename, attachment.size) == ("contract.pdf", 17)
    monkeypatch.setattr(client, "http_client", gmail.client)
    response = logged_in.get(f"/api/attachments/{attachment.id}")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 contract"
    # Inline and frameable by Kanso alone, for the reader's attachment viewer.
    assert response.headers["content-disposition"] == "inline; filename*=UTF-8''contract.pdf"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert logged_in.get("/api/attachments/999").status_code == 404


async def _attachments() -> list[Attachment]:
    async with Session() as db:
        return list(await db.scalars(select(Attachment)))


def _service_account() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "client_email": "kanso-pubsub@project.iam.gserviceaccount.com",
        "private_key": pem,
        "private_key_id": "key-1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return base64.b64encode(json.dumps(info).encode()).decode()


async def _push_round(gmail: FakeGmail, fail_sync: bool = False) -> list[str]:
    """Pull once, handle each notification against a running account loop."""
    async with gmail.client() as http:
        sched = scheduler.Scheduler(http, poll_seconds=3600)
        await sched.refresh()
        loop = sched.get(1)
        assert loop is not None
        async with asyncio.timeout(5):
            await loop.synced(loop.request())  # the start-up sync (the backfill)
        if fail_sync:
            gmail.failures["GET /history"] = [400] * 10
        pubsub = push.PubSub(http, _service_account(), "projects/p/subscriptions/kanso-gmail")
        received = await pubsub.pull()
        await asyncio.gather(*(push.handle(pubsub, sched, r) for r in received))
        sched.stop()
    return gmail.acked


@pytest.fixture
def fast_push(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(scheduler, "RETRY_MIN", 3600)
    monkeypatch.setattr(push, "SYNC_TIMEOUT", 0.5)


@pytest.mark.usefixtures("fast_push")
def test_push_notification_syncs_then_acks(gmail: FakeGmail) -> None:
    run(_create_account())
    gmail.add("m6", "t6", ["INBOX", "UNREAD"], make_raw("Pushed", "Instant"))
    ack_id = gmail.notify()
    unknown = gmail.notify("someone-else@gmail.com")
    acked = run(_push_round(gmail))
    assert sorted(acked) == sorted([ack_id, unknown])
    assert "m6" in state()["messages"]
    pull = next(r for r in gmail.requests if r.startswith("POST /v1/"))
    assert pull == "POST /v1/projects/p/subscriptions/kanso-gmail:pull"


@pytest.mark.usefixtures("fast_push")
def test_push_notification_is_not_acked_when_the_sync_fails(gmail: FakeGmail) -> None:
    run(_create_account())
    gmail.notify()
    assert run(_push_round(gmail, fail_sync=True)) == []
    assert run(_error()) is not None


async def _error() -> str | None:
    async with Session() as db:
        return await db.scalar(select(Account.sync_error))


def test_demo_accounts_are_never_synced(gmail: FakeGmail) -> None:
    async def demo() -> int:
        async with Session() as db:
            db.add(
                Account(
                    email="demo@example.com",
                    display_name="Demo",
                    color="#2F4B6E",
                    refresh_token=b"",
                    demo=True,
                )
            )
            await db.commit()
            return await db.scalar(select(func.count()).select_from(Account)) or 0

    assert run(demo()) == 1
    sync(gmail)
    assert gmail.requests == []


def test_refresh_asks_the_worker_to_sync_the_account(logged_in: Any, fake_redis: Any) -> None:
    requested: list[int] = []

    class Loop:
        def __init__(self, account_id: int) -> None:
            self.account_id = account_id

        def request(self) -> int:
            requested.append(self.account_id)
            return 1

    class Accounts:
        def get(self, account_id: int) -> Loop | None:
            return Loop(account_id) if account_id == 1 else None

    async def listen_while(publish: Any) -> None:
        listener = asyncio.create_task(scheduler.listen(Accounts()))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # subscribed
        await asyncio.to_thread(publish)
        for _ in range(40):
            if requested:
                break
            await asyncio.sleep(0.05)
        listener.cancel()

    def refresh() -> None:
        assert logged_in.post("/api/sync/2").status_code == 204  # not synced here: ignored
        assert logged_in.post("/api/sync/1").status_code == 204

    run(listen_while(refresh))
    assert requested == [1]
