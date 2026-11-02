import asyncio
import base64
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, case, exists, func, null, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from kanso import demo_seed
from kanso.core.settings import settings
from kanso.db import search
from kanso.db.models import Account, Attachment, Label, Message, MessageLabel, Thread
from kanso.db.session import get_session
from kanso.gmail import client as gmail_client
from kanso.gmail import mime

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]
Mailbox = Literal["inbox", "starred", "done", "sent", "drafts", "all", "trash", "spam"]


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    subject: str
    snippet: str
    senders: list[str]
    last_message_at: datetime
    message_count: int
    label_ids: list[int]
    unread: bool
    starred: bool
    important: bool
    in_inbox: bool
    in_trash: bool
    has_attachments: bool
    lang: str | None
    # In the grouped inbox: the label section the thread is placed in (None: unlabeled).
    section: str | None = None
    # The names in `senders` that are the user's own addresses (any connected account): "me".
    me: list[str] = []
    # Sent and Drafts: who the user's messages in the thread went to, shown instead of senders.
    recipients: list[str] | None = None


class SectionOut(BaseModel):
    """A section of the grouped inbox, or a bundle."""

    name: str | None  # the label; None for unlabeled threads
    bundle: bool
    count: int
    newest_at: datetime
    senders: list[str]  # bundles: the senders of their newest threads


class ThreadPage(BaseModel):
    threads: list[ThreadOut]
    next_cursor: str | None
    sections: list[SectionOut] | None = None  # the grouped inbox's first page


class Address(BaseModel):
    name: str
    email: str


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime_type: str
    size: int
    content_id: str | None
    inline: bool


class MessageOut(BaseModel):
    id: int
    date: datetime
    subject: str
    snippet: str
    sender: Address
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    reply_to: list[Address]
    label_ids: list[int]
    unread: bool
    draft: bool  # a draft being written: the reader opens it in the composer
    lang: str | None
    body_html: str | None  # sanitized; cid: URLs refer to attachments by content_id
    body_text: str | None  # only when there is no HTML
    attachments: list[AttachmentOut]


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


async def _people(db: AsyncSession, threads: list[ThreadOut], recipients: bool) -> list[ThreadOut]:
    """Mark the user's own names among each thread's senders and, with `recipients` (Sent and
    Drafts), list who the user wrote to, as Gmail shows them: "Daniel Reyes, me", "To: Daniel"."""
    mine = {email.lower() for email in await db.scalars(select(Account.email))}
    if not threads or not mine:
        return threads
    name = func.coalesce(func.nullif(Message.from_name, ""), Message.from_email)  # as `senders`
    rows = await db.execute(
        select(Message.thread_id, name, Message.to_addrs, Message.cc_addrs)
        .where(
            Message.thread_id.in_([t.id for t in threads]), func.lower(Message.from_email).in_(mine)
        )
        .order_by(Message.internal_date, Message.id)
    )
    me: dict[int, list[str]] = {}
    to: dict[int, dict[str, str]] = {}  # address -> name, in order
    for thread_id, sender, to_addrs, cc_addrs in rows:
        if sender not in me.setdefault(thread_id, []):
            me[thread_id].append(sender)
        people = to.setdefault(thread_id, {})
        for address in to_addrs + cc_addrs:
            email = address["email"].lower()
            if email not in mine:
                people.setdefault(email, address["name"] or address["email"])
    return [
        t.model_copy(
            update={
                "me": me.get(t.id, []),
                "recipients": list(to.get(t.id, {}).values()) if recipients else None,
            }
        )
        for t in threads
    ]


