"""New-mail notifications: Web Push for browsers and FCM for the Android app, which also hears
when a thread leaves the unread inbox, to remove its notification.

The VAPID key (RFC 8292) is derived from SECRET_KEY: changing it means turning notifications
on again in each browser."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Iterable
from functools import cache
from itertools import batched
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from google.auth import crypt, jwt
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.api.threads import _images
from kanso.core.settings import settings
from kanso.db.models import (
    Account,
    FcmToken,
    Label,
    Message,
    MessageLabel,
    PushSubscription,
    Thread,
)
from kanso.db.session import Session
from kanso.gmail import client as gmail_client

log = logging.getLogger(__name__)

# More new messages than this in one sync make one summary notification.
MAX_SEPARATE = 5
# How long a push service keeps a notification for an offline device. Clears keep as long: one
# that expired first would leave the phone showing mail that was read meanwhile.
TTL_SECONDS = 24 * 3600
# Thread ids in one "clear" message, well under FCM's 4 kB of data.
CLEAR_BATCH = 200
# Ids per query, under Postgres's limit on parameters.
QUERY_CHUNK = 1000
# The Android app's notifications go through FCM, sent as the Pub/Sub service account.
FCM_API = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# P-256's group order: a private key is an integer in [1, n - 1].
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


@cache
def vapid_key() -> ec.EllipticCurvePrivateKey:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"kanso web push vapid")
    seed = int.from_bytes(hkdf.derive(settings.secret_key.encode()))
    return ec.derive_private_key(seed % (P256_ORDER - 1) + 1, ec.SECP256R1())


def vapid_public_key() -> str:
    """The applicationServerKey the browser subscribes with."""
    return b64url(_public_bytes(vapid_key().public_key()))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    server_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The aes128gcm body of RFC 8291 (one record). Tests pass the RFC's key and salt."""
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    ua_public = b64url_decode(p256dh)
    as_public = _public_bytes(server_key.public_key())
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), peer)
    # HKDF with SHA-256, written out: each output here is at most one hash long.
    prk_key = _hmac(b64url_decode(auth), shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02: the last record
    header = salt + (4096).to_bytes(4) + bytes([len(as_public)]) + as_public
    return header + ciphertext


def _vapid_header(endpoint: str) -> str:
    url = urlsplit(endpoint)
    # Apple refuses a subject that isn't a real https: URL or mailto: address.
    subject = (
        settings.public_url
        if settings.public_url.startswith("https://")
        else "mailto:kanso@localhost"
    )
    claims = {
        "aud": f"{url.scheme}://{url.netloc}",
        "exp": int(time.time()) + 12 * 3600,
        "sub": subject,
    }
    signing_input = (
        b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        + "."
        + b64url(json.dumps(claims).encode())
    )
    der = vapid_key().sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    jwt = f"{signing_input}.{b64url(r.to_bytes(32) + s.to_bytes(32))}"
    return f"vapid t={jwt}, k={vapid_public_key()}"


async def send(
    http: httpx.AsyncClient, subscription: PushSubscription, payload: dict[str, Any]
) -> bool:
    """False when the subscription is gone, so it can be deleted."""
    body = encrypt(json.dumps(payload).encode(), subscription.p256dh, subscription.auth)
    response = await http.post(
        subscription.endpoint,
        content=body,
        headers={
            "Authorization": _vapid_header(subscription.endpoint),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS),
            "Urgency": "high",
        },
        timeout=15,
    )
    if response.status_code in (404, 410):
        return False
    if response.status_code >= 400:
        log.warning(
            "push to %s failed: %s %s",
            urlsplit(subscription.endpoint).netloc,
            response.status_code,
            response.text[:200],
        )
    return True


