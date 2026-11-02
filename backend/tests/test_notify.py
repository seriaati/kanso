"""Notifications of new inbox mail: Web Push encryption against RFC 8291's example, the VAPID
signature, the subscription API, and what an incremental sync pushes."""

import hashlib
import hmac
import json
import time
from typing import Any

import httpx2 as httpx
import pytest
from conftest import run
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fake_gmail import FakeGmail, make_raw
from fastapi.testclient import TestClient
from sqlalchemy import select

from kanso.db.models import Account, PushSubscription
from kanso.db.session import Session
from kanso.gmail import client, oauth
from kanso.sync import engine, notify
from kanso.sync.notify import b64url, b64url_decode


def _key(private: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(private)), ec.SECP256R1())


def test_encryption_matches_rfc_8291_example() -> None:
    body = notify.encrypt(
        b"When I grow up, I want to be a watermelon",
        p256dh="BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        auth="BTBZMqHH6r4Tts7J_aSIgg",
        server_key=_key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    # Appendix A: the 86-octet header, then the ciphertext.
    assert body == b64url_decode(
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
    ) + b64url_decode(
        "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ"
    )


def test_vapid_header_is_a_valid_es256_jwt() -> None:
    header = notify._vapid_header("https://push.example.net/send/abc?x=1")
    token, key = header.removeprefix("vapid t=").split(", k=")
    assert key == notify.vapid_public_key()
    head, claims, signature = token.split(".")
    assert json.loads(b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(b64url_decode(claims))
    assert body["aud"] == "https://push.example.net"
    assert body["sub"].startswith("mailto:")  # PUBLIC_URL is http:// in the tests
    assert time.time() < body["exp"] <= time.time() + 24 * 3600
    raw = b64url_decode(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(key))
    der = encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:]))
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if bad


