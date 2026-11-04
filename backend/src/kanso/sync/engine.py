"""Outbox changes are re-applied after each commit, so a sync never undoes a kanso action that
Gmail hasn't received yet."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import labeling
from kanso.core.settings import settings
from kanso.db import redis as events
from kanso.db.models import Account, BlockedSender, Label, Message
from kanso.db.session import Session
from kanso.gmail import mime
from kanso.gmail.client import Gmail, GmailError, NotFound
from kanso.sync import notify, outbox, store

log = logging.getLogger(__name__)

# Only bounds how many are in flight; the Gmail client paces requests to the quota.
FETCH_CONCURRENCY = 8
BACKFILL_CHUNK = 100
LIST_ID_BATCH = 1000
# Gmail asks to renew users.watch daily; it expires after 7 days.
WATCH_RENEW_BEFORE = timedelta(days=6)


async def sync_account(account_id: int, http: httpx.AsyncClient) -> None:
    async with Session() as db:
        account = await db.get(Account, account_id)
        if account is None or account.demo:
            return
        gmail = Gmail(account, http)
        if account.sync_state == "backfill":
            await backfill(db, gmail, account)
        if settings.pubsub_configured:
            await renew_watch(db, gmail, account)
        await incremental(db, gmail, account)
        await fill_list_ids(db, gmail, account.id)


async def _now(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.now()))
    assert now is not None
    return now


async def backfill(db: AsyncSession, gmail: Gmail, account: Account) -> None:
    """Also a full resync: known messages only get their labels refreshed, and messages the pass
    didn't see are removed at the end."""
    if account.backfill_started_at is None:
        profile = await gmail.profile()
        # Changes from here on are replayed from history once the backfill is done.
        account.history_id = int(profile["historyId"])
        account.backfill_total = profile.get("messagesTotal")
        account.backfill_done = 0
        account.backfill_page_token = None
        account.backfill_started_at = await _now(db)
        await db.commit()
        log.info("account %s: backfill started (~%s messages)", account.id, account.backfill_total)
    await events.sync_changed(account.id)
    started = account.backfill_started_at
    assert started is not None
    labels = await store.save_labels(db, account.id, await gmail.labels())
    await db.commit()

    while True:
        try:
            page = await gmail.list_messages(account.backfill_page_token)
        except GmailError as error:
            if error.status != 400 or not account.backfill_page_token:
                raise
            # An expired page token: start the listing over. Already-synced messages are skipped.
            log.warning("account %s: backfill page token rejected, relisting", account.id)
            account.backfill_page_token = None
            continue
        ids = [m["id"] for m in page.get("messages", [])]
        thread_ids: set[int] = set()
        # Commit in chunks, so a fetch that keeps failing doesn't lose the chunks before it.
        for i in range(0, len(ids), BACKFILL_CHUNK):
            chunk_threads, _ = await import_messages(
                db, gmail, account.id, ids[i : i + BACKFILL_CHUNK], labels, started
            )
            account.sync_error = None
            await db.commit()
            await events.threads_changed(account.id, chunk_threads)
            thread_ids |= chunk_threads
        account.backfill_done += len(ids)
        account.backfill_page_token = page.get("nextPageToken")
        if account.backfill_page_token is None:
            thread_ids |= await store.delete_unsynced(db, account.id, started)
            await store.refresh_threads(db, thread_ids)
            account.sync_state = "ready"
            account.backfill_started_at = None
            account.last_synced_at = await _now(db)
            account.sync_error = None
        await db.commit()
        thread_ids |= await outbox.reapply(db, account.id)
        await events.threads_changed(account.id, thread_ids)
        await events.sync_changed(account.id)  # progress, and a cleared sync error
        if account.sync_state == "ready":
            break
    log.info("account %s: backfill done, %s messages", account.id, account.backfill_done)
    await events.sync_changed(account.id)