class Fcm:
    """FCM HTTP v1, with an access token from the service account in GOOGLE_PUBSUB_CREDENTIALS."""

    _token: tuple[str, float] = ("", 0.0)  # the access token and when to get a new one

    def __init__(self, http: httpx.AsyncClient, credentials_b64: str) -> None:
        self.http = http
        self.info = json.loads(base64.b64decode(credentials_b64))

    async def access_token(self) -> str:
        token, renew_at = Fcm._token
        if time.time() < renew_at:
            return token
        now = int(time.time())
        claims = {
            "iss": self.info["client_email"],
            "scope": FCM_SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        assertion = jwt.encode(crypt.RSASigner.from_service_account_info(self.info), claims)
        response = await self.http.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion.decode(),
            },
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        Fcm._token = (body["access_token"], time.time() + body.get("expires_in", 3600) - 300)
        return body["access_token"]

    async def send(
        self, token: str, data: dict[str, str], priority: Literal["high", "normal"] = "high"
    ) -> bool:
        """False when the app is gone (uninstalled, or its token replaced), so it can be deleted.
        Normal priority for what shows nothing: FCM demotes high-priority messages that don't."""
        response = await self.http.post(
            FCM_API.format(project=self.info["project_id"]),
            json={
                "message": {
                    "token": token,
                    # Data only: the app builds the notification itself.
                    "data": data,
                    "android": {"priority": priority, "ttl": f"{TTL_SECONDS}s"},
                }
            },
            headers={"Authorization": f"Bearer {await self.access_token()}"},
            timeout=15,
        )
        if response.status_code == 404 and "UNREGISTERED" in response.text:
            return False
        if response.status_code == 401:
            Fcm._token = ("", 0.0)  # get a new one next time
        if response.status_code >= 400:
            log.warning("FCM send failed: %s %s", response.status_code, response.text[:200])
        return True


def _has_label(gmail_id: str) -> Any:
    return exists().where(
        MessageLabel.message_id == Message.id,
        MessageLabel.label_id == Label.id,
        Label.gmail_id == gmail_id,
    )


def _clip(text: str, length: int) -> str:
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


async def new_mail(
    db: AsyncSession, http: httpx.AsyncClient, account_id: int, gmail_ids: set[str]
) -> None:
    """Never raises: a failed push mustn't fail a sync."""
    try:
        if not gmail_ids:
            return
        subscriptions = list(await db.scalars(select(PushSubscription)))
        # FCM needs the service account; without it the app gets no notifications.
        tokens = (
            list(await db.scalars(select(FcmToken))) if settings.google_pubsub_credentials else []
        )
        if not subscriptions and not tokens:
            return
        rows = (
            await db.execute(
                select(
                    Message.id,
                    Message.thread_id,
                    Message.from_name,
                    Message.from_email,
                    Message.subject,
                    Message.snippet,
                )
                .where(
                    Message.account_id == account_id,
                    Message.gmail_id.in_(gmail_ids),
                    _has_label("INBOX"),
                    _has_label("UNREAD"),
                    ~_has_label("SENT"),
                    ~_has_label("DRAFT"),
                )
                .order_by(Message.internal_date)
            )
        ).all()
        if not rows:
            return
        # payloads for Web Push (public/push-sw.js), messages for the Android app (FCM data,
        # strings only, drawn by MessagingService.kt). "type" lets the app tell them apart.
        payloads: list[dict[str, Any]] = []
        messages: list[dict[str, str]] = []
        if len(rows) > MAX_SEPARATE:
            title = f"{len(rows)} new messages"
            url = f"/inbox?account={account_id}"
            payloads.append({"title": title, "body": "", "url": url, "tag": "kanso-new-mail"})
            messages.append({"type": "summary", "title": title, "url": url})
        else:
            # Expanded, Android shows the body's lines as they are: the subject, then the snippet.
            # Collapsed, it joins them into one line.
            images = await _images(db, {(account_id, r.from_email.lower()) for r in rows})
            for r in rows:
                sender = r.from_name or r.from_email
                subject = _clip(r.subject or "(no subject)", 200)
                snippet = _clip(r.snippet, 200)
                url = f"/inbox/{r.thread_id}?account={account_id}"
                image = images[account_id, r.from_email.lower()]
                payloads.append(
                    {
                        "title": _clip(sender, 100),
                        "body": "\n".join(filter(None, [subject, snippet])),
                        "url": url,
                        "tag": f"thread-{r.thread_id}",  # a newer message replaces its thread's
                        # For the Done and Delete buttons, and the sender's picture (push-sw.js).
                        "thread_id": r.thread_id,
                        "sender": sender,
                        "image": image,
                    }
                )
                messages.append(
                    {
                        "type": "mail",
                        "thread_id": str(r.thread_id),
                        "message_id": str(r.id),  # what the Reply button answers
                        "sender": _clip(sender, 100),
                        "subject": subject,
                        "snippet": snippet,
                        "image": image or "",
                        "url": url,
                    }
                )
        await _web_push(db, http, subscriptions, payloads)
        if tokens:
            # One channel and one group per account: the app names them after its address.
            account = await db.get(Account, account_id)
            common = {"account_id": str(account_id), "account": account.email if account else ""}
            await _fcm(db, http, tokens, [m | common for m in messages])
    except Exception:
        log.exception("could not send new mail notifications")


# A thread in the inbox's view (api/threads.py) with unread mail: it may have a notification.
_UNREAD_IN_INBOX = (Thread.in_inbox, Thread.unread, ~Thread.in_trash, ~Thread.in_spam)