class Browser:
    """A subscribed browser: its keys, and the notifications its push service received."""

    def __init__(self, endpoint: str = "https://push.example.net/send/abc") -> None:
        self.endpoint = endpoint
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.status = 201
        self.received: list[dict[str, Any]] = []

    def subscription(self) -> dict[str, Any]:
        public = notify._public_bytes(self.key.public_key())
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": b64url(public), "auth": b64url(self.auth)},
        }

    def decrypt(self, body: bytes) -> dict[str, Any]:
        salt, as_public, ciphertext = body[:16], body[21:86], body[86:]
        assert body[20] == 65
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
        shared = self.key.exchange(ec.ECDH(), peer)
        ua_public = notify._public_bytes(self.key.public_key())

        def mac(k: bytes, d: bytes) -> bytes:
            return hmac.new(k, d, hashlib.sha256).digest()

        ikm = mac(mac(self.auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
        prk = mac(salt, ikm)
        cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
        nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
        plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["authorization"].startswith("vapid t=")
        self.received.append(self.decrypt(request.content))
        return httpx.Response(self.status)


# The API.


@pytest.mark.usefixtures("db")
def test_subscribe_and_unsubscribe(logged_in: TestClient) -> None:
    browser = Browser()
    assert logged_in.get("/api/notifications/key").json() == {
        "public_key": notify.vapid_public_key()
    }
    assert (
        logged_in.put("/api/notifications/subscription", json=browser.subscription()).status_code
        == 204
    )
    # Subscribing again (new keys for the same endpoint) replaces the row.
    browser.key = ec.generate_private_key(ec.SECP256R1())
    assert (
        logged_in.put("/api/notifications/subscription", json=browser.subscription()).status_code
        == 204
    )
    rows = run(_subscriptions())
    assert [(r.endpoint, r.p256dh) for r in rows] == [
        (browser.endpoint, browser.subscription()["keys"]["p256dh"])
    ]
    response = logged_in.request(
        "DELETE", "/api/notifications/subscription", json={"endpoint": browser.endpoint}
    )
    assert response.status_code == 204
    assert run(_subscriptions()) == []


@pytest.mark.usefixtures("db")
def test_subscribe_refuses_bad_subscriptions(logged_in: TestClient) -> None:
    plain_http = Browser("http://push.example.net/send/abc").subscription()
    bad_keys = Browser().subscription() | {"keys": {"p256dh": "AAAA", "auth": "AAAA"}}
    for body in (plain_http, bad_keys):
        assert logged_in.put("/api/notifications/subscription", json=body).status_code == 400
    assert run(_subscriptions()) == []


def test_notifications_need_a_session(client: TestClient) -> None:
    assert client.get("/api/notifications/key").status_code == 401


async def _subscriptions() -> list[PushSubscription]:
    async with Session() as db:
        return list(await db.scalars(select(PushSubscription)))


# What a sync pushes.


async def _setup(browser: Browser) -> None:
    async with Session() as db:
        account = Account(
            email="me@gmail.com", display_name="me", color="#2F4B6E", refresh_token=b"x"
        )
        db.add(account)
        keys = browser.subscription()["keys"]
        db.add(
            PushSubscription(endpoint=browser.endpoint, p256dh=keys["p256dh"], auth=keys["auth"])
        )
        await db.commit()
        oauth._access_tokens[account.id] = ("access-token", time.time() + 3600)


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch, db: None) -> tuple[FakeGmail, Browser]:
    gmail = FakeGmail()
    gmail.add("m1", "t1", ["INBOX", "UNREAD"], make_raw("Old news", "From the backfill"))
    browser = Browser()

    async def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(client, "sleep", no_sleep)
    handle = gmail.handle
    # The push service shares the worker's HTTP client with Gmail.

    def route(request: httpx.Request) -> httpx.Response:
        return (
            browser.handle(request) if request.url.host == "push.example.net" else handle(request)
        )

    monkeypatch.setattr(gmail, "handle", route)
    run(_setup(browser))
    return gmail, browser


def sync(gmail: FakeGmail) -> None:
    async def go() -> None:
        async with gmail.client() as http:
            await engine.sync_account(1, http)

    run(go())


def test_new_unread_inbox_mail_is_pushed(mail: tuple[FakeGmail, Browser]) -> None:
    gmail, browser = mail
    sync(gmail)  # the backfill: history isn't news
    assert browser.received == []
    gmail.add("m2", "t1", ["INBOX", "UNREAD"], make_raw("Re: Old news", "A reply"))
    gmail.add("m3", "t3", ["INBOX"], make_raw("Read elsewhere", "Already read"))
    gmail.add("m4", "t4", ["SENT"], make_raw("Mine", "Sent", "Me <me@gmail.com>"))
    gmail.add("m5", "t5", ["UNREAD"], make_raw("Filtered", "Skipped the inbox"))
    sync(gmail)
    assert browser.received == [
        {"title": "Daniel", "body": "Re: Old news\nA reply", "url": "/inbox/1", "tag": "thread-1"}
    ]


def test_many_new_messages_make_one_notification(mail: tuple[FakeGmail, Browser]) -> None:
    gmail, browser = mail
    sync(gmail)
    for i in range(notify.MAX_SEPARATE + 1):
        gmail.add(f"n{i}", f"tn{i}", ["INBOX", "UNREAD"], make_raw(f"Hello {i}", "..."))
    sync(gmail)
    assert [n["title"] for n in browser.received] == [f"{notify.MAX_SEPARATE + 1} new messages"]


def test_a_gone_subscription_is_removed_and_a_failing_one_kept(
    mail: tuple[FakeGmail, Browser],
) -> None:
    gmail, browser = mail
    sync(gmail)
    browser.status = 500
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("One", "..."))
    sync(gmail)
    assert len(run(_subscriptions())) == 1
    browser.status = 410
    gmail.add("m3", "t3", ["INBOX", "UNREAD"], make_raw("Two", "..."))
    sync(gmail)
    assert run(_subscriptions()) == []
    assert "m3" in gmail.messages  # the sync itself went through
