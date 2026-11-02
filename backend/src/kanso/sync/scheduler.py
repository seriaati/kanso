"""When to sync: one loop per account, woken by push notifications and a safety-net poll."""

import asyncio
import logging

import httpx2 as httpx
from sqlalchemy import select

from kanso.db.models import Account
from kanso.db.session import Session
from kanso.gmail.oauth import ReconnectRequired
from kanso.sync import engine

log = logging.getLogger(__name__)

# Notifications arriving within this window are handled by one sync.
DEBOUNCE_SECONDS = 1.0
# Retry delays after failures: 30 s doubling to 30 min.
RETRY_MIN, RETRY_MAX = 30.0, 1800.0


class AccountLoop:
    """Runs one account's syncs one at a time. request() asks for a sync and synced() waits for it
    to succeed (push notifications are acknowledged only after that)."""

    def __init__(self, account_id: int, http: httpx.AsyncClient, poll_seconds: float) -> None:
        self.account_id = account_id
        self.http = http
        self.poll_seconds = poll_seconds
        self.wake = asyncio.Event()
        self.wake.set()  # sync once on start
        self.requested = 0
        self.completed = 0
        self.done = asyncio.Condition()

    def request(self) -> int:
        self.requested += 1
        self.wake.set()
        return self.requested

    async def synced(self, ticket: int) -> None:
        """Wait until a sync that started after request() returned `ticket` has succeeded."""
        async with self.done:
            await self.done.wait_for(lambda: self.completed >= ticket)

    async def run(self) -> None:
        delay, failures = self.poll_seconds, 0
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
                await asyncio.sleep(DEBOUNCE_SECONDS)
            except TimeoutError:
                pass  # the safety-net poll, or a retry after a failure
            self.wake.clear()
            ticket = self.requested
            try:
                await engine.sync_account(self.account_id, self.http)
            except Exception as error:
                failures += 1
                delay = min(RETRY_MIN * 2 ** (failures - 1), RETRY_MAX)
                if isinstance(error, ReconnectRequired):
                    log.error(
                        "account %s: Google rejected the refresh token, reconnect the account",
                        self.account_id,
                    )
                else:
                    log.exception("account %s: sync failed, retry in %ds", self.account_id, delay)
                try:
                    await engine.record_error(self.account_id, error)
                except Exception:
                    log.exception("account %s: could not record the sync error", self.account_id)
                continue
            delay, failures = self.poll_seconds, 0
            async with self.done:
                self.completed = ticket
                self.done.notify_all()


class Scheduler:
    """Keeps one AccountLoop per connected (non-demo) account."""

    def __init__(self, http: httpx.AsyncClient, poll_seconds: float) -> None:
        self.http = http
        self.poll_seconds = poll_seconds
        self.loops: dict[int, tuple[AccountLoop, asyncio.Task[None]]] = {}

    async def refresh(self) -> None:
        """Start loops for new accounts and stop those of disconnected ones."""
        async with Session() as db:
            ids = set(await db.scalars(select(Account.id).where(Account.demo.is_(False))))
        for account_id, (_, task) in list(self.loops.items()):
            if task.done():  # crashed outside a sync (e.g. the database was down): restart it
                log.error("account %s: sync loop stopped (%r), restarting", account_id, task)
                del self.loops[account_id]
        for account_id in ids - self.loops.keys():
            loop = AccountLoop(account_id, self.http, self.poll_seconds)
            self.loops[account_id] = (loop, asyncio.create_task(loop.run()))
            log.info("account %s: sync started", account_id)
        for account_id in self.loops.keys() - ids:
            self.loops.pop(account_id)[1].cancel()
            log.info("account %s: disconnected, sync stopped", account_id)

    def get(self, account_id: int) -> AccountLoop | None:
        entry = self.loops.get(account_id)
        return entry[0] if entry else None

    def stop(self) -> None:
        for _, task in self.loops.values():
            task.cancel()
