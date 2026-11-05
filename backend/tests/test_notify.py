"""Web Push encryption is checked against the example in RFC 8291, Appendix A."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest
from conftest import run
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fake_gmail import FakeGmail, make_raw
from fastapi.testclient import TestClient
from google.auth import jwt
from sqlalchemy import select

from kanso.core.settings import settings
from kanso.db.models import Account, FcmToken, PushSubscription, Thread
from kanso.db.session import Session
from kanso.gmail import client, oauth
from kanso.sync import engine, notify, outbox
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
    """A subscribed browser and its push service."""

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
        {
            "title": "Daniel",
            "body": "Re: Old news\nA reply",
            "url": "/inbox/1?account=1",
            "tag": "thread-1",
            "thread_id": 1,
            "sender": "Daniel",
            "image": "/api/logos/example.com",
        }
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


class Phone:
    """The Android app's token, FCM, and Google's OAuth token endpoint."""

    PROJECT = "kanso-test"
    EMAIL = "kanso-pubsub@kanso-test.iam.gserviceaccount.com"

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.token = "fcm-token-1"
        self.status = 200
        self.received: list[dict[str, str]] = []
        self.token_requests = 0

    def credentials(self) -> str:
        """GOOGLE_PUBSUB_CREDENTIALS: the service account's JSON key, base64-encoded."""
        pem = self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        info = {
            "type": "service_account",
            "project_id": self.PROJECT,
            "private_key_id": "key-1",
            "private_key": pem.decode(),
            "client_email": self.EMAIL,
            "token_uri": notify.TOKEN_URL,
        }
        return base64.b64encode(json.dumps(info).encode()).decode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            public = self.key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            # Checks the signature too.
            claims = jwt.decode(form["assertion"][0], certs=public, audience=notify.TOKEN_URL)
            assert (claims["iss"], claims["scope"]) == (self.EMAIL, notify.FCM_SCOPE)
            self.token_requests += 1
            return httpx.Response(200, json={"access_token": "fcm-access", "expires_in": 3599})
        assert request.url.path == f"/v1/projects/{self.PROJECT}/messages:send"
        assert request.headers["authorization"] == "Bearer fcm-access"
        message = json.loads(request.content)["message"]
        assert message["token"] == self.token
        # A clear shows nothing, so it isn't urgent.
        priority = "normal" if message["data"]["type"] == "clear" else "high"
        assert message["android"] == {"priority": priority, "ttl": "86400s"}
        assert all(isinstance(value, str) for value in message["data"].values())
        if self.status == 404:
            error = {"errorCode": "UNREGISTERED"}
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [error]}})
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": {"status": "UNAVAILABLE"}})
        self.received.append(message["data"])
        return httpx.Response(200, json={"name": f"projects/{self.PROJECT}/messages/1"})


async def _tokens() -> list[str]:
    async with Session() as db:
        return list(await db.scalars(select(FcmToken.token)))


async def _add_token(token: str) -> None:
    async with Session() as db:
        db.add(FcmToken(token=token))
        await db.commit()


@pytest.fixture
def phone(mail: tuple[FakeGmail, Browser], monkeypatch: pytest.MonkeyPatch) -> Phone:
    gmail, _ = mail
    phone = Phone()
    monkeypatch.setattr(settings, "google_pubsub_credentials", phone.credentials())
    monkeypatch.setattr(notify.Fcm, "_token", ("", 0.0))
    route = gmail.handle

    def handle(request: httpx.Request) -> httpx.Response:
        google = request.url.host in ("oauth2.googleapis.com", "fcm.googleapis.com")
        return phone.handle(request) if google else route(request)

    monkeypatch.setattr(gmail, "handle", handle)
    run(_add_token(phone.token))
    return phone


@pytest.mark.usefixtures("db")
def test_register_and_unregister_the_app(logged_in: TestClient) -> None:
    for _ in range(2):  # registering again changes nothing
        response = logged_in.put("/api/notifications/fcm", json={"token": "fcm-token-1"})
        assert response.status_code == 204
    assert run(_tokens()) == ["fcm-token-1"]
    assert logged_in.put("/api/notifications/fcm", json={"token": ""}).status_code == 422
    response = logged_in.request("DELETE", "/api/notifications/fcm", json={"token": "fcm-token-1"})
    assert response.status_code == 204
    assert run(_tokens()) == []