async def unread_threads(db: AsyncSession, thread_ids: Iterable[int]) -> dict[int, int]:
    """Those of the threads that are unread in the inbox: thread id -> account id. Taken before a
    change, for clear() after it."""
    found: dict[int, int] = {}
    for chunk in batched(set(thread_ids), QUERY_CHUNK, strict=False):
        rows = await db.execute(
            select(Thread.id, Thread.account_id).where(Thread.id.in_(chunk), *_UNREAD_IN_INBOX)
        )
        found.update({thread_id: account_id for thread_id, account_id in rows})
    return found


async def unread_threads_of(
    db: AsyncSession, account_id: int, gmail_ids: Iterable[str]
) -> dict[int, int]:
    """unread_threads() of these messages' threads."""
    thread_ids: set[int] = set()
    for chunk in batched(set(gmail_ids), QUERY_CHUNK, strict=False):
        thread_ids.update(
            await db.scalars(
                select(Message.thread_id).where(
                    Message.account_id == account_id, Message.gmail_id.in_(chunk)
                )
            )
        )
    return await unread_threads(db, thread_ids)


async def clear(db: AsyncSession, http: httpx.AsyncClient, before: dict[int, int]) -> None:
    """After a change is committed: the threads of `before` (unread_threads() from before it)
    that are no longer unread in the inbox lose their notifications in the app. FCM only: Web
    Push can't deliver anything silently. Never raises: it mustn't fail a sync or an action."""
    try:
        if not before or not settings.google_pubsub_credentials:
            return
        tokens = list(await db.scalars(select(FcmToken)))
        if not tokens:
            return
        still = await unread_threads(db, before)
        handled: dict[int, list[int]] = {}
        for thread_id, account_id in sorted(before.items()):
            if thread_id not in still:
                handled.setdefault(account_id, []).append(thread_id)
        messages: list[dict[str, str]] = []
        for account_id, thread_ids in handled.items():
            common = {"type": "clear", "account_id": str(account_id)}
            left = await db.scalar(
                select(exists().where(Thread.account_id == account_id, *_UNREAD_IN_INBOX))
            )
            if not left:
                # No ids: nothing unread is left, so every notification of the account goes,
                # a burst's summary too.
                messages.append(common | {"thread_ids": ""})
                continue
            for batch in batched(thread_ids, CLEAR_BATCH, strict=False):
                messages.append(common | {"thread_ids": ",".join(map(str, batch))})
        if messages:
            await _fcm(db, http, tokens, messages, priority="normal")
    except Exception:
        log.exception("could not clear notifications")


async def clear_soon(before: dict[int, int]) -> None:
    """clear() with a session and HTTP client of its own, for a request's background task, so
    the response doesn't wait for FCM."""
    if before and settings.google_pubsub_credentials:
        async with Session() as db, gmail_client.http_client() as http:
            await clear(db, http, before)


async def _web_push(
    db: AsyncSession,
    http: httpx.AsyncClient,
    subscriptions: list[PushSubscription],
    payloads: list[dict[str, Any]],
) -> None:
    async def deliver(subscription: PushSubscription) -> PushSubscription | None:
        try:
            for payload in payloads:
                if not await send(http, subscription, payload):
                    return subscription
        except (httpx.HTTPError, ValueError) as error:  # ValueError: a malformed key
            log.warning("push failed: %r", error)
        return None

    gone = [s for s in await asyncio.gather(*map(deliver, subscriptions)) if s]
    if gone:
        await db.execute(
            delete(PushSubscription).where(PushSubscription.id.in_([s.id for s in gone]))
        )
        await db.commit()


async def _fcm(
    db: AsyncSession,
    http: httpx.AsyncClient,
    tokens: list[FcmToken],
    messages: list[dict[str, str]],
    priority: Literal["high", "normal"] = "high",
) -> None:
    try:
        fcm = Fcm(http, settings.google_pubsub_credentials)
        await fcm.access_token()  # once, before the sends share it
    except (httpx.HTTPError, ValueError, KeyError) as error:  # e.g. a malformed key
        log.warning("FCM: could not get an access token: %r", error)
        return

    async def deliver(token: FcmToken) -> FcmToken | None:
        try:
            for message in messages:
                if not await fcm.send(token.token, message, priority):
                    return token
        except httpx.HTTPError as error:
            log.warning("FCM failed: %r", error)
        return None

    gone = [t for t in await asyncio.gather(*map(deliver, tokens)) if t]
    if gone:
        await db.execute(delete(FcmToken).where(FcmToken.id.in_([t.id for t in gone])))
        await db.commit()
