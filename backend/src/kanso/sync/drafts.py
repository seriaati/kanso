"""Sending never duplicates: `sending_since` is committed before drafts.send, and Gmail deletes a
draft when it sends it, so a retry either sends it or gets 404 (sent already). A 4xx refusal
clears the mark; an unknown outcome keeps it for resume_sends.
"""

import asyncio
import html
import logging
import shutil
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from email.utils import make_msgid
from pathlib import Path
from typing import Any

import httpx2 as httpx
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from kanso import demo_seed
from kanso.core.settings import settings
from kanso.db import contacts
from kanso.db import redis as events
from kanso.db.models import Account, Attachment, Draft, Label, Message, MessageLabel, Thread
from kanso.db.session import Session
from kanso.gmail import compose, mime, oauth
from kanso.gmail.client import Gmail, GmailError, NotFound
from kanso.sync import store

log = logging.getLogger(__name__)

ATTACHMENTS_LIMIT = 25 * 1024 * 1024  # Gmail's limit for a message's attachments
CACHE_KEEP = timedelta(days=1)  # since last use
ORPHAN_AFTER = timedelta(days=1)  # drafts opened but never saved
RESUME_AFTER = timedelta(minutes=1)  # a send left unfinished this long is resumed by the worker
LOOP_SECONDS = 60
CONTACTS_EVERY = 5  # loops
_LOCK_NAMESPACE = 5  # pg_advisory_xact_lock(namespace, draft id)


class DraftError(Exception):
    """Shown to the user as is, with an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class SendPending(Exception):
    """Gmail's answer to drafts.send never arrived; the draft stays marked and is resumed."""


@asynccontextmanager
async def lock(draft_id: int, wait: bool = True) -> AsyncGenerator[bool]:
    """Held by its own transaction, so the work can commit as it goes; a crash releases it."""
    async with Session() as session:
        args = {"ns": _LOCK_NAMESPACE, "id": draft_id}
        if wait:
            await session.execute(text("SELECT pg_advisory_xact_lock(:ns, :id)"), args)
            yield True
        else:
            yield bool(
                await session.scalar(text("SELECT pg_try_advisory_xact_lock(:ns, :id)"), args)
            )


def _new_id() -> str:
    return uuid.uuid4().hex


def _msgid(account: Account) -> str:
    return make_msgid(domain=account.email.rpartition("@")[2] or "kanso.local")


async def create(
    db: AsyncSession, mode: str, message_id: int | None, account_id: int | None, tz: str | None
) -> Draft:
    """Nothing goes to Gmail until the first save. The caller commits."""
    accounts = list(await db.scalars(select(Account).order_by(Account.id)))
    if not accounts:
        raise DraftError(400, "Connect a Gmail account first.")
    if mode == "new":
        account = next((a for a in accounts if a.id == account_id), accounts[0])
        draft = Draft(account_id=account.id, mode="new", message_id_header=_msgid(account))
        db.add(draft)
        await db.flush()
        return draft

    original = await db.get(Message, message_id) if message_id else None
    if original is None:
        raise DraftError(404, "That message is no longer in your mail.")
    account = next(a for a in accounts if a.id == original.account_id)
    thread = await db.get(Thread, original.thread_id)
    assert thread is not None
    draft = Draft(
        account_id=account.id,
        mode="forward" if mode == "forward" else "reply",
        gmail_thread_id=thread.gmail_id,
        message_id_header=_msgid(account),
        in_reply_to=original.message_id_header,
        references=compose.references(original),
    )
    if mode == "forward":
        draft.subject = compose.forward_subject(original.subject)
        draft.quote_html, draft.quote_text = compose.forward_quote(original, tz)
        rows = await db.scalars(
            select(Attachment)
            .where(Attachment.message_id == original.id)
            .order_by(Attachment.part_index)
        )
        draft.attachments = [_entry(a, account.id, original.gmail_id) for a in rows]
    else:
        mine = {a.email.lower() for a in accounts}
        to, cc = compose.reply_recipients(original, mine, reply_all=mode == "reply_all")
        draft.to_addrs, draft.cc_addrs = to, cc
        draft.subject = compose.reply_subject(original.subject)
        draft.quote_html, draft.quote_text = compose.reply_quote(original, tz)
    db.add(draft)
    await db.flush()
    return draft


def _entry(attachment: Attachment, account_id: int, gmail_id: str) -> dict[str, Any]:
    return {
        "id": _new_id(),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "content_id": attachment.content_id if attachment.inline else None,
        "src": {
            "account_id": account_id,
            "gmail_id": gmail_id,
            "part_index": attachment.part_index,
        },
    }