def _google_services(*packages: str) -> str:
    """FIREBASE_ANDROID_CONFIG: a google-services.json with an app for each package, base64."""
    clients = [
        {
            "client_info": {
                "mobilesdk_app_id": f"1:123456789012:android:{i}abc",
                "android_client_info": {"package_name": package},
            },
            "oauth_client": [],
            "api_key": [{"current_key": f"AIza-key-{i}"}],
            "services": {"appinvite_service": {"other_platform_oauth_client": []}},
        }
        for i, package in enumerate(packages)
    ]
    config = {
        "project_info": {"project_number": "123456789012", "project_id": "kanso-test"},
        "client": clients,
        "configuration_version": "1",
    }
    return base64.b64encode(json.dumps(config).encode()).decode()


def test_firebase_settings_for_the_app(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "google_pubsub_credentials", "a service account key")
    monkeypatch.setattr(
        settings, "firebase_android_config", _google_services("x.other", "app.kanso")
    )
    response = logged_in.get("/api/notifications/firebase")
    assert response.status_code == 200
    assert response.json() == {
        "project_id": "kanso-test",
        "sender_id": "123456789012",
        "app_id": "1:123456789012:android:1abc",
        "api_key": "AIza-key-1",
    }


@pytest.mark.parametrize(
    ("config", "credentials"),
    [
        ("", True),  # not set up
        (_google_services("app.kanso"), False),  # nothing to send with
        (_google_services("x.other"), True),  # no app.kanso in it
        ("not base64 json", True),
    ],
)
def test_no_firebase_settings_for_the_app(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch, config: str, credentials: bool
) -> None:
    monkeypatch.setattr(settings, "google_pubsub_credentials", "a key" if credentials else "")
    monkeypatch.setattr(settings, "firebase_android_config", config)
    assert logged_in.get("/api/notifications/firebase").status_code == 404


def test_new_mail_goes_to_the_app_and_the_browser(
    mail: tuple[FakeGmail, Browser], phone: Phone
) -> None:
    gmail, browser = mail
    sync(gmail)
    gmail.add("m2", "t1", ["INBOX", "UNREAD"], make_raw("Re: Old news", "A reply"))
    sync(gmail)
    assert phone.received == [
        {
            "type": "mail",
            "thread_id": "1",
            "message_id": "2",
            "sender": "Daniel",
            "subject": "Re: Old news",
            "snippet": "A reply",
            "image": "/api/logos/example.com",
            "url": "/inbox/1?account=1",
            "account_id": "1",
            "account": "me@gmail.com",
        }
    ]
    assert [n["title"] for n in browser.received] == ["Daniel"]  # Web Push as before
    gmail.add("m3", "t3", ["INBOX", "UNREAD"], make_raw("Another", "..."))
    sync(gmail)
    assert len(phone.received) == 2
    assert phone.token_requests == 1  # the access token is reused


def test_many_new_messages_make_one_app_notification(
    mail: tuple[FakeGmail, Browser], phone: Phone
) -> None:
    gmail, _ = mail
    sync(gmail)
    for i in range(notify.MAX_SEPARATE + 1):
        gmail.add(f"n{i}", f"tn{i}", ["INBOX", "UNREAD"], make_raw(f"Hello {i}", "..."))
    sync(gmail)
    assert phone.received == [
        {
            "type": "summary",
            "title": f"{notify.MAX_SEPARATE + 1} new messages",
            "url": "/inbox?account=1",
            "account_id": "1",
            "account": "me@gmail.com",
        }
    ]


def test_an_unregistered_app_is_removed_and_a_failing_one_kept(
    mail: tuple[FakeGmail, Browser], phone: Phone
) -> None:
    gmail, _ = mail
    sync(gmail)
    phone.status = 503
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("One", "..."))
    sync(gmail)
    assert run(_tokens()) == [phone.token]
    phone.status = 404
    gmail.add("m3", "t3", ["INBOX", "UNREAD"], make_raw("Two", "..."))
    sync(gmail)
    assert run(_tokens()) == []
    assert len(run(_subscriptions())) == 1  # the browser keeps its notifications


def test_no_app_notifications_without_the_service_account(
    mail: tuple[FakeGmail, Browser], phone: Phone, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail, browser = mail
    monkeypatch.setattr(settings, "google_pubsub_credentials", "")
    sync(gmail)
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("One", "..."))
    sync(gmail)
    assert (phone.received, phone.token_requests) == ([], 0)
    assert len(browser.received) == 1
    assert run(_tokens()) == [phone.token]


