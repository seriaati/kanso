import email
import email.policy
import sys
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL, run
from fake_gmail import Crash, FakeGmail, make_raw
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso import demo_seed
from kanso.core.settings import settings
from kanso.db import contacts, session
from kanso.db.models import Account, Draft, Message
from kanso.db.session import Session
from kanso.gmail import client, compose, mime, oauth
from kanso.sync import drafts, engine

# Pure MIME and recipient tests need no database.
db_test = pytest.mark.usefixtures("db", "unpooled", "uploads")


def parse(raw: bytes) -> EmailMessage:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message


def build(**overrides: Any) -> bytes:
    fields: dict[str, Any] = {
        "sender": {"name": "Me Myself", "email": "me@gmail.com"},
        "to": [{"name": "Daniel Reyes", "email": "daniel@example.com"}],
        "cc": [],
        "bcc": [],
        "subject": "Hello",
        "body_html": "<div>Hi</div>",
        "quote_html": None,
        "quote_text": None,
        "message_id": "<new@gmail.com>",
        "in_reply_to": None,
        "references": None,
        "attachments": [],
    }
    return compose.build_message(**(fields | overrides))


def test_headers_and_threading() -> None:
    raw = build(
        cc=[{"name": "", "email": "ana@example.com"}],
        bcc=[{"name": "", "email": "secret@example.com"}],
        subject="Re: Dinner",
        in_reply_to="<b@x>",
        references="<a@x> <b@x>",
    )
    msg = parse(raw)
    assert msg["From"] == "Me Myself <me@gmail.com>"
    assert msg["To"] == "Daniel Reyes <daniel@example.com>"
    assert msg["Cc"] == "ana@example.com"
    assert msg["Bcc"] == "secret@example.com"  # Gmail delivers to it and strips it
    assert (msg["Message-ID"], msg["In-Reply-To"], msg["References"]) == (
        "<new@gmail.com>",
        "<b@x>",
        "<a@x> <b@x>",
    )
    assert msg.get_content_type() == "multipart/alternative"
    assert raw.isascii()  # 7-bit clean on the wire
    # Without a display name, From is left to Gmail.
    assert "From" not in parse(build(sender=None))


def test_cjk_subject_body_and_names_are_encoded() -> None:
    raw = build(
        to=[{"name": "陳志明", "email": "chen@example.tw"}],
        subject="周五的项目会议安排",
        body_html="<div>大家好，<b>周五</b>下午三点开会。</div><div>よろしくお願いします</div>",
    )
    assert raw.isascii()
    head = raw.split(b"\n\n", 1)[0].decode()
    assert "Subject: =?utf-8?" in head  # RFC 2047 encoded words
    assert "=?utf-8?" in head.split("To:", 1)[1].splitlines()[0]
    parsed = mime.parse(raw)  # Kanso's own reader gets it all back
    assert parsed.subject == "周五的项目会议安排"
    assert parsed.to == [{"name": "陳志明", "email": "chen@example.tw"}]
    assert parsed.body_text == "大家好，周五下午三点开会。\nよろしくお願いします"
    assert parsed.body_html is not None and "<b>周五</b>" in parsed.body_html


def test_attachments_and_inline_images() -> None:
    raw = build(
        quote_html='<div class="gmail_quote"><img src="cid:logo@x"></div>',
        attachments=[
            compose.Part("报告.pdf", "application/pdf", b"%PDF-1"),
            compose.Part("logo.png", "image/png", b"PNG", content_id="logo@x"),
            compose.Part("note.eml", "message/rfc822", b"From: x\n\nhi"),
        ],
    )
    msg = parse(raw)
    assert msg.get_content_type() == "multipart/mixed"
    html_part = msg.get_body(("html",))
    assert html_part is not None
    files = {p.get_filename(): p for p in msg.iter_attachments()}
    assert files["报告.pdf"].get_content() == b"%PDF-1"
    assert files["note.eml"].get_content_type() == "application/octet-stream"
    # The inline image is related to the HTML, not a separate attachment.
    assert "logo.png" not in files
    related = [p for p in msg.walk() if p.get_content_type() == "multipart/related"]
    inline = [p for p in related[0].walk() if p.get_content_type() == "image/png"]
    assert inline[0]["Content-ID"] == "<logo@x>"
    parsed = mime.parse(raw)
    assert sorted(a.filename for a in parsed.attachments) == ["logo.png", "note.eml", "报告.pdf"]


