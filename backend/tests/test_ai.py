import json
import sys
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from conftest import TEST_DATABASE_URL, run, set_key
from fake_gmail import Crash, FakeGmail, make_raw
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso.ai import keys, labeling, trim
from kanso.core.settings import settings
from kanso.db import session
from kanso.db.models import Account, AiQueue, AiUsage, BlockedSender, Label, Outbox, Thread
from kanso.db.session import Session
from kanso.gmail import client, oauth
from kanso.sync import engine, outbox

pytestmark = pytest.mark.usefixtures("db", "unpooled")


@pytest.fixture
def unpooled(monkeypatch: pytest.MonkeyPatch) -> None:
    maker = async_sessionmaker(
        create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool), expire_on_commit=False
    )
    for module in (session, outbox, engine, labeling, sys.modules[__name__]):
        monkeypatch.setattr(module, "Session", maker)


@pytest.fixture
def ai_on(db: None) -> None:
    set_key("anthropic", "test-key")


def _reply(subject: str, body: str, sender: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@gmail.com, partner@example.com"
    msg["Cc"] = "accountant@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(b"%PDF-1.4 secret", "application", "pdf", filename="statement-secret.pdf")
    return msg.as_bytes()


BILL = (
    "Hi,\n\nYour October bill is $64.20, due by Nov 15.\n\nThanks,\nHarbor\n-- \n"
    "Harbor Electric · 1 Main St · unsubscribe\n\n"
    "On Mon, Oct 3, 2026 at 9:00 AM Me <me@gmail.com> wrote:\n> my private earlier question\n"
)


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    fake = FakeGmail()
    fake.labels.append({"id": "Label_2", "name": "Finance", "type": "user"})
    fake.labels.append({"id": "Label_3", "name": "Newsletters", "type": "user"})
    fake.add(
        "m1",
        "t1",
        ["INBOX", "UNREAD"],
        _reply("Your bill for October", BILL, "Harbor Electric <billing@harbor.example.com>"),
    )
    fake.add("m2", "t2", ["INBOX", "Label_1"], make_raw("Q4 planning", "Hiring is open"))
    fake.add("m3", "t3", ["Label_2"], make_raw("September statement", "Archived already"))
    fake.add("m4", "t4", ["INBOX"], make_raw("Dinner?", "Saturday at 7?"))
    fake.add("m5", "t5", ["INBOX", "Label_3"], make_raw("Weekly brief", "News", "Brief <b@x.io>"))

    async def no_sleep(seconds: float) -> None:
        fake.sleeps.append(seconds)

    monkeypatch.setattr(client, "sleep", no_sleep)
    monkeypatch.setattr(client, "http_client", fake.client)
    return fake


class FakeClaude:
    """The Messages API behind an httpx2 MockTransport, driven through the real SDK client."""

    def __init__(self) -> None:
        self.answers: list[Any] = []  # dict answers, or an HTTP status to fail with
        self.requests: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        answer = self.answers.pop(0) if self.answers else {"label": "none", "confidence": 0.1}
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            error = {"type": "error", "error": {"type": "api_error", "message": "injected"}}
            return httpx.Response(answer, json=error)
        content = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": json.dumps(answer)},
        ]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": content,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 300,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    def client(self) -> anthropic.AsyncAnthropic:
        transport = httpx.MockTransport(self.handle)
        return anthropic.AsyncAnthropic(
            api_key="test-key", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
        )


@pytest.fixture
def claude() -> FakeClaude:
    return FakeClaude()


async def _create_account() -> int:
    async with Session() as db:
        account = Account(
            email="me@gmail.com", display_name="me", color="#2F4B6E", refresh_token=b"x"
        )
        db.add(account)
        await db.commit()
        oauth._access_tokens[account.id] = ("access-token", time.time() + 3600)
        return account.id


def sync(gmail: FakeGmail) -> None:
    async def go() -> None:
        async with gmail.client() as http:
            await engine.sync_account(1, http)

    run(go())


async def _describe(name: str, description: str, **flags: bool) -> None:
    async with Session() as db:
        await db.execute(
            update(Label)
            .where(Label.name == name)
            .values(description=description, **({"ai_managed": True} | flags))
        )
        await db.commit()


