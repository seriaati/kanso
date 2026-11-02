import asyncio
from typing import Any

import pytest
from conftest import run
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kanso import demo_seed
from kanso.api import events
from kanso.db import redis as redis_module
from kanso.db.models import Account, Label, Message, Thread
from kanso.db.session import Session

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def demo(logged_in: TestClient) -> TestClient:
    run(demo_seed.seed())
    return logged_in


def subjects(client: TestClient, **params: Any) -> list[str]:
    response = client.get("/api/threads", params={"account_id": 1, "limit": 200, **params})
    assert response.status_code == 200, response.text
    return [t["subject"] for t in response.json()["threads"]]


def label_id(client: TestClient, name: str) -> int:
    return next(
        label["id"]
        for label in client.get("/api/labels?account_id=1").json()
        if label["name"] == name
    )


def test_requires_login(client: TestClient) -> None:
    for path in ("/api/threads", "/api/threads/1", "/api/labels", "/api/sync", "/api/events"):
        assert client.get(path).status_code == 401


def test_thread_list_newest_first_with_cursor_pagination(demo: TestClient) -> None:
    everything = subjects(demo)
    assert everything[0] == "PR #12: Gmail history sync"
    assert "Congratulations!!!" not in everything  # Trash is left out
    pages: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"account_id": 1, "limit": 7}
        if cursor:
            params["cursor"] = cursor
        body = demo.get("/api/threads", params=params).json()
        pages += [t["subject"] for t in body["threads"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == everything
    assert (
        demo.get("/api/threads", params={"account_id": 1, "cursor": "nonsense"}).status_code == 400
    )


def test_mailboxes_and_labels(demo: TestClient) -> None:
    inbox = subjects(demo, mailbox="inbox")
    assert "Lease renewal for apartment 4B" in inbox
    assert "Lisbon apartment booking" not in inbox
    assert set(subjects(demo, mailbox="done")) >= {"Lisbon apartment booking", "Trail map"}
    assert "Lease renewal for apartment 4B" not in subjects(demo, mailbox="done")
    assert subjects(demo, mailbox="drafts") == ["Notes on the contract"]
    assert "Trail map" in subjects(demo, mailbox="sent")
    assert subjects(demo, mailbox="trash") == ["Congratulations!!!"]
    assert subjects(demo, mailbox="starred") == ["Flight itinerary: Lisbon, Dec 12"]
    everything = subjects(demo, mailbox="all")
    assert set(everything) >= set(inbox) | {"Lisbon apartment booking", "Trail map"}
    assert "Congratulations!!!" not in everything
    assert subjects(demo, label_id=label_id(demo, "Health")) == [
        "Appointment reminder: Nov 9, 10:30"
    ]
    assert subjects(demo, label_id=label_id(demo, "TRASH")) == ["Congratulations!!!"]
    # Several labels match threads in any of them.
    health_and_travel = subjects(
        demo, label_id=[label_id(demo, "Health"), label_id(demo, "Travel")]
    )
    assert "Appointment reminder: Nov 9, 10:30" in health_and_travel
    assert "Flight itinerary: Lisbon, Dec 12" in health_and_travel
    assert "Q4 planning notes" not in health_and_travel
    assert demo.get("/api/threads", params={"account_id": 1, "label_id": 9999}).status_code == 404
    assert demo.get("/api/threads", params={"account_id": 1, "mailbox": "nope"}).status_code == 422
    assert subjects(demo, account_id=2) == []
    assert demo.get("/api/threads").status_code == 422  # always one account


def test_thread_list_item_shape(demo: TestClient) -> None:
    thread = demo.get("/api/threads", params={"account_id": 1, "q": "dinner"}).json()["threads"][0]
    assert thread["subject"] == "Re: Dinner on Saturday?"
    assert thread["senders"] == ["Daniel Reyes", "Demo"]
    assert thread["me"] == ["Demo"]  # shown as "Daniel Reyes, me"
    assert thread["recipients"] is None  # only in Sent and Drafts
    assert (thread["message_count"], thread["unread"], thread["in_inbox"]) == (3, True, True)
    assert thread["snippet"] == "Works for me. 7pm?"
    assert set(thread) == {
        "id", "account_id", "subject", "snippet", "senders", "last_message_at", "message_count",
        "label_ids", "unread", "starred", "important", "in_inbox", "in_trash", "has_attachments",
        "lang", "section", "me", "recipients",
    }  # fmt: skip


def test_me_and_recipients(demo: TestClient) -> None:
    def people(**params: Any) -> dict[str, tuple[list[str], list[str] | None]]:
        threads = demo.get("/api/threads", params={"account_id": 1, "limit": 200, **params}).json()[
            "threads"
        ]
        return {t["subject"]: (t["me"], t["recipients"]) for t in threads}

    # Sent and Drafts show who the user wrote to, leaving the user's own addresses out.
    sent = people(mailbox="sent")
    assert sent["Trail map"] == (["Demo"], ["Priya Shah"])
    assert sent["Re: Dinner on Saturday?"] == (["Demo"], ["Daniel Reyes"])
    assert people(mailbox="drafts") == {"Notes on the contract": (["Demo"], ["Kenji Mori"])}
    # Elsewhere (a mailbox, search) only "me" is marked.
    assert people()["Trail map"] == (["Demo"], None)
    assert people()["Lease renewal for apartment 4B"] == ([], None)
    assert people()["Re: Dinner on Saturday?"] == (["Demo"], None)
    assert people(q="trail")["Trail map"] == (["Demo"], None)
    grouped = demo.get(
        "/api/threads", params={"account_id": 1, "mailbox": "inbox", "grouped": "true"}
    ).json()
    dinner = next(t for t in grouped["threads"] if t["subject"] == "Re: Dinner on Saturday?")
    assert dinner["me"] == ["Demo"]


LEASE = "Lease renewal for apartment 4B"
STATEMENTS = ["Your October statement is ready", "Your September statement is ready"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("lease", ["Lease renewal for apartment 4B"]),
        ("renew", ["Lease renewal for apartment 4B"]),  # words match as prefixes
        ('"seats 14A"', ["Flight itinerary: Lisbon, Dec 12"]),
        ('"14A seats"', []),
        ("from:kenji", ["Contract review, draft v2", "Team offsite in March"]),
        # from: and to: match the start of a word (name, address or a part of it), as in Gmail.
        ("from:ana", ["Flight itinerary: Lisbon, Dec 12", "Lisbon apartment booking"]),
        ("from:tan", ["Lease renewal for apartment 4B"]),
        ("from:aka", []),  # inside "Tanaka": not a word start
        ("from:reyes", ["Re: Dinner on Saturday?"]),  # daniel.reyes@example.com
        ("from:example.cn", ["周五的项目会议安排", "新版官网上线时间"]),
        ("from:@example.jp", ["来週の打ち合わせについて"]),
        ('from:"daniel reyes"', ["Re: Dinner on Saturday?"]),
        ("to:reyes", ["Re: Dinner on Saturday?"]),
        ("to:name", []),  # not the "name" key of the stored addresses
        ("from:northwind.example", STATEMENTS),
        ("to:priya", ["Trail map"]),
        ("label:health", ["Appointment reminder: Nov 9, 10:30"]),
        ("is:starred", ["Flight itinerary: Lisbon, Dec 12"]),
        ("is:unread label:receipts", ["Your order has shipped"]),
        ("has:attachment label:finance", STATEMENTS[:1]),
        ("contract has:attachment", [LEASE, "Contract review, draft v2"]),
        ("label:trash", ["Congratulations!!!"]),
        ("before:2000/01/01", []),
        ("after:2000-01-01 from:akiyama", ["Appointment reminder: Nov 9, 10:30"]),
        # Chinese and Japanese: anywhere inside a run of characters, any length.
        ("会议室", ["周五的项目会议安排"]),
        ("项目会议", ["周五的项目会议安排"]),
        ("会", ["周五的项目会议安排"]),
        ("搬遷", ["台北辦公室搬遷通知"]),
        ("打ち合わせ", ["来週の打ち合わせについて"]),
        ("京都駅", ["来週の打ち合わせについて"]),
        ("from:王芳", ["周五的项目会议安排", "新版官网上线时间"]),
        # A CJK name has no word breaks: anywhere in it.
        ("from:田花", ["来週の打ち合わせについて"]),
        ("from:花子", ["来週の打ち合わせについて"]),
        ("会议室 from:kenji", []),
        ("会議室", []),  # a Japanese word not in the (Chinese) mail
        ("is:weird", []),  # not an operator: searched as text
    ],
)  # fmt: skip
def test_search(demo: TestClient, query: str, expected: list[str]) -> None:
    assert subjects(demo, q=query) == expected


@pytest.mark.parametrize(
    "query",
    ["don't", "a&b|c", "(x", "!", "'", "\\", ":*", "foo:bar:baz", '"unclosed', "from:", "%_"],
)
def test_odd_search_input_is_harmless(demo: TestClient, query: str) -> None:
    assert demo.get("/api/threads", params={"account_id": 1, "q": query}).status_code == 200


def test_search_combines_with_filters(demo: TestClient) -> None:
    assert subjects(demo, q="statement", mailbox="inbox") == ["Your October statement is ready"]
    assert subjects(demo, q="statement", mailbox="done") == ["Your September statement is ready"]


def test_thread_detail(demo: TestClient) -> None:
    [thread] = demo.get("/api/threads", params={"account_id": 1, "q": "dinner"}).json()["threads"]
    detail = demo.get(f"/api/threads/{thread['id']}").json()
    messages = detail["messages"]
    assert [m["sender"]["name"] for m in messages] == ["Daniel Reyes", "Demo", "Daniel Reyes"]
    assert [m["unread"] for m in messages] == [False, False, True]
    assert messages[1]["to"] == [{"name": "Daniel Reyes", "email": "daniel.reyes@example.com"}]
    assert messages[0]["body_text"].startswith("Are you free for dinner")
    assert messages[0]["body_html"] is None
    assert demo.get("/api/threads/9999").status_code == 404


def test_html_mail_with_inline_image_and_cjk_language(demo: TestClient) -> None:
    [brief] = demo.get("/api/threads", params={"account_id": 1, "q": "morning brief"}).json()[
        "threads"
    ]
    [message] = demo.get(f"/api/threads/{brief['id']}").json()["messages"]
    assert 'src="cid:logo@kanso.demo"' in message["body_html"]
    assert "<script" not in message["body_html"]
    assert message["body_text"] is None
    [logo] = message["attachments"]
    assert (logo["content_id"], logo["inline"], logo["mime_type"]) == (
        "logo@kanso.demo",
        True,
        "image/png",
    )
    assert brief["has_attachments"] is False  # inline images don't count
    langs = {
        t["subject"]: t["lang"] for t in demo.get("/api/threads?account_id=1").json()["threads"]
    }
    assert langs["周五的项目会议安排"] == "zh-Hans"
    assert langs["台北辦公室搬遷通知"] == "zh-Hant"
    assert langs["来週の打ち合わせについて"] == "ja"
    assert langs["Q4 planning notes"] is None
    # Big5 and ISO-2022-JP HTML parts decode too.
    for subject, text in (
        ("台北辦公室搬遷通知", "信義區"),
        ("来週の打ち合わせについて", "京都駅前"),
    ):
        [thread] = demo.get("/api/threads", params={"account_id": 1, "q": subject}).json()[
            "threads"
        ]
        [message] = demo.get(f"/api/threads/{thread['id']}").json()["messages"]
        assert text in message["body_html"]
    # Demo attachments are served from demo_seed.DEMO_FILES, cached by the browser for good.
    image = demo.get(f"/api/attachments/{logo['id']}")
    assert image.status_code == 200
    assert image.content == demo_seed.DEMO_FILES["image/png"]
    assert image.headers["content-disposition"].startswith("inline;")
    assert image.headers["cache-control"] == "private, max-age=31536000, immutable"


def test_labels_with_counts(demo: TestClient) -> None:
    labels = {label["name"]: label for label in demo.get("/api/labels?account_id=1").json()}
    assert labels["INBOX"]["type"] == "system"
    assert (labels["Work"]["color"], labels["Work"]["type"]) == ("#6B6B66", "user")
    assert labels["Work"]["threads_total"] == 7
    assert labels["Work"]["threads_unread"] == 3
    assert labels["Personal"]["messages_total"] == 8
    inbox = labels["INBOX"]
    assert inbox["threads_total"] == len(subjects(demo, mailbox="inbox"))
    assert inbox["threads_unread"] == len(subjects(demo, mailbox="inbox", q="is:unread"))
    assert labels["TRASH"]["threads_total"] == 1


def test_sync_status(demo: TestClient) -> None:
    [status] = demo.get("/api/sync").json()
    assert status["state"] == "demo" and status["email"] == "demo@example.com"
    assert status["push"] is False


def test_demo_seed_is_idempotent(demo: TestClient) -> None:
    before = subjects(demo)
    run(demo_seed.seed())
    [account] = demo.get("/api/accounts").json()  # reseeded: a new id
    assert subjects(demo, account_id=account["id"]) == before
    assert len(demo.get("/api/accounts").json()) == 1


def test_disconnecting_cascades_everything(demo: TestClient) -> None:
    account_id = demo.get("/api/accounts").json()[0]["id"]
    assert demo.delete(f"/api/accounts/{account_id}").status_code == 204
    assert run(_counts()) == {"labels": 0, "threads": 0, "messages": 0, "accounts": 0}


async def _counts() -> dict[str, int]:
    async with Session() as db:
        return {
            model.__tablename__: await db.scalar(select(func.count()).select_from(model)) or 0
            for model in (Label, Thread, Message, Account)
        }


def test_event_stream_relays_published_events(fake_redis: Any) -> None:
    async def first_events() -> list[str]:
        stream = events.stream()
        chunks = [await anext(stream)]  # subscribed
        await redis_module.threads_changed(1, {3, 2})
        chunks.append(await anext(stream))
        await stream.aclose()
        return chunks

    chunks = asyncio.run(first_events())
    assert chunks == [
        "retry: 3000\n\n",
        'data: {"type": "threads_changed", "account_id": 1, "thread_ids": [2, 3]}\n\n',
    ]


def test_event_stream_is_sse_and_not_compressed(logged_in: TestClient, fake_redis: Any) -> None:
    with logged_in.stream("GET", "/api/events", headers={"Accept-Encoding": "gzip"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in response.headers
        lines = response.iter_lines()
        assert next(lines) == "retry: 3000"


def test_responses_are_gzipped(demo: TestClient) -> None:
    response = demo.get("/api/threads?account_id=1", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