async def open_message(db: AsyncSession, message_id: int, http: httpx.AsyncClient) -> Draft:
    """A draft written elsewhere (in Gmail, or the demo seed) is taken over. The caller commits."""
    message = await db.get(Message, message_id)
    if message is None:
        raise DraftError(404, "That draft is no longer in your mail.")
    draft = await db.scalar(
        select(Draft).where(
            Draft.account_id == message.account_id, Draft.gmail_message_id == message.gmail_id
        )
    )
    if draft is not None:
        return draft
    is_draft = await db.scalar(
        select(MessageLabel.message_id)
        .join(Label)
        .where(MessageLabel.message_id == message.id, Label.gmail_id == "DRAFT")
    )
    if is_draft is None:
        raise DraftError(400, "That message isn't a draft.")
    account = await db.get(Account, message.account_id)
    thread = await db.get(Thread, message.thread_id)
    assert account is not None and thread is not None
    gmail_draft_id = None
    if not account.demo:
        gmail_draft_id = await _find_gmail_draft(Gmail(account, http), message.gmail_id)
        if gmail_draft_id is None:
            raise DraftError(404, "That draft is gone from Gmail.")
    if message.body_html is not None:
        body = compose.sanitize_body(message.body_html)
    else:
        body = html.escape(message.body_text).replace("\n", "<br>")
    rows = await db.scalars(
        select(Attachment)
        .where(Attachment.message_id == message.id)
        .order_by(Attachment.part_index)
    )
    draft = Draft(
        account_id=account.id,
        mode="reply" if message.in_reply_to else "new",
        gmail_draft_id=gmail_draft_id,
        gmail_message_id=message.gmail_id,
        gmail_thread_id=thread.gmail_id,
        message_id_header=message.message_id_header or _msgid(account),
        in_reply_to=message.in_reply_to,
        references=message.references,
        to_addrs=message.to_addrs,
        cc_addrs=message.cc_addrs,
        bcc_addrs=message.bcc_addrs,
        subject=message.subject,
        body_html=body,
        attachments=[_entry(a, account.id, message.gmail_id) for a in rows],
    )
    db.add(draft)
    await db.flush()
    return draft


async def _find_gmail_draft(gmail: Gmail, gmail_message_id: str) -> str | None:
    page_token = None
    while True:
        page = await gmail.list_drafts(page_token)
        for item in page.get("drafts", []):
            if item.get("message", {}).get("id") == gmail_message_id:
                return item["id"]
        page_token = page.get("nextPageToken")
        if not page_token:
            return None


def _path(draft_id: int, attachment_id: str) -> Path:
    return settings.upload_dir / str(draft_id) / attachment_id


def _read(path: Path) -> bytes | None:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    path.touch()
    return content


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _remove_files(draft_id: int) -> None:
    shutil.rmtree(settings.upload_dir / str(draft_id), ignore_errors=True)


def clean_filename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "attachment"


async def add_attachment(draft: Draft, filename: str, mime_type: str, content: bytes) -> None:
    if draft.sending_since:
        raise DraftError(409, "This message is being sent.")
    used = sum(a["size"] for a in draft.attachments)
    if used + len(content) > ATTACHMENTS_LIMIT:
        raise DraftError(413, "Attachments can add up to 25 MB, as in Gmail.")
    entry = {
        "id": _new_id(),
        "filename": clean_filename(filename),
        "mime_type": mime_type or "application/octet-stream",
        "size": len(content),
        "content_id": None,
        "src": None,
    }
    await asyncio.to_thread(_write, _path(draft.id, entry["id"]), content)
    draft.attachments = [*draft.attachments, entry]


def remove_attachment(draft: Draft, attachment_id: str) -> None:
    if draft.sending_since:
        raise DraftError(409, "This message is being sent.")
    if not any(a["id"] == attachment_id for a in draft.attachments):
        return  # unknown (or not an id at all): no file is touched
    draft.attachments = [a for a in draft.attachments if a["id"] != attachment_id]
    _path(draft.id, attachment_id).unlink(missing_ok=True)


async def _parts(db: AsyncSession, draft: Draft, http: httpx.AsyncClient) -> list[compose.Part]:
    raws: dict[tuple[int, str], bytes | None] = {}
    parts = []
    for entry in draft.attachments:
        path = _path(draft.id, entry["id"])
        content = await asyncio.to_thread(_read, path)
        if content is None:
            content = await _fetch(db, entry, http, raws)
            if content is None:
                raise DraftError(
                    409,
                    f"“{entry['filename']}” is no longer available. Remove it and attach it again.",
                )
            await asyncio.to_thread(_write, path, content)
        parts.append(
            compose.Part(entry["filename"], entry["mime_type"], content, entry.get("content_id"))
        )
    return parts