@pytest.fixture
def synced(gmail: FakeGmail, logged_in: TestClient) -> TestClient:
    run(_create_account())
    sync(gmail)
    run(_describe("Finance", "bills, statements and payment reminders"))
    run(_describe("Work", "work mail"))
    run(_describe("Newsletters", "", ai_managed=False, is_bundle=True))
    return logged_in


async def _threads() -> dict[str, Thread]:
    async with Session() as db:
        return {t.gmail_id: t for t in await db.scalars(select(Thread))}


def threads() -> dict[str, Thread]:
    return run(_threads())


async def _rows(model: Any) -> list[Any]:
    async with Session() as db:
        return list(await db.scalars(select(model)))


def rows(model: Any) -> list[Any]:
    return run(_rows(model))


def queued() -> dict[str, str]:
    ids = {t.id: g for g, t in threads().items()}
    return {ids[r.thread_id]: r.status for r in rows(AiQueue)}


def process(claude: FakeClaude) -> int:
    async def go() -> int:
        count = 0
        ai = claude.client()
        while await labeling.process_one(ai):
            count += 1
        return count

    return run(go())


def drain(gmail: FakeGmail) -> None:
    async def go() -> None:
        async with gmail.client() as http:
            while await outbox.process_one(http):
                pass

    run(go())


def new_mail(gmail: FakeGmail, gmail_id: str, thread: str, subject: str, body: str) -> None:
    gmail.add(gmail_id, thread, ["INBOX", "UNREAD"], make_raw(subject, body))
    sync(gmail)


def label_names(thread: Thread) -> set[str]:
    async def go() -> set[str]:
        async with Session() as db:
            return set(await db.scalars(select(Label.name).where(Label.id.in_(thread.label_ids))))

    return run(go())


def test_trim_drops_quotes_signature_and_caps_length() -> None:
    expected = "Hi,\n\nYour October bill is $64.20, due by Nov 15.\n\nThanks,\nHarbor"
    assert trim.trim(BILL, 1500) == expected
    assert trim.trim("回复内容\n\n在 2026年10月3日 王芳 写道：\n> 原文", 100) == "回复内容"
    long = trim.trim("word " * 1000, 100)
    assert len(long) <= 102 and long.endswith(" …")