def _encode_cursor(thread: Thread) -> str:
    raw = f"{thread.last_message_at.isoformat()}|{thread.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        when, _, thread_id = base64.urlsafe_b64decode(cursor).decode().partition("|")
        return datetime.fromisoformat(when), int(thread_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid cursor") from error


def _has_system_label(gmail_id: str) -> ColumnElement[bool]:
    return exists(select(1).where(Label.id == Thread.label_ids.any_(), Label.gmail_id == gmail_id))


MAILBOXES: dict[str, Any] = {
    "inbox": lambda: Thread.in_inbox.is_(True),
    "starred": lambda: Thread.starred.is_(True),
    # Archived mail. A thread of drafts only is in Drafts, not Done.
    "done": lambda: ~Thread.in_inbox & ~Thread.in_trash & ~Thread.in_spam & ~Thread.drafts_only,
    "sent": lambda: _has_system_label("SENT"),
    "drafts": lambda: _has_system_label("DRAFT"),
    # Gmail's All Mail: everything but Trash and Spam (list_threads leaves those out).
    "all": lambda: true(),
    "trash": lambda: Thread.in_trash.is_(True),
    "spam": lambda: Thread.in_spam.is_(True),
}


# Each inbox thread goes under one user label, non-bundles first. Label sections only with AI on;
# without it the inbox is one date-ordered list, but bundles still collapse.


def _placed(account_id: int) -> Any:
    first = (
        select(Label.name.label("name"), Label.is_bundle.label("is_bundle"))
        .where(Label.id == Thread.label_ids.any_(), Label.type == "user")
        .order_by(Label.is_bundle, func.lower(Label.name), Label.name)
        .limit(1)
        .lateral("first_label")
    )
    section = case((first.c.is_bundle.is_(False), first.c.name)) if settings.ai_enabled else null()
    stmt = (
        select(
            Thread.id.label("id"),
            Thread.last_message_at.label("at"),
            section.label("section"),
            case((first.c.is_bundle.is_(True), first.c.name)).label("bundle"),
        )
        .select_from(Thread)
        .outerjoin(first, true())
        .where(
            Thread.account_id == account_id, MAILBOXES["inbox"](), ~Thread.in_trash, ~Thread.in_spam
        )
    )
    return stmt.subquery("placed")


async def section_thread_ids(
    db: AsyncSession, name: str | None, bundle: bool, account_id: int, until: datetime
) -> list[int]:
    placed = _placed(account_id)
    if bundle:
        where = placed.c.bundle == name
    elif name is None:
        where = placed.c.section.is_(None) & placed.c.bundle.is_(None)
    else:
        where = placed.c.section == name
    return list(await db.scalars(select(placed.c.id).where(where, placed.c.at <= until)))


async def _sections(db: AsyncSession, account_id: int) -> list[SectionOut]:
    placed = _placed(account_id)
    rows = await db.execute(
        select(placed.c.section, placed.c.bundle, func.count(), func.max(placed.c.at)).group_by(
            placed.c.section, placed.c.bundle
        )
    )
    sections = []
    for section, bundle, count, newest in rows:
        senders: list[str] = []
        if bundle is not None:
            for names in await db.scalars(
                select(Thread.senders)
                .join(placed, placed.c.id == Thread.id)
                .where(placed.c.bundle == bundle)
                .order_by(Thread.last_message_at.desc())
                .limit(10)
            ):
                senders += [n for n in names if n not in senders]
        sections.append(
            SectionOut(
                name=bundle if bundle is not None else section,
                bundle=bundle is not None,
                count=count,
                newest_at=newest,
                senders=senders[:3],
            )
        )
    sections.sort(key=lambda s: (s.name is None and not s.bundle, s.newest_at), reverse=True)
    return sections


async def _grouped(db: AsyncSession, account_id: int, cursor: str | None, limit: int) -> ThreadPage:
    """The inbox outside bundles, section by section."""
    placed = _placed(account_id)
    ranked = (
        select(
            placed.c.id,
            placed.c.section,
            placed.c.section.is_(None).label("other"),
            func.max(placed.c.at).over(partition_by=placed.c.section).label("section_at"),
            func.coalesce(placed.c.section, "").label("key"),
            placed.c.at,
        )
        .where(placed.c.bundle.is_(None))
        .subquery("ranked")
    )
    order = (ranked.c.other, ranked.c.section_at, ranked.c.key, ranked.c.at, ranked.c.id)
    stmt = select(Thread, *order[:3], ranked.c.section).join(ranked, ranked.c.id == Thread.id)
    if cursor:
        try:
            other, section_at, key, at, thread_id = json.loads(base64.urlsafe_b64decode(cursor))
            after = (
                bool(other),
                datetime.fromisoformat(section_at),
                key,
                datetime.fromisoformat(at),
                thread_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid cursor") from error
        stmt = stmt.where(tuple_(*order) < after)
    stmt = stmt.order_by(*(column.desc() for column in order)).limit(limit + 1)
    rows = (await db.execute(stmt)).all()
    next_cursor = None
    if len(rows) > limit:
        thread, other, section_at, key, _ = rows[limit - 1]
        raw = [other, section_at.isoformat(), key, thread.last_message_at.isoformat(), thread.id]
        next_cursor = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
    out = [
        ThreadOut.model_validate(thread).model_copy(update={"section": section})
        for thread, _, _, _, section in rows[:limit]
    ]
    return ThreadPage(
        threads=await _people(db, out, recipients=False),
        next_cursor=next_cursor,
        sections=None if cursor else await _sections(db, account_id),
    )


@router.get("/threads")
async def list_threads(
    db: DB,
    account_id: int,
    mailbox: Mailbox | None = None,
    label_id: Annotated[list[int] | None, Query()] = None,
    q: str = "",
    grouped: bool = False,
    bundle: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ThreadPage:
    """Trash and Spam are left out unless asked for (mailbox, label or label: in the query)."""
    if grouped or bundle is not None:
        if mailbox != "inbox" or label_id or q.strip():
            raise HTTPException(status_code=400, detail="Only the inbox is grouped")
        if grouped:
            return await _grouped(db, account_id, cursor, limit)
    stmt = select(Thread)
    if bundle is not None:
        placed = _placed(account_id)
        stmt = stmt.join(placed, placed.c.id == Thread.id).where(placed.c.bundle == bundle)
    wants_trash_or_spam = mailbox in ("trash", "spam")
    stmt = stmt.where(Thread.account_id == account_id)
    if mailbox:
        stmt = stmt.where(MAILBOXES[mailbox]())
    if label_id:
        labels = list(await db.scalars(select(Label).where(Label.id.in_(label_id))))
        if len(labels) != len(set(label_id)):
            raise HTTPException(status_code=404, detail="Label not found")
        stmt = stmt.where(Thread.label_ids.overlap(label_id))
        wants_trash_or_spam |= any(label.gmail_id in ("TRASH", "SPAM") for label in labels)
    if q.strip():
        conditions, targets = search.conditions(q)
        stmt = stmt.where(*conditions)
        wants_trash_or_spam |= targets
    if not wants_trash_or_spam:
        stmt = stmt.where(~Thread.in_trash, ~Thread.in_spam)
    if cursor:
        stmt = stmt.where(tuple_(Thread.last_message_at, Thread.id) < _decode_cursor(cursor))
    stmt = stmt.order_by(Thread.last_message_at.desc(), Thread.id.desc()).limit(limit + 1)
    threads = list(await db.scalars(stmt))
    next_cursor = _encode_cursor(threads[limit - 1]) if len(threads) > limit else None
    out = [ThreadOut.model_validate(t) for t in threads[:limit]]
    recipients = mailbox in ("sent", "drafts")
    return ThreadPage(threads=await _people(db, out, recipients), next_cursor=next_cursor)


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: int, db: DB) -> ThreadDetail:
    thread = await db.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = list(
        await db.scalars(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.internal_date, Message.id)
        )
    )
    ids = [m.id for m in messages]
    label_rows = await db.execute(
        select(MessageLabel.message_id, MessageLabel.label_id, Label.gmail_id)
        .join(Label)
        .where(MessageLabel.message_id.in_(ids))
    )
    labels: dict[int, list[int]] = {}
    unread: set[int] = set()
    draft: set[int] = set()
    for message_id, label_id, gmail_id in label_rows:
        labels.setdefault(message_id, []).append(label_id)
        if gmail_id == "UNREAD":
            unread.add(message_id)
        elif gmail_id == "DRAFT":
            draft.add(message_id)
    attachments: dict[int, list[AttachmentOut]] = {}
    for attachment in await db.scalars(
        select(Attachment).where(Attachment.message_id.in_(ids)).order_by(Attachment.part_index)
    ):
        attachments.setdefault(attachment.message_id, []).append(
            AttachmentOut.model_validate(attachment)
        )
    return ThreadDetail(
        **ThreadOut.model_validate(thread).model_dump(),
        messages=[
            MessageOut(
                id=m.id,
                date=m.internal_date,
                subject=m.subject,
                snippet=m.snippet,
                sender=Address(name=m.from_name, email=m.from_email),
                to=[Address(**a) for a in m.to_addrs],
                cc=[Address(**a) for a in m.cc_addrs],
                bcc=[Address(**a) for a in m.bcc_addrs],
                reply_to=[Address(**a) for a in m.reply_to],
                label_ids=sorted(labels.get(m.id, [])),
                unread=m.id in unread,
                draft=m.id in draft,
                lang=m.lang,
                body_html=m.body_html,
                body_text=m.body_text if m.body_html is None else None,
                attachments=attachments.get(m.id, []),
            )
            for m in messages
        ],
    )


# Types safe to show inline; everything else downloads, so a hostile attachment (HTML, SVG…)
# never renders on kanso's origin.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: int, db: DB) -> Response:
    """Fetched from Gmail on demand: the mirror keeps only metadata."""
    row = (
        await db.execute(
            select(Attachment, Message.gmail_id, Account)
            .join(Message, Attachment.message_id == Message.id)
            .join(Account, Message.account_id == Account.id)
            .where(Attachment.id == attachment_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment, gmail_id, account = row
    if account.demo:
        content = demo_seed.DEMO_FILES.get(attachment.mime_type)
    else:
        async with gmail_client.http_client() as http:
            try:
                _, raw = await gmail_client.Gmail(account, http).message_raw(gmail_id)
            except gmail_client.NotFound as error:
                raise HTTPException(status_code=404, detail="Message is gone from Gmail") from error
        content = await asyncio.to_thread(mime.attachment_content, raw, attachment.part_index)
    if content is None:
        raise HTTPException(status_code=404, detail="Attachment not found in the message")
    disposition = "inline" if attachment.mime_type in INLINE_TYPES else "attachment"
    return Response(
        content,
        media_type=attachment.mime_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(attachment.filename)}",
            "X-Content-Type-Options": "nosniff",
            # An attachment never changes (a new message gets new ids): no repeat Gmail fetch.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )
