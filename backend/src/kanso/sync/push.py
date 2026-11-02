"""A notification is acknowledged only after its sync succeeds; otherwise Pub/Sub redelivers it."""

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx2 as httpx
from google.auth import jwt
from sqlalchemy import select

from kanso.core.settings import settings
from kanso.db.models import Account
from kanso.db.session import Session
from kanso.gmail.client import backoff
from kanso.sync.scheduler import Scheduler

log = logging.getLogger(__name__)

PUBSUB_API = "https://pubsub.googleapis.com/v1/"
# A self-signed service account JWT with this audience needs no token exchange.
AUDIENCE = "https://pubsub.googleapis.com/"
# After this, Pub/Sub redelivers the notification.
SYNC_TIMEOUT = 300.0


class PubSub:
    def __init__(self, http: httpx.AsyncClient, credentials_b64: str, subscription: str) -> None:
        info = json.loads(base64.b64decode(credentials_b64))
        self.credentials = jwt.Credentials.from_service_account_info(info, audience=AUDIENCE)
        self.http = http
        self.subscription = subscription
        self.token_expires = 0.0

    def _headers(self) -> dict[str, str]:
        if time.time() > self.token_expires:
            self.credentials.refresh(None)  # signs locally, no request is made
            self.token_expires = time.time() + 30 * 60  # the JWT is valid for an hour
        token = self.credentials.token
        if isinstance(token, bytes):
            token = token.decode()
        return {"Authorization": f"Bearer {token}"}

    async def _post(self, action: str, body: dict[str, Any], wait: float) -> dict[str, Any]:
        response = await self.http.post(
            f"{PUBSUB_API}{self.subscription}:{action}",
            json=body,
            headers=self._headers(),
            timeout=wait,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def pull(self) -> list[dict[str, Any]]:
        """Long poll."""
        body = await self._post("pull", {"maxMessages": 100}, wait=90)
        return body.get("receivedMessages", [])

    async def ack(self, ack_ids: list[str]) -> None:
        await self._post("acknowledge", {"ackIds": ack_ids}, wait=30)


async def handle(pubsub: PubSub, scheduler: Scheduler, received: dict[str, Any]) -> None:
    ack_id = received["ackId"]
    try:
        data = json.loads(base64.b64decode(received["message"]["data"]))
        email = data["emailAddress"]
    except (ValueError, KeyError, TypeError):
        log.warning("ignoring a Pub/Sub message that is not a Gmail notification")
        await pubsub.ack([ack_id])
        return
    async with Session() as db:
        account = await db.scalar(select(Account).where(Account.email == email))
    loop = scheduler.get(account.id) if account else None
    if account is None or loop is None or account.sync_state == "backfill":
        # Not (or no longer) connected; or mid-backfill, which replays history when it finishes.
        await pubsub.ack([ack_id])
        return
    try:
        async with asyncio.timeout(SYNC_TIMEOUT):
            await loop.synced(loop.request())
    except TimeoutError:
        log.warning("account %s: sync for a push notification did not finish", account.id)
        return
    await pubsub.ack([ack_id])


async def _handle_logged(pubsub: PubSub, scheduler: Scheduler, received: dict[str, Any]) -> None:
    try:
        await handle(pubsub, scheduler, received)
    except Exception:
        log.exception("could not handle a Pub/Sub notification; it will be redelivered")


async def run(http: httpx.AsyncClient, scheduler: Scheduler) -> None:
    """Each notification is handled in its own task, so pulling never waits."""
    pubsub = PubSub(http, settings.google_pubsub_credentials, settings.google_pubsub_subscription)
    tasks: set[asyncio.Task[None]] = set()
    failures = 0
    while True:
        try:
            received = await pubsub.pull()
        except Exception as error:
            failures += 1
            delay = backoff(min(failures, 6))
            log.warning("Pub/Sub pull failed (%s), retrying in %.0fs", error, delay)
            await asyncio.sleep(delay)
            continue
        failures = 0
        for message in received:
            task = asyncio.create_task(_handle_logged(pubsub, scheduler, message))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