def test_plain_text_alternative() -> None:
    html = (
        "<div>Hi <b>Dan</b>,</div><div><br></div><ul><li>one</li><li>two</li></ul>"
        "<ol><li>first</li><li>second</li></ol>"
        '<div>See <a href="https://example.com/x">the plan</a> or <a href="https://a.b">'
        "https://a.b</a>.</div><p>Para&nbsp;one</p><p>Para two</p>"
    )
    assert compose.html_to_text(html) == (
        "Hi Dan,\n\n- one\n- two\n1. first\n2. second\n"
        "See the plan <https://example.com/x> or https://a.b.\nPara one\n\nPara two"
    )
    raw = build(
        body_html="<div>Thanks!</div>",
        quote_html="<blockquote>x</blockquote>",
        quote_text="On Sun Dan wrote:\n> x",
    )
    text_part = parse(raw).get_body(("plain",))
    assert text_part is not None
    assert text_part.get_content() == "Thanks!\n\nOn Sun Dan wrote:\n> x\n"


def test_body_is_sanitized_to_plain_rich_text() -> None:
    dirty = (
        '<div style="color:red" onclick="x()">Hi <script>alert(1)</script><b>bold</b> '
        '<a href="javascript:alert(1)">bad</a> <a href="https://ok.example">ok</a>'
        '<img src="https://t.example/pixel.gif"><span class="x">kept text</span></div>'
    )
    clean = compose.sanitize_body(dirty)
    assert "script" not in clean and "onclick" not in clean and "style" not in clean
    assert "<img" not in clean and "javascript" not in clean
    assert '<a href="https://ok.example"' in clean and "<b>bold</b>" in clean
    assert "kept text" in clean


def test_body_keeps_markdown_blocks_and_only_its_own_images() -> None:
    dirty = (
        "<h2>Plan</h2><pre>a  b\n  c</pre><p>Run <code>make</code></p>"
        '<img src="cid:shot@kanso" alt="shot"><img src="https://t.example/p.gif">'
        '<a href="cid:shot@kanso">x</a>'
    )
    clean = compose.sanitize_body(dirty)
    assert "<h2>Plan</h2>" in clean and "<code>make</code>" in clean
    assert '<img src="cid:shot@kanso" alt="shot">' in clean
    assert "t.example" not in clean and clean.count("<img") == 1
    assert 'href="cid:' not in clean
    assert compose.html_to_text(clean) == "Plan\na  b\n  c\nRun `make`\n\nx"


def original(**fields: Any) -> Message:
    values: dict[str, Any] = {
        "from_name": "Daniel Reyes",
        "from_email": "daniel@example.com",
        "to_addrs": [{"name": "Me", "email": "me@gmail.com"}],
        "cc_addrs": [],
        "reply_to": [],
        "subject": "Dinner on Saturday?",
        "internal_date": datetime(2026, 11, 1, 17, 41, tzinfo=UTC),
        "body_html": None,
        "body_text": "Are you free?\nRamen at 7.",
        "message_id_header": "<c@example.com>",
        "in_reply_to": "<b@example.com>",
        "references": "<a@example.com> <b@example.com>",
    }
    return Message(**(values | fields))


MINE = {"me@gmail.com", "me@work.example"}


def emails(addresses: list[dict[str, str]]) -> list[str]:
    return [a["email"] for a in addresses]


def test_reply_and_reply_all_recipients() -> None:
    message = original(
        to_addrs=[
            {"name": "Me", "email": "Me@Gmail.com"},
            {"name": "Ana", "email": "ana@example.com"},
            {"name": "Work me", "email": "me@work.example"},  # another connected account
        ],
        cc_addrs=[
            {"name": "Kenji", "email": "kenji@example.com"},
            {"name": "", "email": "ANA@example.com"},
        ],
    )
    to, cc = compose.reply_recipients(message, MINE, reply_all=False)
    assert (emails(to), cc) == (["daniel@example.com"], [])
    to, cc = compose.reply_recipients(message, MINE, reply_all=True)
    assert emails(to) == ["daniel@example.com"]
    assert emails(cc) == ["ana@example.com", "kenji@example.com"]  # no own address, no repeats
    # Reply-To wins over From.
    to, _ = compose.reply_recipients(
        original(reply_to=[{"name": "List", "email": "list@example.com"}]), MINE, reply_all=False
    )
    assert emails(to) == ["list@example.com"]