async def _fetch(
    db: AsyncSession,
    entry: dict[str, Any],
    http: httpx.AsyncClient,
    raws: dict[tuple[int, str], bytes | None],
) -> bytes | None:
    src = entry.get("src")
    if not src:
        return None
    account = await db.get(Account, src["account_id"])
    if account is None:
        return None
    if account.demo:  # demo mail has no Gmail copy
        return demo_seed.DEMO_FILES.get(entry["mime_type"])
    key = (account.id, src["gmail_id"])
    if key not in raws:
        try:
            raws[key] = (await Gmail(account, http).message_raw(src["gmail_id"]))[1]
        except NotFound:
            raws[key] = None
    raw = raws[key]
    return None if raw is None else mime.attachment_content(raw, src["part_index"])


def _point_at(draft: Draft, account_id: int, gmail_id: str, raw: bytes) -> None:
    """After a save, every attachment's bytes are in the new draft message, so the cache can go."""
    pool = list(mime.parse(raw).attachments)
    entries = []
    for entry in draft.attachments:
        match = next(
            (p for p in pool if p.filename == entry["filename"] and p.size == entry["size"]), None
        )
        if match is not None:
            pool.remove(match)
            src = {"account_id": account_id, "gmail_id": gmail_id, "part_index": match.part_index}
            entry = {**entry, "src": src}
        entries.append(entry)
    draft.attachments = entries


# Gmail's default send-as display name, per process.
_sender_names: dict[int, str] = {}


async def _sender(account: Account, gmail: Gmail) -> dict[str, str] | None:
    """Without a display name in Gmail, From is left out and Gmail fills it in."""
    if account.demo:
        return {"name": account.display_name, "email": account.email}
    if account.id not in _sender_names:
        try:
            aliases = await gmail.send_as()
        except GmailError as error:
            log.warning("account %s: could not read the send-as name: %s", account.id, error)
            return None
        default = next((a for a in aliases if a.get("isDefault")), {})
        _sender_names[account.id] = default.get("displayName") or ""
    name = _sender_names[account.id]
    return {"name": name, "email": account.email} if name else None


async def _build(
    db: AsyncSession, draft: Draft, account: Account, http: httpx.AsyncClient
) -> tuple[bytes, dict[str, str] | None]:
    parts = await _parts(db, draft, http)
    sender = await _sender(account, Gmail(account, http))
    raw = compose.build_message(
        sender=sender,
        to=draft.to_addrs,
        cc=draft.cc_addrs,
        bcc=draft.bcc_addrs,
        subject=draft.subject,
        body_html=draft.body_html,
        quote_html=draft.quote_html,
        quote_text=draft.quote_text,
        message_id=draft.message_id_header,
        in_reply_to=draft.in_reply_to,
        references=draft.references,
        attachments=parts,
    )
    return raw, sender


async def _mirror(
    db: AsyncSession,
    account: Account,
    meta: dict[str, Any],
    raw: bytes,
    sender: dict[str, str] | None,
) -> int:
    """Mirrored at once, so the sync then finds it known."""
    parsed = await asyncio.to_thread(mime.parse, raw)
    if not parsed.from_email:
        parsed.from_email = account.email
        parsed.from_name = (sender or {}).get("name", "")
    full = {
        "labelIds": ["DRAFT"],
        "snippet": " ".join(parsed.body_text.split())[:200],
        "internalDate": str(int(time.time() * 1000)),
        "sizeEstimate": len(raw),
        **meta,
    }
    labels = await store.label_map(db, account.id)
    return await store.save_message(db, account.id, full, parsed, labels)


async def push(db: AsyncSession, draft: Draft, http: httpx.AsyncClient) -> set[int]:
    """The caller commits."""
    account = await db.get(Account, draft.account_id)
    assert account is not None
    raw, sender = await _build(db, draft, account, http)
    old_message = draft.gmail_message_id
    if account.demo:
        meta = {"id": f"local{_new_id()[:16]}", "threadId": draft.gmail_thread_id or _new_id()[:16]}
    else:
        gmail = Gmail(account, http)
        result = None
        if draft.gmail_draft_id:
            with suppress(NotFound):
                result = await gmail.update_draft(draft.gmail_draft_id, raw, draft.gmail_thread_id)
        if result is None:
            result = await gmail.create_draft(raw, draft.gmail_thread_id)
        draft.gmail_draft_id = result["id"]
        meta = result["message"]
        _point_at(draft, account.id, meta["id"], raw)
    draft.gmail_message_id = meta["id"]
    draft.gmail_thread_id = meta.get("threadId") or draft.gmail_thread_id
    touched = {await _mirror(db, account, meta, raw, sender)}
    if old_message and old_message != meta["id"]:
        touched |= await store.delete_messages(db, account.id, [old_message])
    await store.refresh_threads(db, touched)
    return touched


