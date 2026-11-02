"""Writes to the mail mirror. Every change to messages or their labels goes through here and ends
with refresh_threads(), which keeps the derived thread columns consistent."""

import html
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import Attachment, Label, Message, MessageLabel, Thread
from kanso.db.search import index_text
from kanso.gmail.mime import ParsedMessage

# Gmail label id -> kanso label id, for one account.
LabelMap = dict[str, int]


async def save_labels(db: AsyncSession, account_id: int, labels: list[dict[str, Any]]) -> LabelMap:
    """Mirror the account's labels (from labels.list); labels gone from Gmail are removed."""
    for label in labels:
        values = {
            "name": label["name"],
            "type": label.get("type", "user").lower(),
            "color": (label.get("color") or {}).get("backgroundColor"),
        }
        await db.execute(
            insert(Label)
            .values(account_id=account_id, gmail_id=label["id"], **values)
            .on_conflict_do_update(index_elements=["account_id", "gmail_id"], set_=values)
        )
    current = {label["id"] for label in labels}
    existing = await label_map(db, account_id)
    if gone := [lid for gid, lid in existing.items() if gid not in current]:
        affected = await db.scalars(select(Thread.id).where(Thread.label_ids.overlap(gone)))
        thread_ids = set(affected)
        await db.execute(delete(Label).where(Label.id.in_(gone)))
        await refresh_threads(db, thread_ids)
    return await label_map(db, account_id)


async def save_label(
    db: AsyncSession, account_id: int, gmail_id: str, name: str, color: str | None
) -> None:
    """Insert or update one user label (label management; a sync may have added it already)."""
    values = {"name": name, "type": "user", "color": color}
    await db.execute(
        insert(Label)
        .values(account_id=account_id, gmail_id=gmail_id, **values)
        .on_conflict_do_update(index_elements=["account_id", "gmail_id"], set_=values)
    )


async def label_map(db: AsyncSession, account_id: int) -> LabelMap:
    rows = await db.execute(select(Label.gmail_id, Label.id).where(Label.account_id == account_id))
    return {gmail_id: label_id for gmail_id, label_id in rows}


async def _label_ids(
    db: AsyncSession, account_id: int, labels: LabelMap, gmail_ids: Iterable[str]
) -> list[int]:
    """kanso ids for Gmail label ids. A label we haven't seen yet gets a placeholder row that the
    next label sync names properly."""
    result = []
    for gmail_id in gmail_ids:
        if gmail_id not in labels:
            label_id = await db.scalar(
                insert(Label)
                .values(account_id=account_id, gmail_id=gmail_id, name=gmail_id, type="user")
                .on_conflict_do_update(
                    index_elements=["account_id", "gmail_id"], set_={"gmail_id": gmail_id}
                )
                .returning(Label.id)
            )
            assert label_id is not None
            labels[gmail_id] = label_id
        result.append(labels[gmail_id])
    return result


async def _set_message_labels(db: AsyncSession, message_id: int, label_ids: Iterable[int]) -> None:
    await db.execute(delete(MessageLabel).where(MessageLabel.message_id == message_id))
    rows = [{"message_id": message_id, "label_id": label_id} for label_id in set(label_ids)]
    if rows:
        await db.execute(insert(MessageLabel).values(rows))


async def save_message(
    db: AsyncSession,
    account_id: int,
    meta: dict[str, Any],
    parsed: ParsedMessage,
    labels: LabelMap,
) -> int:
    """Insert or replace one message. `meta` is Gmail's message resource without `raw`
    (id, threadId, labelIds, snippet, internalDate, sizeEstimate). Returns the thread id."""
    thread_id = await db.scalar(
        insert(Thread)
        .values(account_id=account_id, gmail_id=meta["threadId"])
        .on_conflict_do_update(
            index_elements=["account_id", "gmail_id"], set_={"gmail_id": meta["threadId"]}
        )
        .returning(Thread.id)
    )
    assert thread_id is not None
    recipients = " ".join(
        f"{a['name']} {a['email']}" for a in (*parsed.to, *parsed.cc, *parsed.bcc)
    )
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "internal_date": datetime.fromtimestamp(int(meta["internalDate"]) / 1000, UTC),
        "subject": parsed.subject,
        "snippet": html.unescape(meta.get("snippet", "")),
        "from_name": parsed.from_name,
        "from_email": parsed.from_email,
        "to_addrs": parsed.to,
        "cc_addrs": parsed.cc,
        "bcc_addrs": parsed.bcc,
        "reply_to": parsed.reply_to,
        "message_id_header": parsed.message_id,
        "in_reply_to": parsed.in_reply_to,
        "references": parsed.references,
        "body_text": parsed.body_text,
        "body_html": parsed.body_html,
        "lang": parsed.lang,
        "has_attachments": any(not a.inline for a in parsed.attachments),
        "size": int(meta.get("sizeEstimate", 0)),
        "synced_at": func.now(),
        "search": func.to_tsvector(
            "simple",
            index_text(
                parsed.subject,
                parsed.from_name,
                parsed.from_email,
                recipients,
                *(a.filename for a in parsed.attachments),
                parsed.body_text,
            ),
        ),
    }
    message_id = await db.scalar(
        insert(Message)
        .values(account_id=account_id, gmail_id=meta["id"], **values)
        .on_conflict_do_update(index_elements=["account_id", "gmail_id"], set_=values)
        .returning(Message.id)
    )
    assert message_id is not None
    await db.execute(delete(Attachment).where(Attachment.message_id == message_id))
    if parsed.attachments:
        await db.execute(
            insert(Attachment).values(
                [{"message_id": message_id, **vars(a)} for a in parsed.attachments]
            )
        )
    label_ids = await _label_ids(db, account_id, labels, meta.get("labelIds", []))
    await _set_message_labels(db, message_id, label_ids)
    return thread_id