def test_prompt_sends_only_sender_subject_and_trimmed_body(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Your bill for November", "x")
    gmail.add(
        "m7",
        "t7",
        ["INBOX"],
        _reply("Your bill for October", BILL, "Harbor Electric <billing@harbor.example.com>"),
    )
    sync(gmail)
    claude.answers = [{"label": "none", "confidence": 0.2}] * 2
    process(claude)
    request = claude.requests[-1]
    [message] = request["messages"]
    assert message["content"] == (
        "<email>\nFrom: Harbor Electric <billing@harbor.example.com>\n"
        "Subject: Your bill for October\n\n"
        "Hi,\n\nYour October bill is $64.20, due by Nov 15.\n\nThanks,\nHarbor\n</email>"
    )
    sent = json.dumps(request)
    private = ["partner@example.com", "accountant@example.com", "statement-secret", "PDF"]
    private += ["my private earlier question", "unsubscribe", "Q4 planning", "Hiring"]
    assert [p for p in private if p in sent] == []
    # The AI-managed labels and their descriptions, nothing else; a structured answer.
    system = request["system"][0]["text"]
    assert "- Finance: bills, statements and payment reminders\n- Work: work mail" in system
    assert "Newsletters" not in sent
    assert request["model"] == "claude-haiku-5-5"
    schema = request["output_config"]["format"]["schema"]
    assert schema["properties"]["label"]["enum"] == ["Finance", "Work", "none"]
    assert "cache_control" not in request["system"][0]  # a short prefix: nothing to cache


def test_long_label_list_is_cached_only_with_a_backlog() -> None:
    labels: list[tuple[str, str]] = [(f"Label {i}", "a long description " * 8) for i in range(20)]
    email = labeling.Email("A", "a@example.com", "Hi", "Body")
    assert (
        "cache_control"
        in labeling.request(labels, email, "claude-haiku-5-5", cache=True)["system"][0]
    )
    assert (
        "cache_control"
        not in labeling.request(labels, email, "claude-haiku-5-5", cache=False)["system"][0]
    )
    short = [("Finance", "bills")]
    assert (
        "cache_control"
        not in labeling.request(short, email, "claude-haiku-5-5", cache=True)["system"][0]
    )


def _response(content: list[dict[str, Any]], stop: str = "end_turn") -> Any:
    from anthropic.types import Message

    return Message.model_validate(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def test_parse_structured_output() -> None:
    names = ["Finance", "Work"]
    thinking = {"type": "thinking", "thinking": "", "signature": "s"}

    def text(value: Any) -> dict[str, Any]:
        return {"type": "text", "text": value if isinstance(value, str) else json.dumps(value)}

    answer = text({"label": "Finance", "confidence": 0.9})
    assert labeling.parse(_response([thinking, answer]), names) == ("Finance", 0.9)
    none = text({"label": "none", "confidence": 0.8})
    assert labeling.parse(_response([none]), names) == (None, 0.8)
    unknown = text({"label": "Travel", "confidence": 0.99})
    assert labeling.parse(_response([unknown]), names) == (None, 0.99)
    clamped = text({"label": "Work", "confidence": 3})
    assert labeling.parse(_response([clamped]), names) == ("Work", 1.0)
    assert labeling.parse(_response([], "refusal"), names) == (None, 0.0)
    for bad in (_response([thinking], "max_tokens"), _response([text("not json")])):
        with pytest.raises(labeling.AnswerError):
            labeling.parse(bad, names)


def test_new_inbox_mail_is_labeled_through_the_outbox(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    assert rows(AiQueue) == []  # the backfill queues nothing
    new_mail(gmail, "m6", "t6", "Your bill for November", "Due Dec 15")
    gmail.add("m7", "t7", ["SENT"], make_raw("Sent by me", "x", "Me <me@gmail.com>"))
    sync(gmail)
    assert queued() == {"t6": "pending"}  # inbox mail only, not sent mail

    claude.answers = [{"label": "Finance", "confidence": 0.92}]
    assert process(claude) == 1
    assert queued() == {"t6": "labeled"}
    thread = threads()["t6"]
    assert label_names(thread) == {"INBOX", "UNREAD", "Finance"}
    assert not thread.labels_manual
    [entry] = rows(Outbox)
    assert (entry.action, entry.message_ids, entry.add_labels) == ("label", ["m6"], ["Label_2"])
    drain(gmail)
    assert "Label_2" in gmail.messages["m6"]["labelIds"]  # a real Gmail label
    # Gmail's history of kanso's own change is not mistaken for a manual one.
    sync(gmail)
    assert not threads()["t6"].labels_manual


def test_low_confidence_stays_unlabeled(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Hello", "Maybe a bill?")
    claude.answers = [{"label": "Finance", "confidence": 0.5}]
    process(claude)
    [row] = rows(AiQueue)
    assert (row.status, row.label, row.confidence) == ("unlabeled", "Finance", 0.5)
    assert label_names(threads()["t6"]) == {"INBOX", "UNREAD"}
    assert rows(Outbox) == []


def test_usage_is_recorded_and_priced(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Bill", "x")
    new_mail(gmail, "m7", "t7", "Bill", "y")
    process(claude)
    [row] = rows(AiUsage)
    assert (row.feature, row.model, row.requests) == ("labeling", "claude-haiku-5-5", 2)
    assert (row.input_tokens, row.output_tokens) == (600, 40)
    usage = synced.get("/api/ai/usage").json()
    [feature] = usage["features"]
    assert feature["input_tokens"] == 600
    expected = (600 * 0.10 + 40 * 0.50) / 1_000_000
    assert usage["cost_usd"] == pytest.approx(expected) and usage["complete"]


def test_label_set_in_kanso_is_final(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    ids = threads()
    for action, thread in (("label", "t4"), ("unlabel", "t2")):
        response = synced.post(
            "/api/threads/actions",
            json={"action": action, "thread_ids": [ids[thread].id], "label": "Work"},
        )
        assert response.status_code == 200
    assert threads()["t4"].labels_manual and threads()["t2"].labels_manual
    assert not threads()["t1"].labels_manual
    drain(gmail)
    sync(gmail)
    # New mail in those threads is never queued; queued earlier, it is skipped.
    new_mail(gmail, "m6", "t2", "Re: Q4 planning", "More hiring news")
    assert queued() == {}
    run(_queue_directly("t2"))
    process(claude)
    assert queued() == {"t2": "skipped"} and claude.requests == []


async def _queue_directly(gmail_thread: str) -> None:
    async with Session() as db:
        thread = await db.scalar(select(Thread).where(Thread.gmail_id == gmail_thread))
        assert thread is not None
        db.add(AiQueue(account_id=thread.account_id, thread_id=thread.id))
        await db.commit()


def test_label_changed_in_gmail_is_final(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    gmail.relabel("m4", add=("Label_1",))  # labeled by hand in Gmail
    gmail.relabel("m2", remove=("Label_1",))  # unlabeled by hand in Gmail
    gmail.relabel("m1", remove=("UNREAD",))  # read: not a label decision
    sync(gmail)
    state = threads()
    assert state["t4"].labels_manual and state["t2"].labels_manual
    assert not state["t1"].labels_manual
    new_mail(gmail, "m6", "t2", "Re: Q4 planning", "More")
    assert queued() == {}


def test_thread_labeled_while_claude_thinks_is_left_alone(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Bill", "x")
    thread_id = threads()["t6"].id

    def label_meanwhile(request: httpx.Request) -> httpx.Response:
        response = synced.post(
            "/api/threads/actions",
            json={"action": "label", "thread_ids": [thread_id], "label": "Work"},
        )
        assert response.status_code == 200
        claude.handle = original
        return original(request)

    original = claude.handle
    claude.handle = label_meanwhile  # type: ignore[method-assign]
    claude.answers = [{"label": "Finance", "confidence": 0.95}]
    process(claude)
    assert queued() == {"t6": "skipped"}
    assert label_names(threads()["t6"]) == {"INBOX", "UNREAD", "Work"}


def test_worker_crash_mid_call_loses_nothing(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Bill", "x")
    claude.answers = [Crash("killed")]
    with pytest.raises(Crash):
        process(claude)
    assert queued() == {"t6": "pending"}  # rolled back with the worker
    claude.answers = [{"label": "Finance", "confidence": 0.9}]
    assert process(claude) == 1
    assert queued() == {"t6": "labeled"}
    assert len(rows(Outbox)) == 1


def test_transient_errors_retry_later_and_permanent_ones_fail(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    new_mail(gmail, "m6", "t6", "Bill", "x")
    claude.answers = [429]
    process(claude)
    [row] = rows(AiQueue)
    assert (row.status, row.attempts) == ("pending", 1)
    assert row.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)
    assert rows(AiUsage) == []

    run(_due_now())
    claude.answers = [400]
    process(claude)
    [row] = rows(AiQueue)
    assert (row.status, row.attempts) == ("failed", 2)
    assert row.last_error and "injected" in row.last_error


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_pauses_labeling_and_shows_a_notice(
    ai_on: None,
    synced: TestClient,
    gmail: FakeGmail,
    claude: FakeClaude,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    monkeypatch.setattr(labeling, "_paused_until", 0.0)
    assert synced.get("/api/ai/usage").json()["key_rejected"] is False
    new_mail(gmail, "m6", "t6", "Bill", "x")
    new_mail(gmail, "m7", "t7", "Bill", "y")
    claude.answers = [status]
    process(claude)
    # One call, then the queue waits: nothing failed, no attempt counted.
    assert len(claude.requests) == 1
    assert queued() == {"t6": "pending", "t7": "pending"}
    assert [r.attempts for r in rows(AiQueue)] == [0, 0]
    assert synced.get("/api/ai/usage").json()["key_rejected"] is True
    run(_due_now())
    assert process(claude) == 0 and len(claude.requests) == 1  # still paused

    monkeypatch.setattr(labeling, "_paused_until", 0.0)  # ten minutes later, the key works
    claude.answers = [{"label": "Finance", "confidence": 0.9}]
    assert process(claude) == 2
    assert queued() == {"t6": "labeled", "t7": "unlabeled"}
    assert synced.get("/api/ai/usage").json()["key_rejected"] is False


async def _due_now() -> None:
    async with Session() as db:
        await db.execute(update(AiQueue).values(next_attempt_at=datetime.now(UTC)))
        await db.commit()


def test_label_existing_inbox_covers_unlabeled_inbox_threads_only(
    ai_on: None, synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    # t1, t4: inbox, unlabeled. t2, t5: labeled. t3: archived. t4 then gets labeled by hand and
    # unlabeled again: final.
    ids = threads()
    for action in ("label", "unlabel"):
        synced.post(
            "/api/threads/actions",
            json={"action": action, "thread_ids": [ids["t4"].id], "label": "Work"},
        )
    assert synced.get("/api/ai/label-inbox?account_id=1").json() == {"eligible": 1, "run": None}
    started = synced.post("/api/ai/label-inbox", json={"account_id": 1}).json()
    assert started["run"]["total"] == 1 and started["run"]["pending"] == 1
    assert queued() == {"t1": "pending"}
    claude.answers = [{"label": "Finance", "confidence": 0.9}]
    process(claude)
    status = synced.get("/api/ai/label-inbox?account_id=1").json()
    assert status["eligible"] == 0
    assert status["run"] | {"id": 0} == {
        "id": 0,
        "total": 1,
        "pending": 0,
        "labeled": 1,
        "failed": 0,
        "error": None,
    }
    assert synced.get("/api/ai/label-inbox?account_id=2").json()["run"] is None


def test_demo_mail_is_never_sent_to_claude(
    ai_on: None, synced: TestClient, claude: FakeClaude
) -> None:
    from kanso import demo_seed

    run(demo_seed.seed())
    eligible = synced.get("/api/ai/label-inbox?account_id=2").json()["eligible"]
    assert eligible > 0
    synced.post("/api/ai/label-inbox", json={"account_id": 2})
    process(claude)
    assert claude.requests == []
    run_ = synced.get("/api/ai/label-inbox?account_id=2").json()["run"]
    assert run_["pending"] == 0 and run_["total"] == eligible


def test_a_demo_instance_sends_its_demo_mail_to_claude(
    ai_on: None, logged_in: TestClient, claude: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kanso import demo_seed

    monkeypatch.setattr(settings, "demo_mode", True)  # all mail is made up there
    run(demo_seed.seed())
    eligible = logged_in.get("/api/ai/label-inbox?account_id=1").json()["eligible"]
    assert eligible > 0
    logged_in.post("/api/ai/label-inbox", json={"account_id": 1})
    process(claude)
    assert len(claude.requests) == eligible


def test_ai_off_hides_and_runs_nothing(
    synced: TestClient, gmail: FakeGmail, claude: FakeClaude
) -> None:
    assert synced.get("/api/config").json() == {"ai": False, "labeling": False}
    new_mail(gmail, "m6", "t6", "Bill", "x")
    assert rows(AiQueue) == []
    assert synced.post("/api/ai/label-inbox", json={"account_id": 1}).status_code == 409
    assert process(claude) == 0 and claude.requests == []


def test_config_with_a_key(ai_on: None, logged_in: TestClient) -> None:
    assert logged_in.get("/api/config").json() == {"ai": True, "labeling": True}


def _grouped(api: TestClient, query: str = "") -> dict[str, Any]:
    response = api.get(f"/api/threads?account_id=1&mailbox=inbox&grouped=true{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_grouped_inbox_sections_and_bundles(ai_on: None, synced: TestClient) -> None:
    # t6: Work and Finance (sections by name: Finance); t7: Work and the bundle (Work wins).
    gmail_threads = {
        "t6": ["INBOX", "Label_1", "Label_2"],
        "t7": ["INBOX", "Label_3", "Label_1"],
    }
    run(_add_threads(gmail_threads))
    page = _grouped(synced)
    order = [(t["subject"], t["section"]) for t in page["threads"]]
    # Unlabeled first (t4, t1), then sections by their newest thread: Work (t7), Finance (t6).
    assert order == [
        ("Dinner?", None),
        ("Your bill for October", None),
        ("Extra t7", "Work"),
        ("Q4 planning", "Work"),
        ("Extra t6", "Finance"),
    ]
    assert [s["name"] for s in page["sections"] if not s["bundle"]] == [None, "Work", "Finance"]
    sections = {(s["name"], s["bundle"]): s for s in page["sections"]}
    assert {k: s["count"] for k, s in sections.items()} == {
        ("Work", False): 2,
        ("Finance", False): 1,
        (None, False): 2,
        ("Newsletters", True): 1,
    }
    assert sections[("Newsletters", True)]["senders"] == ["Brief"]
    bundle = synced.get("/api/threads?account_id=1&mailbox=inbox&bundle=Newsletters").json()
    assert [t["subject"] for t in bundle["threads"]] == ["Weekly brief"]

    # Cursor pagination walks the same order.
    first = _grouped(synced, "&limit=2")
    second = _grouped(synced, f"&limit=2&cursor={first['next_cursor']}")
    third = _grouped(synced, f"&limit=2&cursor={second['next_cursor']}")
    walked = [t["subject"] for p in (first, second, third) for t in p["threads"]]
    assert walked == [s for s, _ in order] and third["next_cursor"] is None
    assert second["sections"] is None


async def _add_threads(threads_: dict[str, list[str]]) -> None:
    from kanso.gmail import mime
    from kanso.sync import store

    async with Session() as db:
        labels = await store.label_map(db, 1)
        ids = set()
        for n, (gmail_thread, label_ids) in enumerate(threads_.items()):
            raw = make_raw(f"Extra {gmail_thread}", "x")
            meta = {
                "id": f"x{gmail_thread}",
                "threadId": gmail_thread,
                "labelIds": label_ids,
                "internalDate": str(1_800_000_000_000 + n * 60_000),
            }
            ids.add(await store.save_message(db, 1, meta, mime.parse(raw), labels))
        await store.refresh_threads(db, ids)
        await db.commit()


def test_without_ai_the_inbox_is_one_list_plus_bundles(synced: TestClient) -> None:
    page = _grouped(synced)
    assert {t["section"] for t in page["threads"]} == {None}
    assert [t["subject"] for t in page["threads"]] == [
        "Dinner?",
        "Q4 planning",
        "Your bill for October",
    ]
    assert {(s["name"], s["count"]) for s in page["sections"]} == {(None, 3), ("Newsletters", 1)}


def test_done_all_clears_a_section_seen_until_then_undo(ai_on: None, synced: TestClient) -> None:
    page = _grouped(synced)
    other = next(s for s in page["sections"] if s["name"] is None)
    until = other["newest_at"]
    run(_add_threads({"t9": ["INBOX"]}))  # arrives after the user looked: left alone
    response = synced.post(
        "/api/threads/actions",
        json={"action": "done", "section": {"name": None, "account_id": 1, "until": until}},
    )
    entries = response.json()["entries"]
    state = threads()
    assert not state["t1"].in_inbox and not state["t4"].in_inbox
    assert state["t9"].in_inbox and state["t2"].in_inbox  # newer, or another section
    synced.post("/api/threads/undo", json={"entries": entries})
    assert threads()["t1"].in_inbox and threads()["t4"].in_inbox
    bundle = {
        "name": "Newsletters",
        "bundle": True,
        "account_id": 1,
        "until": "2100-01-01T00:00:00Z",
    }
    synced.post("/api/threads/actions", json={"action": "done", "section": bundle})
    assert not threads()["t5"].in_inbox


def test_label_settings_are_local(synced: TestClient, gmail: FakeGmail) -> None:
    label = next(lb for lb in synced.get("/api/labels?account_id=1").json() if lb["name"] == "Work")
    assert (label["description"], label["ai_managed"], label["is_bundle"]) == (
        "work mail",
        True,
        False,
    )
    writes = len(gmail.writes)
    patch = {"description": "  the office  ", "ai_managed": False, "is_bundle": True}
    updated = synced.patch(f"/api/labels/{label['id']}", json=patch).json()
    assert (updated["description"], updated["ai_managed"], updated["is_bundle"]) == (
        "the office",
        False,
        True,
    )
    assert len(gmail.writes) == writes  # nothing for Gmail
    created = synced.post(
        "/api/labels",
        json={"name": "Receipts", "description": "orders", "is_bundle": True, "account_id": 1},
    ).json()
    assert created["is_bundle"] and created["description"] == "orders"


# The Clean up page. The fixture's inbox, oldest first: t1 (Harbor, unlabeled), t2 (Daniel,
# Work), t4 (Daniel, unlabeled), t5 (Brief, the Newsletters bundle). t3 is archived.
BETWEEN_T2_AND_T4 = "2023-11-14T22:15:00Z"


def test_archive_inbox_threads_older_than_a_date_then_undo(
    synced: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kanso.api import actions

    monkeypatch.setattr(actions, "CHUNK", 1)  # one outbox entry per thread
    count = synced.get(f"/api/cleanup/inbox?account_id=1&before={BETWEEN_T2_AND_T4}").json()
    assert count == {"threads": 2}
    scope = {"account_id": 1, "before": BETWEEN_T2_AND_T4}
    entries = synced.post("/api/threads/actions", json={"action": "done", "inbox": scope}).json()
    assert len(entries["entries"]) == 2
    inbox = {g for g, t in threads().items() if t.in_inbox}
    assert inbox == {"t4", "t5"}
    synced.post("/api/threads/undo", json=entries)
    assert {g for g, t in threads().items() if t.in_inbox} == {"t1", "t2", "t4", "t5"}


def test_senders_and_clearing_one(synced: TestClient) -> None:
    listed = synced.get("/api/cleanup/senders?account_id=1").json()
    ids = threads()
    # Only Daniel has two inbox threads; his newest is t4.
    assert listed["senders"] == [
        {"email": "daniel@example.com", "name": "Daniel", "threads": 2, "thread_id": ids["t4"].id}
    ]
    run(_add_threads({"t9": ["INBOX"]}))  # Daniel again, after the list was loaded: left alone
    scope = {"account_id": 1, "sender": "Daniel@Example.com", "before": listed["as_of"]}
    synced.post("/api/threads/actions", json={"action": "done", "inbox": scope})
    assert {g for g, t in threads().items() if t.in_inbox} == {"t1", "t5", "t9"}


def test_blocking_a_sender_blocks_only_that_sender(synced: TestClient) -> None:
    gmail_threads = {"t9": ["INBOX"]}
    run(_add_threads(gmail_threads))
    scope = {"account_id": 1, "sender": "daniel@example.com"}
    synced.post("/api/threads/actions", json={"action": "spam", "inbox": scope})
    state = threads()
    assert state["t4"].in_spam and state["t9"].in_spam and not state["t1"].in_spam
    assert [b.email for b in rows(BlockedSender)] == ["daniel@example.com"]


# TypeSafe's Jev: the labeling model with only a TypeSafe key, or when picked in Settings.


@pytest.fixture
def jev_on(db: None) -> None:
    set_key("jev", "jev-key")


class FakeJev:
    """POST /v1/systemone behind an httpx2 MockTransport."""

    def __init__(self) -> None:
        self.answers: list[Any] = []  # (choice, confidence), or an HTTP status to fail with
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else ("none", 0.9)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"detail": "injected"})
        criteria = json.loads(request.content)["questions"]["label"]["criteria"]
        choice, confidence = answer
        probabilities = {name: 1.0 if name == choice else 0.0 for name in criteria}
        label = {"type": "choice", "choice": choice, "confidence": confidence}
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {"label": label | {"probabilities": probabilities}},
                "usage": {"input_tokens": 120, "output_tokens": 12},
            },
        )

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


@pytest.fixture
def jev() -> FakeJev:
    return FakeJev()


def process_jev(jev: FakeJev) -> int:
    async def go() -> int:
        count = 0
        async with Session() as db:
            chosen = await labeling.chosen(db, await keys.load(db))
        assert chosen == ("jev-latest", "jev-key")  # the saved key, decrypted
        real = labeling.client("jev-latest", "jev-key")
        assert isinstance(real, httpx.AsyncClient)
        # The real client's headers (the API key), over the fake endpoint.
        transport = httpx.MockTransport(jev.handle)
        async with real, httpx.AsyncClient(transport=transport, headers=real.headers) as ai:
            while await labeling.process_one(ai):
                count += 1
        return count

    return run(go())


def test_jev_alone_turns_on_labeling_only(jev_on: None, logged_in: TestClient) -> None:
    assert logged_in.get("/api/config").json() == {"ai": False, "labeling": True}


def test_jev_request_sends_only_sender_subject_and_trimmed_body(
    jev_on: None,
    synced: TestClient,
    gmail: FakeGmail,
    jev: FakeJev,
) -> None:
    gmail.add(
        "m7",
        "t7",
        ["INBOX"],
        _reply("Your bill for October", BILL, "Harbor Electric <billing@harbor.example.com>"),
    )
    sync(gmail)
    process_jev(jev)
    [request] = jev.requests
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer jev-key"
    body = jev.body()
    assert body["model"] == "jev-latest"
    assert body["state"] == {
        "from": "Harbor Electric <billing@harbor.example.com>",
        "subject": "Your bill for October",
        "body": "Hi,\n\nYour October bill is $64.20, due by Nov 15.\n\nThanks,\nHarbor",
    }
    sent = json.dumps(body)
    private = ["partner@example.com", "accountant@example.com", "statement-secret", "PDF"]
    private += ["my private earlier question", "unsubscribe", "Q4 planning", "Hiring"]
    assert [p for p in private if p in sent] == []
    # "none" first, as Jev leans toward the first option; then the AI-managed labels only.
    question = body["questions"]["label"]
    assert question["type"] == "choice"
    assert list(question["criteria"].items()) == [
        ("none", "None of the other labels clearly fits"),
        ("Finance", "bills, statements and payment reminders"),
        ("Work", "work mail"),
    ]


def test_jev_parse() -> None:
    names = ["Finance", "Work"]

    def answer(choice: str, confidence: float) -> dict[str, Any]:
        label = {"type": "choice", "choice": choice, "confidence": confidence}
        return {"answers": {"label": label | {"probabilities": {}}}}

    assert labeling.jev_parse(answer("Finance", 0.9), names) == ("Finance", 0.9)
    assert labeling.jev_parse(answer("none", 0.8), names) == (None, 0.8)
    assert labeling.jev_parse(answer("Travel", 0.99), names) == (None, 0.99)
    assert labeling.jev_parse(answer("Work", 3), names) == ("Work", 1.0)
    for bad in ({}, {"answers": {}}, {"answers": {"label": {"type": "noul", "noul": 1}}}, []):
        with pytest.raises(labeling.AnswerError):
            labeling.jev_parse(bad, names)


def test_jev_labels_new_mail_through_the_outbox(
    jev_on: None,
    synced: TestClient,
    gmail: FakeGmail,
    jev: FakeJev,
) -> None:
    new_mail(gmail, "m6", "t6", "Your bill for November", "Due Dec 15")
    new_mail(gmail, "m7", "t7", "Hello", "Maybe a bill?")
    assert queued() == {"t6": "pending", "t7": "pending"}
    jev.answers = [("Finance", 0.92), ("Finance", 0.5)]
    assert process_jev(jev) == 2
    assert queued() == {"t6": "labeled", "t7": "unlabeled"}  # 0.5: below the bar
    state = threads()
    assert label_names(state["t6"]) == {"INBOX", "UNREAD", "Finance"}
    assert label_names(state["t7"]) == {"INBOX", "UNREAD"}
    [entry] = rows(Outbox)
    assert (entry.action, entry.message_ids, entry.add_labels) == ("label", ["m6"], ["Label_2"])
    drain(gmail)
    assert "Label_2" in gmail.messages["m6"]["labelIds"]
    assert rows(AiUsage) == []  # AI usage tracks Anthropic only


def test_jev_transient_errors_retry_later_and_permanent_ones_fail(
    jev_on: None,
    synced: TestClient,
    gmail: FakeGmail,
    jev: FakeJev,
) -> None:
    new_mail(gmail, "m6", "t6", "Bill", "x")
    jev.answers = [529]  # overloaded
    process_jev(jev)
    [row] = rows(AiQueue)
    assert (row.status, row.attempts) == ("pending", 1)
    assert row.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)

    run(_due_now())
    jev.answers = [422]
    process_jev(jev)
    [row] = rows(AiQueue)
    assert (row.status, row.attempts) == ("failed", 2)
    assert row.last_error and "422" in row.last_error


def test_jev_rejected_key_pauses_labeling(
    jev_on: None,
    synced: TestClient,
    gmail: FakeGmail,
    jev: FakeJev,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(labeling, "_paused_until", 0.0)
    new_mail(gmail, "m6", "t6", "Bill", "x")
    new_mail(gmail, "m7", "t7", "Bill", "y")
    jev.answers = [401]
    process_jev(jev)
    assert len(jev.requests) == 1
    assert queued() == {"t6": "pending", "t7": "pending"}
    assert [r.attempts for r in rows(AiQueue)] == [0, 0]
    # The rejected-key notice is about the Anthropic key, which is fine.
    assert synced.get("/api/ai/usage").json()["key_rejected"] is False

    monkeypatch.setattr(labeling, "_paused_until", 0.0)  # ten minutes later, the key works
    jev.answers = [("Finance", 0.9)]
    assert process_jev(jev) == 2
    assert queued() == {"t6": "labeled", "t7": "unlabeled"}