def test_a_bad_service_account_does_not_fail_the_sync(
    mail: tuple[FakeGmail, Browser], phone: Phone, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail, browser = mail
    monkeypatch.setattr(settings, "google_pubsub_credentials", "bm90IGpzb24=")  # "not json"
    sync(gmail)
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("One", "..."))
    sync(gmail)
    assert phone.received == []
    assert len(browser.received) == 1  # the sync and Web Push went on


async def _thread_ids() -> dict[str, int]:
    async with Session() as db:
        rows = await db.execute(select(Thread.gmail_id, Thread.id))
        return {gmail_id: thread_id for gmail_id, thread_id in rows}


def _clear(*thread_ids: int) -> dict[str, str]:
    return {"type": "clear", "account_id": "1", "thread_ids": ",".join(map(str, thread_ids))}


def test_mail_read_or_archived_elsewhere_clears_its_notification(
    mail: tuple[FakeGmail, Browser], phone: Phone
) -> None:
    gmail, browser = mail
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("Two", "..."))
    gmail.add("m3", "t3", ["INBOX"], make_raw("Three", "Read already"))
    sync(gmail)
    ids = run(_thread_ids())
    gmail.relabel("m1", remove=("UNREAD",))  # read in Gmail
    gmail.relabel("m3", remove=("INBOX",))  # archived, but it was read: no notification
    sync(gmail)
    assert phone.received == [_clear(ids["t1"])]
    gmail.relabel("m2", add=("STARRED",))  # still unread in the inbox
    sync(gmail)
    assert len(phone.received) == 1
    gmail.relabel("m2", remove=("INBOX",))
    sync(gmail)
    # Nothing unread is left: everything of the account goes, a burst's summary too.
    assert phone.received[1:] == [_clear()]
    assert browser.received == []  # Web Push can't clear silently


def test_mail_deleted_elsewhere_clears_its_notification(
    mail: tuple[FakeGmail, Browser], phone: Phone
) -> None:
    gmail, _ = mail
    sync(gmail)
    gmail.delete("m1")
    sync(gmail)
    assert phone.received == [_clear()]


def test_done_in_kanso_clears_the_notification(
    mail: tuple[FakeGmail, Browser],
    phone: Phone,
    logged_in: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail, _ = mail
    monkeypatch.setattr(client, "http_client", gmail.client)
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("Two", "..."))
    gmail.add("m3", "t3", ["INBOX"], make_raw("Three", "Read already"))
    sync(gmail)
    ids = run(_thread_ids())

    def act(action: str, *threads: str) -> None:
        body = {"action": action, "thread_ids": [ids[t] for t in threads]}
        assert logged_in.post("/api/threads/actions", json=body).status_code == 200

    act("star", "t1")  # still unread in the inbox
    act("done", "t3")  # read: it had no notification
    assert phone.received == []
    act("done", "t1")
    assert phone.received == [_clear(ids["t1"])]

    async def write_back() -> None:
        async with gmail.client() as http:
            while await outbox.process_one(http):
                pass

    run(write_back())
    sync(gmail)  # Gmail's echo of Kanso's own change
    assert len(phone.received) == 1
    act("read", "t2")
    assert phone.received[1:] == [_clear()]


def test_no_clears_without_the_service_account_or_the_app(
    mail: tuple[FakeGmail, Browser],
    phone: Phone,
    logged_in: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail, _ = mail
    monkeypatch.setattr(client, "http_client", gmail.client)
    gmail.add("m2", "t2", ["INBOX", "UNREAD"], make_raw("Two", "..."))
    sync(gmail)
    ids = run(_thread_ids())
    monkeypatch.setattr(settings, "google_pubsub_credentials", "")
    gmail.relabel("m1", remove=("UNREAD",))
    sync(gmail)
    body = {"action": "done", "thread_ids": [ids["t2"]]}
    assert logged_in.post("/api/threads/actions", json=body).status_code == 200
    assert (phone.received, phone.token_requests) == ([], 0)

    monkeypatch.setattr(settings, "google_pubsub_credentials", phone.credentials())
    run(_remove_tokens())
    assert (
        logged_in.post("/api/threads/actions", json=body | {"action": "inbox"}).status_code == 200
    )
    assert logged_in.post("/api/threads/actions", json=body).status_code == 200
    assert (phone.received, phone.token_requests) == ([], 0)


async def _remove_tokens() -> None:
    async with Session() as db:
        for token in await db.scalars(select(FcmToken)):
            await db.delete(token)
        await db.commit()
