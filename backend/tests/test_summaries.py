import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from conftest import TEST_DATABASE_URL, run, set_key
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso import demo_seed
from kanso.ai import summaries, usage
from kanso.core.settings import settings
from kanso.db import session
from kanso.db.models import Account, AiUsage, Summary, Thread
from kanso.db.session import Session
from kanso.gmail import mime
from kanso.sync import store

pytestmark = pytest.mark.usefixtures("db", "unpooled")

ME = "me@gmail.com"
SUMMARY = "Lena needs the headcount by Friday; the Berlin question is still open."


@pytest.fixture
def unpooled(monkeypatch: pytest.MonkeyPatch) -> None:
    maker = async_sessionmaker(
        create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool), expire_on_commit=False
    )
    for module in (session, summaries, sys.modules[__name__]):
        monkeypatch.setattr(module, "Session", maker)


@pytest.fixture
def ai_on(db: None) -> None:
    set_key("anthropic", "test-key")


class FakeClaude:
    """The streaming Messages API behind an httpx2 MockTransport, through the real SDK client."""

    def __init__(self) -> None:
        self.answers: list[Any] = []  # text to stream, an HTTP status, or ("refusal", text)
        self.requests: list[dict[str, Any]] = []
        self.delay = 0.0  # seconds before answering

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        await asyncio.sleep(self.delay)
        answer = self.answers.pop(0) if self.answers else SUMMARY
        if isinstance(answer, int):
            error = {"type": "error", "error": {"type": "api_error", "message": "injected"}}
            return httpx.Response(answer, json=error)
        stop, text = answer if isinstance(answer, tuple) else ("end_turn", answer)
        words = text.split(" ")
        chunks = [w + " " for w in words[:-1]] + [words[-1]]
        events: list[dict[str, Any]] = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 900,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
            # Adaptive thinking: an empty thinking block first (display "omitted").
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            *(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": c},
                }
                for c in chunks
            ),
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 45},
            },
            {"type": "message_stop"},
        ]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    def client(self, key: str) -> anthropic.AsyncAnthropic:
        assert key == "test-key"  # the key saved in Settings
        transport = httpx.MockTransport(self.handle)
        return anthropic.AsyncAnthropic(
            api_key=key, max_retries=0, http_client=httpx.AsyncClient(transport=transport)
        )


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    fake = FakeClaude()
    monkeypatch.setattr(summaries, "client", fake.client)
    return fake


NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _raw(
    sender: str, body: str, *, subject: str = "Team offsite", attachment: bool = False
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = f"{ME}, partner@example.com"
    msg["Cc"] = "accountant@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        msg.add_attachment(b"%PDF-1.4 secret", "application", "pdf", filename="budget-secret.pdf")
    return msg.as_bytes()


async def _add(
    account_id: int, gmail_id: str, thread: str, raw: bytes, hours_ago: float, labels: list[str]
) -> int:
    async with Session() as db:
        label_map = await store.label_map(db, account_id)
        meta = {
            "id": gmail_id,
            "threadId": thread,
            "labelIds": labels,
            "snippet": "",
            "internalDate": str(int((NOW - timedelta(hours=hours_ago)).timestamp() * 1000)),
            "sizeEstimate": len(raw),
        }
        thread_id = await store.save_message(db, account_id, meta, mime.parse(raw), label_map)
        await store.refresh_threads(db, [thread_id])
        await db.commit()
        return thread_id


def add(
    gmail_id: str,
    raw: bytes,
    hours_ago: float,
    labels: tuple[str, ...] = ("INBOX",),
    thread: str = "t1",
) -> int:
    return run(_add(1, gmail_id, thread, raw, hours_ago, list(labels)))


async def _account() -> None:
    async with Session() as db:
        db.add(Account(email=ME, display_name="me", color="#2F4B6E", refresh_token=b"x"))
        await db.flush()
        await store.save_labels(
            db, 1, [{"id": i, "name": i, "type": "system"} for i in ("INBOX", "SENT", "DRAFT")]
        )
        await db.commit()


QUOTED = (
    "\n\nOn Mon, Oct 3, 2026 at 9:00 AM Me <me@gmail.com> wrote:\n> my private earlier question\n"
)


@pytest.fixture
def thread(logged_in: TestClient) -> int:
    """A two-message thread: Lena asks (with an attachment and a signature), I answer (with
    Lena's mail quoted)."""
    run(_account())
    add(
        "m1",
        _raw(
            "Lena Fischer <lena@example.com>",
            "Hi,\n\nCan you send the headcount by Friday?\n\nLena\n"
            "-- \nLena Fischer · Acme · +1 555",
            attachment=True,
        ),
        30,
    )
    return add("m2", _raw(f"Me <{ME}>", "Sure, 12 people." + QUOTED), 9, ("SENT",))


def events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")
    ]


