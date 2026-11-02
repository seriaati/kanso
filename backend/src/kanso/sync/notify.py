"""The VAPID key (RFC 8292) is derived from SECRET_KEY: changing it means turning notifications
on again in each browser."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from functools import cache
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.core.settings import settings
from kanso.db.models import Label, Message, MessageLabel, PushSubscription

log = logging.getLogger(__name__)

# More new messages than this in one sync make one summary notification.
MAX_SEPARATE = 5
# How long a push service keeps a notification for an offline device.
TTL_SECONDS = 24 * 3600
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
        if not subscriptions:
            return
        rows = (
            await db.execute(
                select(
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
        if len(rows) > MAX_SEPARATE:
            payloads = [
                {
                    "title": f"{len(rows)} new messages",
                    "body": "",
                    "url": "/inbox",
                    "tag": "kanso-new-mail",
                }
            ]
        else:
            payloads = [
                {
                    "title": _clip(r.from_name or r.from_email, 100),
                    "body": "\n".join(
                        filter(
                            None, [_clip(r.subject or "(no subject)", 200), _clip(r.snippet, 200)]
                        )
                    ),
                    "url": f"/inbox/{r.thread_id}",
                    "tag": f"thread-{r.thread_id}",  # a newer message replaces its thread's
                }
                for r in rows
            ]

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
    except Exception:
        log.exception("could not send new mail notifications")
