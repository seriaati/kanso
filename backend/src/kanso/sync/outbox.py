"""- Each account's entries go out one at a time, oldest first, so "Done" and its undo can't
  overtake each other.
- An entry is marked sent in the same transaction as the Gmail call; a resend after a crash is
  harmless, as every entry is idempotent.
- A sync can write Gmail's state from before an entry landed, so pending entries are re-applied
  after every sync commit (reapply).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
from sqlalchemy import Select, delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from kanso.db import redis as events
from kanso.db.models import Account, Outbox
from kanso.db.session import Session
from kanso.gmail.client import Gmail, GmailError, NotFound
from kanso.sync import store

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
RETRY_BASE, RETRY_MAX = 5.0, 900.0  # 5 s tripling to 15 min
PERMANENT = {400, 404}  # Gmail won't accept this entry, however often it is sent
FALLBACK_SECONDS = 30.0
KEEP_FINISHED = timedelta(days=7)
BATCH_SIZE = 1000  # messages.batchModify takes up to 1000 ids
TRASH_CONCURRENCY = 8
_LOCK_NAMESPACE = 4  # pg_advisory_xact_lock(namespace, account id)


async def lock_account(db: AsyncSession, account_id: int) -> None:
    """Serializes user actions and the re-apply step on one account, until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :id)"), {"ns": _LOCK_NAMESPACE, "id": account_id}
    )


def enqueue(
    db: AsyncSession,
    account_id: int,
    action: str,
    message_ids: list[str],
    add: list[str],
    remove: list[str],
) -> Outbox:
    entry = Outbox(
        account_id=account_id,
        action=action,
        message_ids=message_ids,
        add_labels=add,
        remove_labels=remove,
    )
    db.add(entry)
    return entry


async def reapply(db: AsyncSession, account_id: int) -> set[int]:
    """Called after the sync commits: under the account lock, an action that raced the sync's
    writes has committed by then, so its entry is seen."""
    await lock_account(db, account_id)
    entries = await db.scalars(
        select(Outbox)
        .where(Outbox.account_id == account_id, Outbox.status == "pending")
        .order_by(Outbox.id)
    )
    thread_ids: set[int] = set()
    for entry in entries:
        thread_ids |= await store.apply_delta(
            db, account_id, entry.message_ids, entry.add_labels, entry.remove_labels
        )
    await store.refresh_threads(db, thread_ids)
    await db.commit()
    return thread_ids


async def undo(db: AsyncSession, entry_ids: list[int]) -> dict[int, set[int]]:
    """A pending entry not being sent right now is cancelled; otherwise the inverse is queued.
    The caller commits."""
    touched: dict[int, set[int]] = {}
    entries = await db.scalars(
        select(Outbox).where(Outbox.id.in_(entry_ids)).order_by(Outbox.id.desc())
    )
    for entry in list(entries):
        await lock_account(db, entry.account_id)
        claimable = await db.scalar(
            select(Outbox.id)
            .where(Outbox.id == entry.id, Outbox.status == "pending")
            .with_for_update(skip_locked=True)
        )
        if claimable is not None:
            await db.delete(entry)
        else:
            inverse = (entry.remove_labels, entry.add_labels)
            enqueue(db, entry.account_id, "undo", entry.message_ids, *inverse)
        thread_ids = await store.apply_delta(
            db, entry.account_id, entry.message_ids, entry.remove_labels, entry.add_labels
        )
        await store.refresh_threads(db, thread_ids)
        touched.setdefault(entry.account_id, set()).update(thread_ids)
    return touched