def summarize(api: TestClient, thread_id: int) -> list[dict[str, Any]]:
    return events(api.post(f"/api/threads/{thread_id}/summary"))


async def _rows(model: Any) -> list[Any]:
    async with Session() as db:
        return list(await db.scalars(select(model)))


def rows(model: Any) -> list[Any]:
    return run(_rows(model))


def _entries(*bodies: str) -> list[summaries.Entry]:
    return [summaries.Entry("A <a@example.com>", NOW, b) for b in bodies]


def test_state_counts_messages_without_drafts(
    ai_on: None, thread: int, logged_in: TestClient
) -> None:
    state = logged_in.get(f"/api/threads/{thread}/summary").json()
    assert state == {"messages": 2, "summary": None}
    add("d1", _raw(f"Me <{ME}>", "draft text in progress"), 0.5, ("DRAFT",))
    assert logged_in.get(f"/api/threads/{thread}/summary").json()["messages"] == 2


def test_prompt_sends_sender_date_and_trimmed_bodies_only(
    ai_on: None, thread: int, logged_in: TestClient, claude: FakeClaude
) -> None:
    add("d1", _raw(f"Me <{ME}>", "draft text in progress"), 0.5, ("DRAFT",))  # a draft: left out
    summarize(logged_in, thread)
    [request] = claude.requests
    assert request["model"] == "claude-haiku-5-5"  # the default
    assert request["output_config"] == {"effort": "low"}
    assert request["stream"] is True
    assert request["system"] == summaries.INSTRUCTIONS
    [message] = request["messages"]
    assert message["content"] == (
        "<thread>\nSubject: Team offsite\n\n"
        "<message>\nFrom: Lena Fischer <lena@example.com>\nDate: Sun, 01 Nov 2026 03:00 UTC\n\n"
        "Hi,\n\nCan you send the headcount by Friday?\n\nLena\n</message>\n\n"
        "<message>\nFrom: me\nDate: Mon, 02 Nov 2026 00:00 UTC\n\nSure, 12 people.\n</message>"
        "\n</thread>"
    )
    sent = json.dumps(request, ensure_ascii=False)
    private = ["partner@example.com", "accountant@example.com", "budget-secret", "PDF", ME]
    private += ["my private earlier question", "+1 555", "Acme", "draft text in progress"]
    assert [p for p in private if p in sent] == []


def test_summary_model_is_set_in_settings(
    ai_on: None, thread: int, logged_in: TestClient, claude: FakeClaude
) -> None:
    logged_in.patch("/api/settings", json={"summary_model": "claude-sonnet-5-5"})
    summarize(logged_in, thread)
    assert claude.requests[0]["model"] == "claude-sonnet-5-5"
    [row] = rows(Summary)
    assert row.model == "claude-sonnet-5-5"


def test_long_threads_are_capped_newest_first() -> None:
    entries = _entries(*(f"message {i} " + "x" * 3000 for i in range(20)))
    text = summaries.prompt(summaries.Context(1, "Long", "m20", entries, demo=False))
    assert "(8 earlier messages left out)" in text
    assert "message 19 " in text and "message 8 " in text and "message 7 " not in text
    assert text.count("<message>") == 12
    assert len(text) < summaries.TOTAL_LIMIT + 2000  # the bodies plus headers