def test_reply_to_your_own_message_goes_to_its_recipients() -> None:
    mine = original(
        from_email="me@gmail.com",
        to_addrs=[{"name": "Daniel", "email": "daniel@example.com"}],
        cc_addrs=[
            {"name": "Ana", "email": "ana@example.com"},
            {"name": "", "email": "me@work.example"},
        ],
    )
    to, cc = compose.reply_recipients(mine, MINE, reply_all=True)
    assert (emails(to), emails(cc)) == (["daniel@example.com"], ["ana@example.com"])
    note = original(from_email="me@gmail.com", to_addrs=[{"name": "", "email": "me@gmail.com"}])
    assert emails(compose.reply_recipients(note, MINE, reply_all=False)[0]) == ["me@gmail.com"]


def test_subject_prefixes_are_not_doubled() -> None:
    assert compose.reply_subject("Dinner") == "Re: Dinner"
    assert compose.reply_subject("RE: Dinner") == "RE: Dinner"
    assert compose.reply_subject("回复：周五") == "回复：周五"
    assert compose.reply_subject("Fwd: Dinner") == "Re: Fwd: Dinner"
    assert compose.forward_subject("Dinner") == "Fwd: Dinner"
    assert compose.forward_subject("FW: Dinner") == "FW: Dinner"
    assert compose.forward_subject("Re: Dinner") == "Fwd: Re: Dinner"


def test_quotes_and_references() -> None:
    message = original()
    assert compose.references(message) == "<a@example.com> <b@example.com> <c@example.com>"
    quote_html, quote_text = compose.reply_quote(message, "Asia/Tokyo")
    assert (
        "On Mon, Nov 2, 2026 at 2:41 AM Daniel Reyes &lt;daniel@example.com&gt; wrote:"
        in quote_html
    )
    assert 'class="gmail_quote"' in quote_html and "Are you free?<br>Ramen at 7." in quote_html
    assert quote_text.endswith("wrote:\n> Are you free?\n> Ramen at 7.")
    styled = original(body_html='<style>body{color:red}</style><p>Hi <img src="cid:logo@x"></p>')
    quote_html, _ = compose.reply_quote(styled, None)
    assert "<style" not in quote_html and "cid:" not in quote_html
    forward_html, forward_text = compose.forward_quote(styled, "UTC")
    assert "---------- Forwarded message ---------" in forward_html
    assert 'src="cid:logo@x"' in forward_html  # the image is forwarded with the attachments
    assert "Subject: Dinner on Saturday?" in forward_text


@pytest.fixture
def unpooled(monkeypatch: pytest.MonkeyPatch) -> None:
    maker = async_sessionmaker(
        create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool), expire_on_commit=False
    )
    for module in (session, engine, drafts, sys.modules[__name__]):
        monkeypatch.setattr(module, "Session", maker)


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(drafts, "_sender_names", {})
    return tmp_path / "uploads"