async def set_labels(
    db: AsyncSession, account_id: int, gmail_id: str, gmail_label_ids: list[str], labels: LabelMap
) -> int | None:
    """Replace a known message's labels (from a format=minimal fetch). Returns its thread id, or
    None if the message isn't in the mirror."""
    row = (
        await db.execute(
            update(Message)
            .where(Message.account_id == account_id, Message.gmail_id == gmail_id)
            .values(synced_at=func.now())
            .returning(Message.id, Message.thread_id)
        )
    ).first()
    if row is None:
        return None
    await _set_message_labels(db, row.id, await _label_ids(db, account_id, labels, gmail_label_ids))
    return row.thread_id


async def change_labels(
    db: AsyncSession,
    account_id: int,
    gmail_id: str,
    add: list[str],
    remove: list[str],
    labels: LabelMap,
) -> tuple[int, set[int]] | None:
    """Apply a History API label change. Returns the thread id and the kanso label ids that
    actually changed (a change the mirror already has, such as the echo of kanso's own
    write-back, changes none), or None for an unknown message."""
    row = (
        await db.execute(
            select(Message.id, Message.thread_id).where(
                Message.account_id == account_id, Message.gmail_id == gmail_id
            )
        )
    ).first()
    if row is None:
        return None
    changed: set[int] = set()
    if add:
        add_ids = await _label_ids(db, account_id, labels, add)
        added = await db.scalars(
            insert(MessageLabel)
            .values([{"message_id": row.id, "label_id": label_id} for label_id in add_ids])
            .on_conflict_do_nothing()
            .returning(MessageLabel.label_id)
        )
        changed.update(added)
    if remove_ids := [labels[g] for g in remove if g in labels]:
        removed = await db.scalars(
            delete(MessageLabel)
            .where(MessageLabel.message_id == row.id, MessageLabel.label_id.in_(remove_ids))
            .returning(MessageLabel.label_id)
        )
        changed.update(removed)
    return row.thread_id, changed


async def apply_delta(
    db: AsyncSession,
    account_id: int,
    message_ids: list[str],
    add: list[str],
    remove: list[str],
) -> set[int]:
    """Add and remove Gmail labels on messages (by Gmail id) in the mirror: a kanso action, or a
    pending one re-applied over synced state. Messages no longer mirrored are skipped. Returns
    the affected thread ids (for refresh_threads)."""
    rows = (
        await db.execute(
            select(Message.id, Message.thread_id).where(
                Message.account_id == account_id, Message.gmail_id.in_(message_ids)
            )
        )
    ).all()
    if not rows:
        return set()
    ids = [row.id for row in rows]
    labels = await label_map(db, account_id)
    if add:
        add_ids = await _label_ids(db, account_id, labels, add)
        await db.execute(
            insert(MessageLabel)
            .values([{"message_id": m, "label_id": label} for m in ids for label in add_ids])
            .on_conflict_do_nothing()
        )
    if remove_ids := [labels[g] for g in remove if g in labels]:
        await db.execute(
            delete(MessageLabel).where(
                MessageLabel.message_id.in_(ids), MessageLabel.label_id.in_(remove_ids)
            )
        )
    return {row.thread_id for row in rows}


