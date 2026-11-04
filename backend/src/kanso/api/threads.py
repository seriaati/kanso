import asyncio
import base64
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, exists, func, select, true, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from kanso import demo_seed, logos
from kanso.db import search
from kanso.db.models import (
    Account,
    Attachment,
    ContactPhoto,
    DomainLogo,
    Label,
    Message,
    MessageLabel,
    Thread,
)
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
    in_spam: bool
    has_attachments: bool
    lang: str | None
    # The names in `senders` that are the user's own addresses (any connected account): "me".
    me: list[str] = []
    # Sent and Drafts: who the user's messages in the thread went to, shown instead of senders.
    recipients: list[str] | None = None
    # The newest sender who isn't the user, and their image: a photo from Google Contacts
    # (sync/photos.py), else their domain's logo (logos.py). None: draw their initial.
    sender_name: str | None = None
    sender_photo: str | None = None


class BundleSender(BaseModel):
    name: str
    photo: str | None  # as ThreadOut.sender_photo


class BundleOut(BaseModel):
    """A label's inbox threads, collapsed into one row of the grouped inbox."""

    name: str  # the label
    count: int
    newest_at: datetime
    senders: list[BundleSender]  # the senders of its newest threads, newest first


class ThreadPage(BaseModel):
    threads: list[ThreadOut]
    next_cursor: str | None
    bundles: list[BundleOut] | None = None  # the grouped inbox's first page


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
    sender_photo: str | None  # as in ThreadOut
    label_ids: list[int]
    unread: bool
    draft: bool  # a draft being written: the reader opens it in the composer
    lang: str | None
    body_html: str | None  # sanitized; cid: URLs refer to attachments by content_id
    body_text: str | None  # only when there is no HTML
    attachments: list[AttachmentOut]


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