async def save(
    db: AsyncSession, draft: Draft, fields: dict[str, Any], http: httpx.AsyncClient
) -> set[int]:
    """The caller commits."""
    if draft.sending_since:
        raise DraftError(409, "This message is being sent.")
    touched: set[int] = set()
    if fields["account_id"] != draft.account_id:
        touched |= await _move(db, draft, fields["account_id"], http)
    draft.to_addrs = fields["to"]
    draft.cc_addrs = fields["cc"]
    draft.bcc_addrs = fields["bcc"]
    draft.subject = fields["subject"]
    draft.body_html = compose.sanitize_body(fields["body_html"])
    draft.updated_at = datetime.now(UTC)
    return touched | await push(db, draft, http)


async def _move(
    db: AsyncSession, draft: Draft, account_id: int, http: httpx.AsyncClient
) -> set[int]:
    """The reply headers stay, so recipients still see it threaded; the Gmail thread doesn't."""
    if await db.get(Account, account_id) is None:
        raise DraftError(404, "That account is no longer connected.")
    await _parts(db, draft, http)  # cache the attachments while their source is reachable
    touched = await _drop_gmail_copy(db, draft, http)
    draft.account_id = account_id
    draft.gmail_draft_id = draft.gmail_message_id = draft.gmail_thread_id = None
    return touched


async def _drop_gmail_copy(db: AsyncSession, draft: Draft, http: httpx.AsyncClient) -> set[int]:
    account = await db.get(Account, draft.account_id)
    assert account is not None
    if draft.gmail_draft_id and not account.demo:
        with suppress(NotFound):
            await Gmail(account, http).delete_draft(draft.gmail_draft_id)
    if not draft.gmail_message_id:
        return set()
    touched = await store.delete_messages(db, account.id, [draft.gmail_message_id])
    await store.refresh_threads(db, touched)
    return touched


async def discard(db: AsyncSession, draft: Draft, http: httpx.AsyncClient) -> set[int]:
    """The caller commits."""
    if draft.sending_since:
        raise DraftError(409, "This message is being sent.")
    touched = await _drop_gmail_copy(db, draft, http)
    await db.delete(draft)
    await asyncio.to_thread(_remove_files, draft.id)
    return touched


async def send(db: AsyncSession, draft: Draft, http: httpx.AsyncClient) -> int | None:
    """Raises DraftError if it wasn't sent, SendPending if that isn't known. Commits."""
    account = await db.get(Account, draft.account_id)
    assert account is not None
    if draft.sending_since is None:
        if not (draft.to_addrs or draft.cc_addrs or draft.bcc_addrs):
            raise DraftError(400, "Add at least one recipient.")
        if not draft.gmail_message_id:
            try:
                await push(db, draft, http)
            except (GmailError, httpx.TransportError, oauth.OAuthError) as error:
                raise DraftError(502, f"Couldn’t save the message to Gmail: {error}") from error
        draft.sending_since = datetime.now(UTC)
        await db.commit()
    return await _deliver(db, draft, account, http)