async def import_messages(
    db: AsyncSession,
    gmail: Gmail,
    account_id: int,
    gmail_ids: list[str],
    labels: store.LabelMap,
    synced_since: datetime | None = None,
) -> tuple[set[int], set[str]]:
    """Known messages synced before `synced_since` only get their labels refreshed. Returns the
    changed thread ids and the Gmail ids fetched in full."""
    if not gmail_ids:
        return set(), set()
    rows = await db.execute(
        select(Message.gmail_id, Message.synced_at).where(
            Message.account_id == account_id, Message.gmail_id.in_(gmail_ids)
        )
    )
    known = dict(rows.all())
    new = [g for g in dict.fromkeys(gmail_ids) if g not in known]
    stale = [g for g, at in known.items() if synced_since is not None and at < synced_since]
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def fetch_full(gmail_id: str) -> tuple[dict[str, Any], mime.ParsedMessage] | None:
        async with semaphore:
            try:
                meta, raw = await gmail.message_raw(gmail_id)
            except NotFound:  # deleted since it was listed
                return None
            return meta, await asyncio.to_thread(mime.parse, raw)

    async def fetch_labels(gmail_id: str) -> dict[str, Any] | None:
        async with semaphore:
            try:
                return await gmail.message_labels(gmail_id)
            except NotFound:
                return None

    full, minimal = await asyncio.gather(
        asyncio.gather(*(fetch_full(g) for g in new)),
        asyncio.gather(*(fetch_labels(g) for g in stale)),
    )
    thread_ids: set[int] = set()
    fetched: set[str] = set()
    for result in full:
        if result:
            meta, parsed = result
            thread_ids.add(await store.save_message(db, account_id, meta, parsed, labels))
            fetched.add(meta["id"])
    for gmail_id, result in zip(stale, minimal, strict=True):
        if result is None:
            thread_ids |= await store.delete_messages(db, account_id, [gmail_id])
        elif tid := await store.set_labels(
            db, account_id, gmail_id, result.get("labelIds", []), labels
        ):
            thread_ids.add(tid)
    await store.refresh_threads(db, thread_ids)
    return thread_ids, fetched


async def fill_list_ids(db: AsyncSession, gmail: Gmail, account_id: int) -> None:
    """Mail mirrored before kanso kept List-Id (migration 0020) gets it, a batch per sync."""
    rows = await db.execute(
        select(Message.id, Message.gmail_id)
        .where(Message.account_id == account_id, Message.list_id.is_(None))
        .limit(LIST_ID_BATCH)
    )
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def fetch(gmail_id: str) -> str:
        async with semaphore:
            try:
                headers = await gmail.message_headers(gmail_id, ["List-Id"])
            except NotFound:  # deleted: the next sync removes it
                return ""
        return " ".join(mime.decode_header(headers.get("list-id")).split())

    pending = rows.all()
    found = await asyncio.gather(*(fetch(gmail_id) for _, gmail_id in pending))
    for (message_id, _), list_id in zip(pending, found, strict=True):
        await db.execute(update(Message).where(Message.id == message_id).values(list_id=list_id))
    await db.commit()