async def _senders(db: AsyncSession, ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """Each thread's newest sender who isn't the thread's account: (name, image URL). The image
    is their contact photo, else their domain's logo, unless the domain is known to have none."""
    name = func.coalesce(func.nullif(Message.from_name, ""), Message.from_email)  # as `senders`
    newest = (
        select(
            Message.thread_id,
            Message.account_id,
            name.label("name"),
            func.lower(Message.from_email).label("email"),
        )
        .join(Account, Account.id == Message.account_id)
        .where(
            Message.thread_id.in_(ids),
            func.lower(Message.from_email) != func.lower(Account.email),
        )
        .ext(distinct_on(Message.thread_id))
        .order_by(Message.thread_id, Message.internal_date.desc(), Message.id.desc())
        .subquery()
    )
    rows = (await db.execute(select(newest))).all()
    images = await _images(db, {(account_id, email) for _, account_id, _, email in rows})
    return {
        thread_id: (sender, images[account_id, email])
        for thread_id, account_id, sender, email in rows
    }


async def _images(
    db: AsyncSession, senders: set[tuple[int, str]]
) -> dict[tuple[int, str], str | None]:
    """The image of each sender, by (account id, lowercase email): their contact photo, else
    their domain's logo, unless the domain is known to have none. None: draw their initial."""
    if not senders:
        return {}
    photos = {
        (account_id, email): photo_id
        for account_id, email, photo_id in await db.execute(
            select(ContactPhoto.account_id, ContactPhoto.email, ContactPhoto.id).where(
                tuple_(ContactPhoto.account_id, ContactPhoto.email).in_(senders)
            )
        )
    }
    domains = {
        sender: domain
        for sender in senders
        if sender not in photos and (domain := logos.domain_of(sender[1]))
    }
    no_logo = set(
        await db.scalars(
            select(DomainLogo.domain).where(
                DomainLogo.domain.in_(set(domains.values())),
                DomainLogo.content.is_(None),
                DomainLogo.fetched_at >= func.now() - logos.REFRESH,
            )
        )
    )
    out: dict[tuple[int, str], str | None] = {}
    for sender in senders:
        domain = domains.get(sender)
        if sender in photos:
            out[sender] = f"/api/photos/{photos[sender]}"
        elif domain and domain not in no_logo:
            out[sender] = f"/api/logos/{domain}"
        else:
            out[sender] = None
    return out


async def _people(db: AsyncSession, threads: list[ThreadOut], recipients: bool) -> list[ThreadOut]:
    """Mark the user's own names among each thread's senders and, with `recipients` (Sent and
    Drafts), list who the user wrote to, as Gmail shows them: "Daniel Reyes, me", "To: Daniel".
    Also each thread's sender and their image. "The user" is the thread's account: mail between
    two connected accounts has a real sender and recipient in each."""
    if not threads:
        return threads
    ids = [t.id for t in threads]
    senders = await _senders(db, ids)
    name = func.coalesce(func.nullif(Message.from_name, ""), Message.from_email)  # as `senders`
    rows = await db.execute(
        select(Message.thread_id, name, Account.email, Message.to_addrs, Message.cc_addrs)
        .join(Account, Account.id == Message.account_id)
        .where(
            Message.thread_id.in_(ids),
            func.lower(Message.from_email) == func.lower(Account.email),
        )
        .order_by(Message.internal_date, Message.id)
    )
    me: dict[int, list[str]] = {}
    to: dict[int, dict[str, str]] = {}  # address -> name, in order
    for thread_id, sender, own, to_addrs, cc_addrs in rows:
        if sender not in me.setdefault(thread_id, []):
            me[thread_id].append(sender)
        people = to.setdefault(thread_id, {})
        for address in to_addrs + cc_addrs:
            email = address["email"].lower()
            if email != own.lower():
                people.setdefault(email, address["name"] or address["email"])
    return [
        t.model_copy(
            update={
                "me": me.get(t.id, []),
                "recipients": list(to.get(t.id, {}).values()) if recipients else None,
                "sender_name": senders[t.id][0] if t.id in senders else None,
                "sender_photo": senders[t.id][1] if t.id in senders else None,
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


# The grouped inbox: unlabeled threads, then each label's threads collapsed into a bundle. A
# thread with several labels goes in one bundle only, its first label's by name.


def _placed(account_id: int) -> Any:
    first = (
        select(Label.name.label("name"))
        .where(Label.id == Thread.label_ids.any_(), Label.type == "user")
        .order_by(func.lower(Label.name), Label.name)
        .limit(1)
        .lateral("first_label")
    )
    stmt = (
        select(
            Thread.id.label("id"), Thread.last_message_at.label("at"), first.c.name.label("bundle")
        )
        .select_from(Thread)
        .outerjoin(first, true())
        .where(
            Thread.account_id == account_id, MAILBOXES["inbox"](), ~Thread.in_trash, ~Thread.in_spam
        )
    )
    return stmt.subquery("placed")


async def bundle_thread_ids(
    db: AsyncSession, name: str, account_id: int, until: datetime
) -> list[int]:
    placed = _placed(account_id)
    where = (placed.c.bundle == name, placed.c.at <= until)
    return list(await db.scalars(select(placed.c.id).where(*where)))


async def _bundles(db: AsyncSession, account_id: int) -> list[BundleOut]:
    placed = _placed(account_id)
    rows = await db.execute(
        select(placed.c.bundle, func.count(), func.max(placed.c.at))
        .where(placed.c.bundle.is_not(None))
        .group_by(placed.c.bundle)
    )
    bundles = []
    for name, count, newest in rows:
        ids = list(
            await db.scalars(
                select(Thread.id)
                .join(placed, placed.c.id == Thread.id)
                .where(placed.c.bundle == name)
                .order_by(Thread.last_message_at.desc())
                .limit(20)
            )
        )
        latest = await _senders(db, ids)
        senders: dict[str, str | None] = {}
        for thread_id in ids:
            if thread_id in latest:
                sender, photo = latest[thread_id]
                senders.setdefault(sender, photo)
        bundles.append(
            BundleOut(
                name=name,
                count=count,
                newest_at=newest,
                senders=[BundleSender(name=n, photo=p) for n, p in list(senders.items())[:8]],
            )
        )
    bundles.sort(key=lambda b: b.newest_at, reverse=True)
    return bundles


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
    """Trash and Spam are left out unless asked for (mailbox, label or label: in the query).

    `grouped`: the inbox's unlabeled threads, with its bundles on the first page. `bundle`: the
    inbox threads in that bundle."""
    if grouped or bundle is not None:
        if mailbox != "inbox" or label_id or q.strip():
            raise HTTPException(status_code=400, detail="Only the inbox is grouped")
    stmt = select(Thread)
    if grouped or bundle is not None:
        placed = _placed(account_id)
        in_bundle = placed.c.bundle.is_(None) if grouped else placed.c.bundle == bundle
        stmt = stmt.join(placed, placed.c.id == Thread.id).where(in_bundle)
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
    return ThreadPage(
        threads=await _people(db, out, recipients),
        next_cursor=next_cursor,
        bundles=await _bundles(db, account_id) if grouped and not cursor else None,
    )


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
    images = await _images(db, {(m.account_id, m.from_email.lower()) for m in messages})
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
                sender_photo=images[m.account_id, m.from_email.lower()],
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
# never renders on kanso's origin. A PDF opens in the browser's own viewer, not as a page.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}


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
        content = demo_seed.demo_file(attachment.filename, attachment.mime_type)
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
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(attachment.filename)}",
        "X-Content-Type-Options": "nosniff",
        # An attachment never changes (a new message gets new ids): no repeat Gmail fetch.
        "Cache-Control": "private, max-age=31536000, immutable",
    }
    if attachment.mime_type == "application/pdf":
        # The reader's attachment viewer frames it; kanso's own pages can't be framed at all.
        headers["X-Frame-Options"] = "SAMEORIGIN"
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content, media_type=attachment.mime_type, headers=headers)


@router.get("/photos/{photo_id}")
async def contact_photo(photo_id: int, db: DB) -> Response:
    photo = await db.get(ContactPhoto, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(
        photo.content,
        media_type=photo.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            # A changed photo gets a new id.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.get("/logos/{domain}")
async def domain_logo(domain: str, db: DB) -> Response:
    if logos.domain_of(f"x@{domain}") != domain:  # only what thread rows link to
        raise HTTPException(status_code=404, detail="No logo")
    logo = await logos.load(db, domain)
    if logo is None or logo.content is None or logo.mime_type is None:
        raise HTTPException(status_code=404, detail="No logo")
    return Response(
        logo.content,
        media_type=logo.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            # BIMI logos are SVG: never let one run script, even opened on its own.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "private, max-age=604800",
        },
    )