def with_attachment(subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Daniel Reyes <daniel@example.com>"
    msg["To"] = "me@gmail.com, Ana <ana@example.com>"
    msg["Subject"] = subject
    msg["Message-ID"] = "<orig@example.com>"
    msg.set_content(body)
    msg.add_attachment(b"%PDF-contract", "application", "pdf", filename="contract.pdf")
    return msg.as_bytes()


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    fake = FakeGmail()
    fake.add("m1", "t1", ["INBOX"], with_attachment("Lease renewal", "Contract attached"))
    fake.add("m2", "t2", ["SENT"], make_raw("Trail map", "For next time", "Me <me@gmail.com>"))

    async def no_sleep(seconds: float) -> None:
        fake.sleeps.append(seconds)

    monkeypatch.setattr(client, "sleep", no_sleep)
    monkeypatch.setattr(client, "http_client", fake.client)
    return fake


async def _setup(gmail: FakeGmail) -> None:
    async with Session() as db:
        account = Account(
            email="me@gmail.com", display_name="me", color="#2F4B6E", refresh_token=b"x"
        )
        db.add(account)
        await db.commit()
        oauth._access_tokens[account.id] = ("access-token", time.time() + 3600)
    async with gmail.client() as http:
        await engine.sync_account(1, http)


@pytest.fixture
def synced(gmail: FakeGmail, logged_in: TestClient) -> TestClient:
    run(_setup(gmail))
    return logged_in


def sync(gmail: FakeGmail) -> None:
    async def go() -> None:
        async with gmail.client() as http:
            await engine.sync_account(1, http)

    run(go())


async def _message_id(gmail_id: str) -> int:
    async with Session() as db:
        found = await db.scalar(select(Message.id).where(Message.gmail_id == gmail_id))
        assert found is not None
        return found


def message_id(gmail_id: str) -> int:
    return run(_message_id(gmail_id))


async def _draft_rows() -> list[Draft]:
    async with Session() as db:
        return list(await db.scalars(select(Draft).order_by(Draft.id)))


def draft_rows() -> list[Draft]:
    return run(_draft_rows())


def thread_subjects(api: TestClient, mailbox: str) -> list[str]:
    response = api.get("/api/threads", params={"account_id": 1, "mailbox": mailbox})
    return [t["subject"] for t in response.json()["threads"]]


def content(draft: dict[str, Any], **changes: Any) -> dict[str, Any]:
    keys = ("account_id", "to", "cc", "bcc", "subject", "body_html")
    return {k: draft[k] for k in keys} | changes


def new_draft(api: TestClient, **body: Any) -> dict[str, Any]:
    response = api.post("/api/drafts", json={"mode": "new", **body})
    assert response.status_code == 200, response.text
    return response.json()


@db_test
def test_draft_create_update_and_delete(synced: TestClient, gmail: FakeGmail) -> None:
    draft = new_draft(synced)
    assert draft["saved"] is False and gmail.drafts == {}  # nothing in Gmail until a save

    to = [{"name": "Ana", "email": "ana@example.com"}]
    saved = synced.put(
        f"/api/drafts/{draft['id']}",
        json=content(draft, to=to, subject="Plans", body_html="<div>Hi</div>"),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] is True
    [(gmail_draft, first_message)] = gmail.drafts.items()
    assert mime.parse(gmail.messages[first_message]["raw"]).subject == "Plans"
    assert gmail.messages[first_message]["labelIds"] == ["DRAFT"]
    assert thread_subjects(synced, "drafts") == ["Plans"]  # in the mirror at once
    assert "From: Me Myself <me@gmail.com>" in gmail.messages[first_message]["raw"].decode()

    synced.put(
        f"/api/drafts/{draft['id']}",
        json=content(draft, to=to, subject="Plans v2", body_html="<div>Hi!</div>"),
    )
    second_message = gmail.drafts[gmail_draft]  # same draft, new message
    assert second_message != first_message and first_message not in gmail.messages
    assert thread_subjects(synced, "drafts") == ["Plans v2"]
    sync(gmail)  # the sync agrees: one draft, no leftovers
    assert thread_subjects(synced, "drafts") == ["Plans v2"]
    reopened = synced.get(f"/api/drafts/{draft['id']}").json()
    assert (reopened["subject"], reopened["body_html"], reopened["to"]) == (
        "Plans v2",
        "<div>Hi!</div>",
        to,
    )

    assert synced.delete(f"/api/drafts/{draft['id']}").status_code == 204
    assert gmail.drafts == {} and second_message not in gmail.messages
    assert thread_subjects(synced, "drafts") == [] and draft_rows() == []


@db_test
def test_draft_deleted_in_gmail_is_written_again(synced: TestClient, gmail: FakeGmail) -> None:
    draft = new_draft(synced)
    synced.put(f"/api/drafts/{draft['id']}", json=content(draft, subject="One"))
    gmail.drafts.clear()  # deleted in Gmail's web UI meanwhile
    response = synced.put(f"/api/drafts/{draft['id']}", json=content(draft, subject="Two"))
    assert response.status_code == 200, response.text
    [message] = gmail.drafts.values()
    assert mime.parse(gmail.messages[message]["raw"]).subject == "Two"


@db_test
def test_reply_draft_threads_in_gmail_and_for_recipients(
    synced: TestClient, gmail: FakeGmail
) -> None:
    draft = synced.post(
        "/api/drafts", json={"mode": "reply_all", "message_id": message_id("m1"), "tz": "UTC"}
    ).json()
    assert draft["subject"] == "Re: Lease renewal"
    assert emails(draft["to"]) == ["daniel@example.com"]
    assert emails(draft["cc"]) == ["ana@example.com"]  # not me
    assert "Contract attached" in draft["quote_html"]
    saved = synced.put(
        f"/api/drafts/{draft['id']}", json=content(draft, body_html="<div>Signed.</div>")
    )
    key, upload = next(w for w in gmail.writes if w[0] == "POST /drafts")
    assert upload["metadata"] == {"message": {"threadId": "t1"}}
    msg = parse(upload["raw"])
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert msg["References"] == "<orig@example.com>"
    text_part = msg.get_body(("plain",))
    assert text_part is not None and "> Contract attached" in text_part.get_content()
    # The draft shows in its thread, which the reader can open in the composer.
    thread = synced.get(f"/api/threads/{saved.json()['thread_id']}")
    assert thread.status_code == 200
    drafts_in_thread = [m for m in thread.json()["messages"] if m["draft"]]
    assert len(drafts_in_thread) == 1
    reopened = synced.post(
        "/api/drafts/open", json={"message_id": drafts_in_thread[0]["id"]}
    ).json()
    assert reopened["id"] == draft["id"]


@db_test
def test_send_new_message(synced: TestClient, gmail: FakeGmail) -> None:
    draft = new_draft(synced)
    body = content(
        draft,
        to=[{"name": "Ana", "email": "ana@example.com"}],
        subject="Hello",
        body_html="<div>Hi</div>",
    )
    response = synced.post(f"/api/drafts/{draft['id']}/send", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert len(gmail.sent) == 1 and gmail.drafts == {}
    assert [k for k, _ in gmail.writes].count("POST /drafts/send") == 1
    assert thread_subjects(synced, "sent")[0] == "Hello"
    assert thread_subjects(synced, "drafts") == []
    assert draft_rows() == []
    sync(gmail)
    assert thread_subjects(synced, "sent").count("Hello") == 1


@db_test
def test_send_needs_a_recipient(synced: TestClient, gmail: FakeGmail) -> None:
    draft = new_draft(synced)
    response = synced.post(f"/api/drafts/{draft['id']}/send", json=content(draft, subject="x"))
    assert response.status_code == 400 and gmail.sent == []
    assert draft_rows()[0].sending_since is None


@db_test
def test_gmail_refusing_the_send_keeps_the_draft(synced: TestClient, gmail: FakeGmail) -> None:
    draft = new_draft(synced)
    body = content(draft, to=[{"name": "", "email": "ana@example.com"}], subject="x")
    gmail.failures["POST /drafts/send"] = [400]
    response = synced.post(f"/api/drafts/{draft['id']}/send", json=body)
    assert response.status_code == 400 and "Gmail didn’t send it" in response.json()["detail"]
    [row] = draft_rows()
    assert row.sending_since is None and len(gmail.drafts) == 1
    # Fixed, sent again: once.
    assert synced.post(f"/api/drafts/{draft['id']}/send", json=body).json()["status"] == "sent"
    assert len(gmail.sent) == 1


async def _send(draft_id: int, gmail: FakeGmail) -> None:
    async with drafts.lock(draft_id), Session() as db, gmail.client() as http:
        draft = await db.get(Draft, draft_id)
        assert draft is not None
        await drafts.send(db, draft, http)


async def _resume(gmail: FakeGmail) -> None:
    async with gmail.client() as http:
        await drafts.resume_sends(http)


def saved_draft(api: TestClient) -> int:
    draft = new_draft(api)
    body = content(draft, to=[{"name": "", "email": "ana@example.com"}], subject="Once only")
    assert api.put(f"/api/drafts/{draft['id']}", json=body).status_code == 200
    return draft["id"]


@db_test
@pytest.mark.parametrize("crash", ["before", "after"])
def test_crash_mid_send_is_resumed_without_a_duplicate(
    synced: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch, crash: str
) -> None:
    """The worker dies just before drafts.send, or after Gmail sent it but before Kanso knew."""
    draft_id = saved_draft(synced)
    (gmail.crash_before if crash == "before" else gmail.crash_after).add("POST /drafts/send")
    with pytest.raises(Crash):
        run(_send(draft_id, gmail))
    [row] = draft_rows()
    assert row.sending_since is not None  # marked before drafts.send
    assert len(gmail.sent) == (1 if crash == "after" else 0)

    # A save can't touch it any more (it would re-create a sent draft).
    saved = synced.put(f"/api/drafts/{draft_id}", json={"account_id": 1, "subject": "changed"})
    assert saved.status_code == 409

    run(_resume(gmail))  # too fresh: left alone (it may still be in flight)
    assert len(draft_rows()) == 1
    monkeypatch.setattr(drafts, "RESUME_AFTER", timedelta(0))
    run(_resume(gmail))
    assert draft_rows() == []
    assert len(gmail.sent) == 1
    assert [k for k, _ in gmail.writes].count("POST /drafts/send") == (2 if crash == "after" else 1)
    assert thread_subjects(synced, "drafts") == []
    sync(gmail)
    assert thread_subjects(synced, "sent").count("Once only") == 1


@db_test
def test_unanswered_send_is_pending_then_retried_once(
    synced: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gmail keeps answering 500, so it may have sent the message."""
    draft_id = saved_draft(synced)
    gmail.failures["POST /drafts/send"] = [500] * client.MAX_ATTEMPTS
    body = {
        "account_id": 1,
        "to": [{"name": "", "email": "ana@example.com"}],
        "subject": "Once only",
    }
    response = synced.post(f"/api/drafts/{draft_id}/send", json=body)
    assert response.json() == {"status": "pending", "thread_id": None}
    assert draft_rows()[0].sending_since is not None
    retry = synced.post(f"/api/drafts/{draft_id}/send", json=body | {"subject": "ignored"})
    assert retry.json()["status"] == "sent"
    assert len(gmail.sent) == 1
    assert mime.parse(gmail.sent[0]).subject == "Once only"


@db_test
def test_reply_from_a_notification(synced: TestClient, gmail: FakeGmail) -> None:
    """The Android app's Reply button: plain text to the sender only, threaded as a reply."""
    body = {"message_id": message_id("m1"), "text": "Sounds good.\n<b>Thanks</b>", "tz": "UTC"}
    response = synced.post("/api/reply", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    [raw] = gmail.sent
    sent = parse(raw)
    assert sent["To"] == "Daniel Reyes <daniel@example.com>"  # not Ana: no reply-all
    assert sent["Cc"] is None
    assert sent["Subject"] == "Re: Lease renewal"
    assert sent["In-Reply-To"] == "<orig@example.com>"
    text = mime.parse(raw).body_text
    assert "Sounds good.\n<b>Thanks</b>" in text  # as typed, not markup
    assert "wrote:" in text  # the quote, as in any reply
    assert draft_rows() == [] and gmail.drafts == {}
    assert thread_subjects(synced, "sent")[0] == "Lease renewal"


@db_test
def test_reply_from_a_notification_unanswered_is_pending_then_resumed_once(
    synced: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail.failures["POST /drafts/send"] = [500] * client.MAX_ATTEMPTS
    body = {"message_id": message_id("m1"), "text": "On my way"}
    response = synced.post("/api/reply", json=body)
    assert response.status_code == 202
    assert response.json() == {"status": "pending", "thread_id": None}
    assert draft_rows()[0].sending_since is not None
    monkeypatch.setattr(drafts, "RESUME_AFTER", timedelta(0))
    run(_resume(gmail))
    assert draft_rows() == [] and len(gmail.sent) == 1


@db_test
def test_reply_from_a_notification_refused(synced: TestClient, gmail: FakeGmail) -> None:
    assert synced.post("/api/reply", json={"message_id": 999, "text": "Hi"}).status_code == 404
    empty = {"message_id": message_id("m1"), "text": ""}
    assert synced.post("/api/reply", json=empty).status_code == 422
    gmail.failures["POST /drafts"] = [500] * client.MAX_ATTEMPTS
    failed = synced.post("/api/reply", json={"message_id": message_id("m1"), "text": "Hi"})
    assert failed.status_code == 502  # not sent, and nothing left behind
    assert draft_rows() == [] and gmail.sent == []


@db_test
def test_forward_sends_the_originals_attachments(synced: TestClient, gmail: FakeGmail) -> None:
    draft = synced.post(
        "/api/drafts", json={"mode": "forward", "message_id": message_id("m1")}
    ).json()
    assert draft["subject"] == "Fwd: Lease renewal"
    assert draft["to"] == [] and [a["filename"] for a in draft["attachments"]] == ["contract.pdf"]
    body = content(
        draft, to=[{"name": "Kenji", "email": "kenji@example.com"}], body_html="<div>FYI</div>"
    )
    assert synced.post(f"/api/drafts/{draft['id']}/send", json=body).json()["status"] == "sent"
    [sent] = gmail.sent
    msg = parse(sent)
    files = {p.get_filename(): p.get_content() for p in msg.iter_attachments()}
    assert files == {"contract.pdf": b"%PDF-contract"}
    text_part = msg.get_body(("plain",))
    assert text_part is not None
    assert "---------- Forwarded message ---------" in text_part.get_content()
    assert msg["References"] == "<orig@example.com>"


@db_test
def test_attachments_upload_limit_remove_and_recovery(
    synced: TestClient, gmail: FakeGmail, uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = new_draft(synced)
    url = f"/api/drafts/{draft['id']}/attachments"
    first = synced.post(
        url,
        params={"filename": "../报价.txt"},
        content=b"quote",
        headers={"Content-Type": "text/plain"},
    )
    assert first.status_code == 200, first.text
    [attachment] = first.json()["attachments"]
    assert attachment["filename"] == "报价.txt" and attachment["size"] == 5
    second = synced.post(
        url,
        params={"filename": "b.bin"},
        content=b"x" * 10,
        headers={"Content-Type": "application/octet-stream"},
    )
    second_id = second.json()["attachments"][1]["id"]
    monkeypatch.setattr(drafts, "ATTACHMENTS_LIMIT", 20)
    too_big = synced.post(url, params={"filename": "c.bin"}, content=b"y" * 6)
    assert too_big.status_code == 413
    removed = synced.delete(f"{url}/{second_id}").json()
    assert [a["filename"] for a in removed["attachments"]] == ["报价.txt"]

    body = content(draft, to=[{"name": "", "email": "ana@example.com"}], subject="Files")
    synced.put(f"/api/drafts/{draft['id']}", json=body)
    [message] = gmail.drafts.values()
    assert [a.filename for a in mime.parse(gmail.messages[message]["raw"]).attachments] == [
        "报价.txt"
    ]
    # The cache is gone (a restart): the bytes come back from the Gmail draft.
    for file in uploads.rglob("*"):
        if file.is_file():
            file.unlink()
    assert synced.post(f"/api/drafts/{draft['id']}/send", json=body).json()["status"] == "sent"
    files = {
        p.get_filename(): p.get_payload(decode=True)
        for p in parse(gmail.sent[0]).iter_attachments()
    }
    assert files == {"报价.txt": b"quote"}
    assert not any(uploads.rglob("*"))  # cleaned up after sending


@db_test
def test_inline_images_go_in_the_body_and_leave_with_it(
    synced: TestClient, gmail: FakeGmail
) -> None:
    draft = new_draft(synced)
    url = f"/api/drafts/{draft['id']}/attachments"
    not_image = synced.post(
        url,
        params={"filename": "a.txt", "inline": True},
        content=b"x",
        headers={"Content-Type": "text/plain"},
    )
    assert not_image.status_code == 400
    added = synced.post(
        url,
        params={"filename": "shot.png", "inline": True},
        content=b"PNG-bytes",
        headers={"Content-Type": "image/png"},
    ).json()
    [image] = added["attachments"]
    assert image["content_id"] == f"{image['id']}@kanso"
    shown = synced.get(f"{url}/{image['id']}")
    assert shown.content == b"PNG-bytes" and shown.headers["content-type"] == "image/png"

    to = [{"name": "", "email": "ana@example.com"}]
    body = content(draft, to=to, body_html=f'<div>See <img src="cid:{image["content_id"]}"></div>')
    assert synced.post(f"/api/drafts/{draft['id']}/send", json=body).json()["status"] == "sent"
    msg = parse(gmail.sent[0])
    related = [p for p in msg.walk() if p.get_content_type() == "multipart/related"]
    [png] = [p for p in related[0].walk() if p.get_content_type() == "image/png"]
    assert png["Content-ID"] == f"<{image['content_id']}>" and png.get_content() == b"PNG-bytes"
    assert list(msg.iter_attachments()) == []

    # Deleted from the body, it doesn't turn into an attachment.
    other = new_draft(synced)
    url = f"/api/drafts/{other['id']}/attachments"
    synced.post(
        url,
        params={"filename": "s.png", "inline": True},
        content=b"P",
        headers={"Content-Type": "image/png"},
    )
    synced.put(f"/api/drafts/{other['id']}", json=content(other, body_html="<div>No image</div>"))
    [saved] = gmail.drafts.values()
    assert mime.parse(gmail.messages[saved]["raw"]).attachments == []


@db_test
def test_draft_written_in_gmail_opens_in_the_composer(synced: TestClient, gmail: FakeGmail) -> None:
    gmail.add("x1", "t9", ["DRAFT"], with_attachment("From the web", "Started in Gmail"))
    gmail.drafts["r-web"] = "x1"
    sync(gmail)
    draft = synced.post("/api/drafts/open", json={"message_id": message_id("x1")}).json()
    assert draft["subject"] == "From the web" and "Started in Gmail" in draft["body_html"]
    assert [a["filename"] for a in draft["attachments"]] == ["contract.pdf"]
    body = content(draft, body_html="<div>Finished in Kanso</div>")
    assert synced.post(f"/api/drafts/{draft['id']}/send", json=body).json()["status"] == "sent"
    files = {p.get_filename(): p.get_content() for p in parse(gmail.sent[0]).iter_attachments()}
    assert files == {"contract.pdf": b"%PDF-contract"}  # carried over from the Gmail draft
    assert gmail.drafts == {}


@pytest.fixture
def demo(logged_in: TestClient) -> TestClient:
    run(demo_seed.seed())
    return logged_in


@db_test
def test_demo_account_drafts_and_sends_locally(demo: TestClient) -> None:
    daniel = next(
        t
        for t in demo.get("/api/threads", params={"account_id": 1, "q": "Dinner"}).json()["threads"]
    )
    thread = demo.get(f"/api/threads/{daniel['id']}").json()
    latest = thread["messages"][-1]["id"]
    draft = demo.post("/api/drafts", json={"mode": "reply", "message_id": latest}).json()
    assert draft["subject"] == "Re: Dinner on Saturday?"
    assert emails(draft["to"]) == ["daniel.reyes@example.com"]
    body = content(draft, body_html="<div>7pm works. 好的！</div>")
    saved = demo.put(f"/api/drafts/{draft['id']}", json=body).json()
    assert saved["saved"] and saved["thread_id"] == daniel["id"]
    assert "Re: Dinner on Saturday?" in thread_subjects(demo, "drafts")
    sent = demo.post(f"/api/drafts/{draft['id']}/send", json=body).json()
    assert sent == {"status": "sent", "thread_id": daniel["id"]}
    messages = demo.get(f"/api/threads/{daniel['id']}").json()["messages"]
    assert messages[-1]["body_text"] is None and "好的" in (messages[-1]["body_html"] or "")
    assert not any(m["draft"] for m in messages)
    assert "Re: Dinner on Saturday?" in thread_subjects(demo, "sent")

    # The seeded draft (written "elsewhere") opens too.
    seeded = next(
        t
        for t in demo.get("/api/threads", params={"account_id": 1, "mailbox": "drafts"}).json()[
            "threads"
        ]
    )
    detail = demo.get(f"/api/threads/{seeded['id']}").json()
    opened = demo.post("/api/drafts/open", json={"message_id": detail["messages"][0]["id"]}).json()
    assert opened["subject"] == "Notes on the contract" and opened["saved"]


@db_test
def test_contacts_ranked_by_how_often_and_recently_you_wrote(demo: TestClient) -> None:
    async def refresh() -> None:
        async with Session() as db:
            await contacts.refresh(db)

    run(refresh())
    found = demo.get("/api/contacts", params={"q": "pri"}).json()
    assert found[0] == {"name": "Priya Shah", "email": "priya.shah@example.com"}
    # Daniel and Priya were written to; mail you only received ranks lower.
    top = [c["email"] for c in demo.get("/api/contacts").json()]
    assert set(top[:2]) == {"daniel.reyes@example.com", "priya.shah@example.com"}
    assert (
        demo.get("/api/contacts", params={"q": "陳"}).json()[0]["email"]
        == "chen.zhiming@example.tw"
    )
    assert demo.get("/api/contacts", params={"q": "nobody-like-this"}).json() == []
    assert demo.get("/api/contacts", params={"q": "%"}).json() == []  # LIKE wildcards escaped
    assert demo_seed.DEMO_EMAIL not in top

    # Writing to someone new moves them up after the next refresh.
    async def write() -> None:
        async with Session() as db:
            await db.execute(
                update(Message)
                .where(Message.subject == "Interview follow-up")
                .values(
                    from_email=demo_seed.DEMO_EMAIL,
                    to_addrs=[{"name": "Fern", "email": "talent@fernwood.example.com"}],
                )
            )
            await db.commit()

    run(write())
    run(refresh())
    assert demo.get("/api/contacts", params={"q": "fern"}).json()[0]["name"] == "Fern"


def test_contacts_require_login(client: TestClient) -> None:
    assert client.get("/api/contacts").status_code == 401
    assert client.post("/api/drafts", json={"mode": "new"}).status_code == 401