async def incremental(db: AsyncSession, gmail: Gmail, account: Account) -> None:
    assert account.history_id is not None
    labels = await store.save_labels(db, account.id, await gmail.labels())
    records: list[dict[str, Any]] = []
    page_token = None
    try:
        while True:
            page = await gmail.history(account.history_id, page_token)
            records += page.get("history", [])
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    except NotFound:
        log.warning("account %s: history %s expired, full resync", account.id, account.history_id)
        account.sync_state = "backfill"
        account.backfill_started_at = None
        await db.commit()
        await backfill(db, gmail, account)
        return
    new_history_id = int(page["historyId"])

    added: dict[str, None] = {}  # ordered set
    deleted: set[str] = set()
    label_changes: list[tuple[str, list[str], list[str]]] = []
    for record in records:
        for item in record.get("messagesAdded", []):
            added[item["message"]["id"]] = None
        for item in record.get("messagesDeleted", []):
            deleted.add(item["message"]["id"])
        for item in record.get("labelsAdded", []):
            label_changes.append((item["message"]["id"], item["labelIds"], []))
        for item in record.get("labelsRemoved", []):
            label_changes.append((item["message"]["id"], [], item["labelIds"]))

    thread_ids = await store.delete_messages(db, account.id, deleted)
    changed, fetched = await import_messages(
        db, gmail, account.id, [g for g in added if g not in deleted], labels
    )
    thread_ids |= changed
    blocked = await _spam_blocked(db, account.id, fetched)
    thread_ids |= blocked
    # A user label change seen here was made by hand in Gmail (kanso's own are in the mirror
    # already), so AI labeling leaves that thread alone.
    user_labels = set(
        await db.scalars(
            select(Label.id).where(Label.account_id == account.id, Label.type == "user")
        )
    )
    missing: list[str] = []
    manual: set[int] = set()
    for gmail_id, add, remove in label_changes:
        if gmail_id in deleted or gmail_id in fetched:
            continue
        result = await store.change_labels(db, account.id, gmail_id, add, remove, labels)
        if result is None:
            missing.append(gmail_id)
            continue
        thread_ids.add(result[0])
        if result[1] & user_labels:
            manual.add(result[0])
    if missing:  # changes to messages the mirror lacks
        thread_ids |= (await import_messages(db, gmail, account.id, missing, labels))[0]
    await store.refresh_threads(db, thread_ids)
    await store.mark_manual(db, manual)
    # Never the backfill: no AI pass over history.
    queued = await labeling.enqueue_new(db, account.id, fetched)

    account.history_id = new_history_id
    account.last_synced_at = await _now(db)
    had_error = account.sync_error is not None
    account.sync_error = None
    await db.commit()
    if queued:
        await labeling.wake()
    if blocked:
        await events.wake_outbox()
    thread_ids |= await outbox.reapply(db, account.id)
    await events.threads_changed(account.id, thread_ids)
    if had_error:
        await events.sync_changed(account.id)
    # Never the backfill: history isn't news.
    await notify.new_mail(db, gmail.http, account.id, fetched)
    if records:
        log.info("account %s: %s changes synced", account.id, len(records))


async def _spam_blocked(db: AsyncSession, account_id: int, gmail_ids: set[str]) -> set[int]:
    """New mail from a blocked sender goes to Spam, in Gmail too. Returns the changed thread ids;
    the caller refreshes them."""
    if not gmail_ids:
        return set()
    blocked = list(
        await db.scalars(
            select(Message.gmail_id)
            .join(
                BlockedSender,
                (BlockedSender.account_id == Message.account_id)
                & (BlockedSender.email == func.lower(Message.from_email)),
            )
            .where(Message.account_id == account_id, Message.gmail_id.in_(gmail_ids))
        )
    )
    if not blocked:
        return set()
    outbox.enqueue(db, account_id, "blocked", blocked, ["SPAM"], ["INBOX"])
    return await store.apply_delta(db, account_id, blocked, ["SPAM"], ["INBOX"])


async def renew_watch(db: AsyncSession, gmail: Gmail, account: Account) -> None:
    now = datetime.now(UTC)
    if account.watch_expires_at and account.watch_expires_at - now > WATCH_RENEW_BEFORE:
        return
    response = await gmail.watch(settings.google_pubsub_topic)
    account.watch_expires_at = datetime.fromtimestamp(int(response["expiration"]) / 1000, UTC)
    await db.commit()
    log.info("account %s: Gmail push watch renewed until %s", account.id, account.watch_expires_at)


async def record_error(account_id: int, error: Exception) -> None:
    async with Session() as db:
        account = await db.get(Account, account_id)
        if account is None:
            return
        account.sync_error = str(error)[:500] or type(error).__name__
        await db.commit()
    await events.sync_changed(account_id)