async def mark_manual(db: AsyncSession, thread_ids: Iterable[int]) -> None:
    """The user labeled or unlabeled these threads by hand: AI labeling leaves them alone."""
    if ids := list(set(thread_ids)):
        await db.execute(update(Thread).where(Thread.id.in_(ids)).values(labels_manual=True))


async def delete_messages(db: AsyncSession, account_id: int, gmail_ids: Iterable[str]) -> set[int]:
    """Remove messages; returns the affected thread ids (for refresh_threads)."""
    gmail_ids = list(gmail_ids)
    if not gmail_ids:
        return set()
    result = await db.scalars(
        delete(Message)
        .where(Message.account_id == account_id, Message.gmail_id.in_(gmail_ids))
        .returning(Message.thread_id)
    )
    return set(result)


async def delete_unsynced(db: AsyncSession, account_id: int, since: datetime) -> set[int]:
    """After a full pass over the mailbox: remove messages the pass didn't see."""
    result = await db.scalars(
        delete(Message)
        .where(Message.account_id == account_id, Message.synced_at < since)
        .returning(Message.thread_id)
    )
    return set(result)


_REFRESH_THREADS = text("""
WITH m AS (
    SELECT * FROM messages WHERE thread_id = ANY(:ids)
), agg AS (
    SELECT thread_id,
        count(*) AS message_count,
        max(internal_date) AS last_message_at,
        (array_agg(subject ORDER BY internal_date, id))[1] AS subject,
        (array_agg(snippet ORDER BY internal_date DESC, id DESC))[1] AS snippet,
        (array_agg(lang ORDER BY internal_date DESC, id DESC))[1] AS lang,
        bool_or(has_attachments) AS has_attachments,
        -- Trash and Spam hold a thread only when all of its messages are there.
        bool_and(EXISTS (
            SELECT 1 FROM message_labels ml JOIN labels l ON l.id = ml.label_id
            WHERE ml.message_id = m.id AND l.gmail_id = 'TRASH'
        )) AS in_trash,
        bool_and(EXISTS (
            SELECT 1 FROM message_labels ml JOIN labels l ON l.id = ml.label_id
            WHERE ml.message_id = m.id AND l.gmail_id = 'SPAM'
        )) AS in_spam,
        bool_and(EXISTS (
            SELECT 1 FROM message_labels ml JOIN labels l ON l.id = ml.label_id
            WHERE ml.message_id = m.id AND l.gmail_id = 'DRAFT'
        )) AS drafts_only
    FROM m GROUP BY thread_id
), snd AS (
    SELECT thread_id, array_agg(name ORDER BY first_at) AS senders
    FROM (
        SELECT thread_id, coalesce(nullif(from_name, ''), from_email) AS name,
            min(internal_date) AS first_at
        FROM m GROUP BY 1, 2
    ) s
    GROUP BY thread_id
), lbl AS (
    SELECT m.thread_id,
        array_agg(DISTINCT l.id ORDER BY l.id) AS label_ids,
        bool_or(l.gmail_id = 'UNREAD') AS unread,
        bool_or(l.gmail_id = 'STARRED') AS starred,
        bool_or(l.gmail_id = 'IMPORTANT') AS important,
        bool_or(l.gmail_id = 'INBOX') AS in_inbox
    FROM m
    JOIN message_labels ml ON ml.message_id = m.id
    JOIN labels l ON l.id = ml.label_id
    GROUP BY m.thread_id
)
UPDATE threads t SET
    message_count = agg.message_count,
    last_message_at = agg.last_message_at,
    subject = agg.subject,
    snippet = agg.snippet,
    lang = agg.lang,
    has_attachments = agg.has_attachments,
    senders = snd.senders,
    label_ids = coalesce(lbl.label_ids, '{}'),
    unread = coalesce(lbl.unread, false),
    starred = coalesce(lbl.starred, false),
    important = coalesce(lbl.important, false),
    in_inbox = coalesce(lbl.in_inbox, false),
    in_trash = agg.in_trash,
    in_spam = agg.in_spam,
    drafts_only = agg.drafts_only
FROM agg
JOIN snd USING (thread_id)
LEFT JOIN lbl USING (thread_id)
WHERE t.id = agg.thread_id
""")


async def refresh_threads(db: AsyncSession, thread_ids: Iterable[int]) -> None:
    """Recompute the derived thread columns from their messages; drop threads left empty."""
    ids = list(set(thread_ids))
    if not ids:
        return
    await db.execute(_REFRESH_THREADS, {"ids": ids})
    await db.execute(
        delete(Thread).where(
            Thread.id.in_(ids), ~select(Message.id).where(Message.thread_id == Thread.id).exists()
        )
    )