async def _deliver(
    db: AsyncSession, draft: Draft, account: Account, http: httpx.AsyncClient
) -> int | None:
    touched: set[int] = set()
    if account.demo:  # no Gmail: the draft message becomes the sent one
        assert draft.gmail_message_id is not None
        touched = await store.apply_delta(
            db, account.id, [draft.gmail_message_id], ["SENT"], ["DRAFT"]
        )
        await db.execute(
            update(Message)
            .where(Message.account_id == account.id, Message.gmail_id == draft.gmail_message_id)
            .values(internal_date=datetime.now(UTC))
        )
        thread_gmail_id = draft.gmail_thread_id
    else:
        gmail = Gmail(account, http)
        assert draft.gmail_draft_id is not None
        try:
            sent: dict[str, Any] | None = await gmail.send_draft(draft.gmail_draft_id)
        except NotFound:
            sent = None  # an earlier attempt sent it: Gmail deletes a draft when sending it
            log.info("draft %s: sent by an earlier attempt", draft.id)
        except GmailError as error:
            if 400 <= error.status < 500 and error.status not in (408, 429):
                draft.sending_since = None  # Gmail refused it: nothing went out
                await db.commit()
                raise DraftError(400, f"Gmail didn’t send it: {_reason(error)}") from error
            raise SendPending(str(error)) from error
        except oauth.OAuthError as error:  # never reached Gmail
            draft.sending_since = None
            await db.commit()
            raise DraftError(400, f"Gmail didn’t send it: {error}") from error
        except Exception as error:
            raise SendPending(str(error) or type(error).__name__) from error
        thread_gmail_id = (sent or {}).get("threadId") or draft.gmail_thread_id
        if sent is not None:
            try:  # Gmail's copy, with the headers it set (Message-ID…), for later replies
                meta, raw = await gmail.message_raw(sent["id"])
                labels = await store.label_map(db, account.id)
                parsed = await asyncio.to_thread(mime.parse, raw)
                touched.add(await store.save_message(db, account.id, meta, parsed, labels))
            except (GmailError, httpx.TransportError) as error:
                log.info("draft %s: sent; the sync will mirror it (%s)", draft.id, error)
        if draft.gmail_message_id:
            touched |= await store.delete_messages(db, account.id, [draft.gmail_message_id])
    await store.refresh_threads(db, touched)
    await db.delete(draft)
    await db.commit()
    await asyncio.to_thread(_remove_files, draft.id)
    await publish(account.id, touched)
    log.info("draft %s sent from account %s", draft.id, account.id)
    if not thread_gmail_id:
        return None
    return await db.scalar(
        select(Thread.id).where(Thread.account_id == account.id, Thread.gmail_id == thread_gmail_id)
    )


def _reason(error: GmailError) -> str:
    text_ = str(error)
    marker = '"message": "'
    if marker in text_:
        return text_.split(marker, 1)[1].split('"', 1)[0]
    return text_


async def publish(account_id: int, touched: set[int]) -> None:
    try:
        await events.threads_changed(account_id, touched)
    except Exception as error:
        log.warning("could not publish to Redis: %s", error)


async def sweep(db: AsyncSession) -> None:
    """Cached files of saved drafts can be fetched back from Gmail."""
    await db.execute(
        delete(Draft).where(
            Draft.gmail_message_id.is_(None),
            Draft.sending_since.is_(None),
            Draft.updated_at < datetime.now(UTC) - ORPHAN_AFTER,
        )
    )
    rows = await db.execute(select(Draft.id, Draft.attachments))
    keep_until_used = {i for i, entries in rows if any(not a.get("src") for a in entries)}
    existing = set(await db.scalars(select(Draft.id)))
    await asyncio.to_thread(_sweep_files, existing, keep_until_used)


def _sweep_files(existing: set[int], keep: set[int]) -> None:
    if not settings.upload_dir.is_dir():
        return
    cutoff = time.time() - CACHE_KEEP.total_seconds()
    for folder in settings.upload_dir.iterdir():
        draft_id = int(folder.name) if folder.name.isdigit() else None
        if draft_id not in existing:
            shutil.rmtree(folder, ignore_errors=True)
        elif draft_id not in keep:
            for file in folder.iterdir():
                if file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)


async def resume_sends(http: httpx.AsyncClient) -> None:
    """Each only calls drafts.send again, which can't send twice."""
    async with Session() as db:
        stale = list(
            await db.scalars(
                select(Draft.id).where(Draft.sending_since < datetime.now(UTC) - RESUME_AFTER)
            )
        )
    for draft_id in stale:
        async with lock(draft_id, wait=False) as locked, Session() as db:
            if not locked:
                continue  # being sent right now
            draft = await db.get(Draft, draft_id)
            if draft is None or draft.sending_since is None:
                continue
            account = await db.get(Account, draft.account_id)
            assert account is not None
            try:
                await _deliver(db, draft, account, http)
            except DraftError as error:
                log.warning("draft %s was not sent: %s", draft_id, error)
                with suppress(Exception):
                    await events.publish({"type": "send_failed", "message": str(error)})
            except SendPending as error:
                log.warning(
                    "draft %s: sending still unconfirmed (%s), retrying later", draft_id, error
                )


async def run(http: httpx.AsyncClient) -> None:
    loops = 0
    while True:
        try:
            await resume_sends(http)
            if loops % CONTACTS_EVERY == 0:
                async with Session() as db:
                    if await contacts.refresh(db, if_changed=True):
                        log.info("contacts for autocomplete refreshed")
        except Exception:
            log.exception("drafts: housekeeping failed")
        loops += 1
        await asyncio.sleep(LOOP_SECONDS)