def test_text_of_reads_text_blocks_and_rejects_unusable_answers() -> None:
    from anthropic.types import Message

    def response(content: list[dict[str, Any]], stop: str = "end_turn") -> Any:
        return Message.model_validate(
            {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": content,
                "stop_reason": stop,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    thinking = {"type": "thinking", "thinking": "", "signature": "s"}
    assert summaries.text_of(response([thinking, {"type": "text", "text": " Done. "}])) == "Done."
    for bad in (response([], "refusal"), response([thinking], "max_tokens"), response([thinking])):
        with pytest.raises(summaries.SummaryError):
            summaries.text_of(bad)


def test_streams_then_stores_and_reopening_makes_no_call(
    ai_on: None, thread: int, logged_in: TestClient, claude: FakeClaude
) -> None:
    got = summarize(logged_in, thread)
    deltas = [e["text"] for e in got if e["type"] == "delta"]
    assert len(deltas) > 3 and "".join(deltas) == SUMMARY  # streamed as it was written
    assert got[-1]["type"] == "done" and got[-1]["summary"]["text"] == SUMMARY
    made = rows(Summary)[0].created_at
    # Reopening: the stored summary, no call, whether read or asked for again.
    state = logged_in.get(f"/api/threads/{thread}/summary").json()
    assert state["summary"]["text"] == SUMMARY
    again = summarize(logged_in, thread)
    assert [e["type"] for e in again] == ["done"] and again[0]["summary"]["text"] == SUMMARY
    assert len(claude.requests) == 1
    # A draft being written doesn't make it stale.
    add("d1", _raw(f"Me <{ME}>", "draft"), 0.5, ("DRAFT",))
    assert logged_in.get(f"/api/threads/{thread}/summary").json()["summary"] is not None

    # A new reply does: no summary until asked, then a fresh one, stored in place of the old.
    add("m3", _raw("Lena Fischer <lena@example.com>", "Also: vegetarian options?"), 0.2)
    assert logged_in.get(f"/api/threads/{thread}/summary").json()["summary"] is None
    claude.answers = ["Fresh summary."]
    assert summarize(logged_in, thread)[-1]["summary"]["text"] == "Fresh summary."
    assert len(claude.requests) == 2
    assert "Also: vegetarian options?" in claude.requests[1]["messages"][0]["content"]
    [row] = rows(Summary)
    assert (row.message_id, row.text) == ("m3", "Fresh summary.")
    assert row.created_at > made


async def _context(thread_id: int) -> summaries.Context:
    async with Session() as db:
        context = await summaries.load(db, thread_id)
    assert context is not None
    return context


def test_requests_at_once_make_one_call(ai_on: None, thread: int, claude: FakeClaude) -> None:
    claude.delay = 0.5

    async def go() -> list[list[dict[str, Any]]]:
        context = await _context(thread)

        async def consume() -> list[dict[str, Any]]:
            return [e async for e in summaries.stream(context)]

        return list(await asyncio.gather(consume(), consume(), consume()))

    results = run(go())
    assert len(claude.requests) == 1
    finals = [r[-1] for r in results]
    assert all(f == {"type": "done", "summary": finals[0]["summary"]} for f in finals)
    assert finals[0]["summary"]["text"] == SUMMARY


def test_reader_leaving_mid_stream_still_stores_it(
    ai_on: None, thread: int, claude: FakeClaude
) -> None:
    async def go() -> None:
        stream = summaries.stream(await _context(thread))
        assert (await anext(stream))["type"] == "delta"
        await stream.aclose()  # the browser went away
        await asyncio.gather(*summaries._tasks)

    run(go())
    [row] = rows(Summary)
    assert row.text == SUMMARY


def test_failure_is_quiet_and_can_be_retried(
    ai_on: None, thread: int, logged_in: TestClient, claude: FakeClaude
) -> None:
    claude.answers = [500, ("refusal", "I can't"), SUMMARY]
    failed = summarize(logged_in, thread)
    assert failed[-1] == {"type": "error", "message": "Claude didn’t answer"}
    assert rows(Summary) == [] and rows(AiUsage) == []  # nothing came back: nothing billed
    refused = summarize(logged_in, thread)
    assert refused[-1] == {"type": "error", "message": "Claude declined to summarize this thread"}
    assert rows(Summary) == []
    [usage] = rows(AiUsage)  # a refusal is billed
    assert (usage.feature, usage.requests) == ("summary", 1)
    # The lock went with each failure, so a retry calls again and works.
    assert summarize(logged_in, thread)[-1]["type"] == "done"
    assert len(claude.requests) == 3


def test_rejected_key_shows_a_notice_until_a_call_works(
    ai_on: None,
    thread: int,
    logged_in: TestClient,
    claude: FakeClaude,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rejected() -> bool:
        return logged_in.get("/api/ai/usage").json()["key_rejected"]

    claude.answers = [401, 403]
    assert summarize(logged_in, thread)[-1] == {"type": "error", "message": usage.KEY_REJECTED}
    assert rejected()
    assert summarize(logged_in, thread)[-1]["message"] == usage.KEY_REJECTED
    # A notice about one key doesn't outlive it.
    set_key("anthropic", "another-key")
    assert not rejected()
    set_key("anthropic", "test-key")
    assert rejected()
    assert summarize(logged_in, thread)[-1]["type"] == "done"
    assert not rejected()


def test_usage_is_recorded_under_summary(
    ai_on: None, thread: int, logged_in: TestClient, claude: FakeClaude
) -> None:
    summarize(logged_in, thread)
    summarize(logged_in, thread)  # stored: no second request, no usage
    [row] = rows(AiUsage)
    assert (row.feature, row.model, row.requests) == ("summary", "claude-haiku-5-5", 1)
    assert (row.input_tokens, row.output_tokens) == (900, 45)
    [feature] = logged_in.get("/api/ai/usage").json()["features"]
    assert feature["feature"] == "summary"
    assert feature["cost_usd"] == pytest.approx((900 * 0.10 + 45 * 0.50) / 1_000_000)


def test_demo_threads_get_a_canned_summary(
    ai_on: None, logged_in: TestClient, claude: FakeClaude
) -> None:
    run(demo_seed.seed())

    async def thread_ids() -> dict[str, int]:
        async with Session() as db:
            return {t.subject: t.id for t in await db.scalars(select(Thread))}

    ids = run(thread_ids())
    long = ids["Team offsite in March"]
    assert logged_in.get(f"/api/threads/{long}/summary").json()["summary"] is None
    got = summarize(logged_in, long)
    assert len([e for e in got if e["type"] == "delta"]) > 3
    done = got[-1]["summary"]
    assert (done["text"], done["model"]) == (demo_seed.SUMMARIES["Team offsite in March"], "demo")
    chinese = ids["新版官网上线时间"]
    assert summarize(logged_in, chinese)[-1]["summary"]["text"].startswith("The new website")
    short = ids["Re: Dinner on Saturday?"]
    assert summarize(logged_in, short)[-1]["summary"]["text"].startswith("Demo summary")
    assert claude.requests == []
    assert {u.feature for u in rows(AiUsage)} == {"labeling"}  # the seed's simulated usage only


def test_a_demo_instance_summarizes_demo_threads_with_claude(
    ai_on: None, logged_in: TestClient, claude: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)  # all mail is made up there
    run(demo_seed.seed())
    [offsite] = [t.id for t in rows(Thread) if t.subject == "Team offsite in March"]
    done = summarize(logged_in, offsite)[-1]["summary"]
    assert (done["text"], done["model"]) == (SUMMARY, "claude-haiku-5-5")
    assert len(claude.requests) == 1
    assert {u.feature for u in rows(AiUsage)} == {"summary"}  # no simulated usage


def test_ai_off_has_no_summaries(thread: int, logged_in: TestClient, claude: FakeClaude) -> None:
    assert logged_in.get(f"/api/threads/{thread}/summary").status_code == 409
    assert logged_in.post(f"/api/threads/{thread}/summary").status_code == 409
    assert claude.requests == [] and rows(Summary) == []


def test_unknown_thread(ai_on: None, logged_in: TestClient) -> None:
    assert logged_in.get("/api/threads/999/summary").status_code == 404
    assert logged_in.post("/api/threads/999/summary").status_code == 404