async def _each(call: Callable[[str], Awaitable[None]], ids: list[str]) -> None:
    semaphore = asyncio.Semaphore(TRASH_CONCURRENCY)

    async def one(gmail_id: str) -> None:
        async with semaphore:
            with suppress(NotFound):
                await call(gmail_id)

    results = await asyncio.gather(*(one(i) for i in ids), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def send(gmail: Gmail, entry: Outbox) -> None:
    if "TRASH" in entry.add_labels:
        await _each(gmail.trash, entry.message_ids)
    if "TRASH" in entry.remove_labels:
        await _each(gmail.untrash, entry.message_ids)
    add = [label for label in entry.add_labels if label != "TRASH"]
    remove = [label for label in entry.remove_labels if label != "TRASH"]
    if add or remove:
        for start in range(0, len(entry.message_ids), BATCH_SIZE):
            await gmail.batch_modify(entry.message_ids[start : start + BATCH_SIZE], add, remove)


def _due_head() -> Select[Outbox]:
    """The oldest pending entry of an account, if it is due."""
    older = aliased(Outbox)
    return (
        select(Outbox)
        .where(
            Outbox.status == "pending",
            Outbox.next_attempt_at <= func.now(),
            ~exists().where(
                older.account_id == Outbox.account_id,
                older.status == "pending",
                older.id < Outbox.id,
            ),
        )
        .order_by(Outbox.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Outbox)
    )


async def process_one(http: httpx.AsyncClient) -> bool:
    async with Session() as db:
        entry = await db.scalar(_due_head())
        if entry is None:
            return False
        account = await db.get(Account, entry.account_id)
        assert account is not None  # entries cascade with their account
        gmail = Gmail(account, http)
        entry.attempts += 1
        try:
            if not account.demo:  # local only
                await send(gmail, entry)
        except Exception as error:
            entry.last_error = (str(error) or type(error).__name__)[:500]
            permanent = isinstance(error, GmailError) and error.status in PERMANENT
            if permanent or entry.attempts >= MAX_ATTEMPTS:
                entry.status = "failed"
                entry.finished_at = datetime.now(UTC)
                log.error(
                    "outbox %s (%s, account %s) failed for good after %s attempts: %s",
                    entry.id,
                    entry.action,
                    entry.account_id,
                    entry.attempts,
                    entry.last_error,
                )
            else:
                delay = min(RETRY_BASE * 3 ** (entry.attempts - 1), RETRY_MAX)
                entry.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                log.warning(
                    "outbox %s (%s) failed (%s), retry %s/%s in %.0fs",
                    entry.id,
                    entry.action,
                    entry.last_error,
                    entry.attempts + 1,
                    MAX_ATTEMPTS,
                    delay,
                )
            await db.commit()
            if entry.status == "failed":
                await _reconcile(db, gmail, entry)
            return True
        entry.status = "sent"
        entry.finished_at = datetime.now(UTC)
        entry.last_error = None
        await db.commit()
        log.info(
            "outbox %s: %s on %s message(s) of account %s %s",
            entry.id,
            entry.action,
            len(entry.message_ids),
            entry.account_id,
            "applied (demo account, local only)" if account.demo else "sent to Gmail",
        )
        return True


async def _reconcile(db: AsyncSession, gmail: Gmail, entry: Outbox) -> None:
    """So kanso doesn't show a change Gmail never got. Best effort: a full sync also fixes it."""
    account_id = entry.account_id
    thread_ids: set[int] = set()
    try:
        labels = await store.label_map(db, account_id)
        for gmail_id in entry.message_ids:
            try:
                meta = await gmail.message_labels(gmail_id)
            except NotFound:
                thread_ids |= await store.delete_messages(db, account_id, [gmail_id])
                continue
            thread_id = await store.set_labels(
                db, account_id, gmail_id, meta.get("labelIds", []), labels
            )
            if thread_id is not None:
                thread_ids.add(thread_id)
        await store.refresh_threads(db, thread_ids)
        await db.commit()
        thread_ids |= await reapply(db, account_id)
    except Exception:
        log.exception("outbox %s: could not restore Gmail's labels", entry.id)
        await db.rollback()
    with suppress(Exception):
        await events.threads_changed(account_id, thread_ids)
        await events.publish(
            {"type": "outbox_failed", "account_id": account_id, "action": entry.action}
        )


async def _seconds_until_due() -> float:
    async with Session() as db:
        await db.execute(
            delete(Outbox).where(
                Outbox.status != "pending", Outbox.finished_at < datetime.now(UTC) - KEEP_FINISHED
            )
        )
        await db.commit()
        due = await db.scalar(
            select(func.min(Outbox.next_attempt_at)).where(Outbox.status == "pending")
        )
    if due is None:
        return FALLBACK_SECONDS
    return min(max((due - datetime.now(UTC)).total_seconds(), 0.0), FALLBACK_SECONDS)


async def _listen(wake: asyncio.Event) -> None:
    while True:
        try:
            async with events.redis.pubsub() as pubsub:
                await pubsub.subscribe(events.OUTBOX_CHANNEL)
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=60)
                    if message is not None:
                        wake.set()
        except Exception as error:
            log.warning(
                "outbox: Redis wake-ups unavailable (%s), checking every %.0fs",
                error,
                FALLBACK_SECONDS,
            )
            await asyncio.sleep(FALLBACK_SECONDS)


async def run(http: httpx.AsyncClient) -> None:
    wake = asyncio.Event()
    listener = asyncio.create_task(_listen(wake))
    try:
        while True:
            wake.clear()
            try:
                while await process_one(http):
                    pass
                delay = await _seconds_until_due()
            except Exception:
                log.exception("outbox: processing failed")
                delay = RETRY_BASE
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=delay)
    finally:
        listener.cancel()
