"""The mail mirror. Migrations in alembic/versions are the source of truth for the DDL (extensions,
expression indexes); these models describe the same tables for queries."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    """A connected Gmail account. Every other table references it with ON DELETE CASCADE, so
    deleting the row (disconnect) removes all of that account's mirrored data."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7))
    # Fernet-encrypted (kanso.core.security.encrypt), never stored in plain text.
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Fake mailbox from `python -m kanso.demo_seed`: never synced with Google.
    demo: Mapped[bool] = mapped_column(server_default=false())

    # Sync. "backfill" until the full history is mirrored (also during a full resync), then
    # "ready" for incremental History API syncs from `history_id`.
    sync_state: Mapped[str] = mapped_column(String(10), server_default="backfill")
    sync_error: Mapped[str | None] = mapped_column(Text)  # last failure; None when healthy
    history_id: Mapped[int | None] = mapped_column(BigInteger)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Backfill progress, persisted after every page so a restart resumes where it stopped.
    backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_page_token: Mapped[str | None] = mapped_column(Text)
    backfill_done: Mapped[int] = mapped_column(server_default="0")
    backfill_total: Mapped[int | None]  # Gmail's estimate, from the profile
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Label(Base):
    """A Gmail label, system (INBOX, UNREAD, …) or user-made."""

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("account_id", "gmail_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    gmail_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(10))  # "system" | "user"
    color: Mapped[str | None] = mapped_column(String(7))  # Gmail's background color
    # kanso-local settings, not stored in Gmail. What belongs under the label, in plain words
    # (the classifier reads it); whether the classifier may apply it; whether the inbox shows
    # its threads as one collapsed bundle row.
    description: Mapped[str] = mapped_column(Text, server_default="")
    ai_managed: Mapped[bool] = mapped_column(server_default=false())
    is_bundle: Mapped[bool] = mapped_column(server_default=false())


class Thread(Base):
    """A Gmail thread. Everything except the ids is derived from its messages by
    kanso.sync.store.refresh_threads, which every write path calls."""

    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("account_id", "gmail_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    gmail_id: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(Text, server_default="")
    snippet: Mapped[str] = mapped_column(Text, server_default="")
    senders: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    message_count: Mapped[int] = mapped_column(server_default="0")
    label_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    unread: Mapped[bool] = mapped_column(server_default=false())
    starred: Mapped[bool] = mapped_column(server_default=false())
    important: Mapped[bool] = mapped_column(server_default=false())
    in_inbox: Mapped[bool] = mapped_column(server_default=false())
    # True only when every message of the thread is in Trash (or Spam).
    in_trash: Mapped[bool] = mapped_column(server_default=false())
    in_spam: Mapped[bool] = mapped_column(server_default=false())
    # Every message is a draft: such a thread is in Drafts only, never in Done.
    drafts_only: Mapped[bool] = mapped_column(server_default=false())
    has_attachments: Mapped[bool] = mapped_column(server_default=false())
    lang: Mapped[str | None] = mapped_column(String(16))
    # The user labeled or unlabeled this thread by hand (in kanso or in Gmail): the classifier
    # never touches it again. Not derived, so refresh_threads leaves it alone.
    labels_manual: Mapped[bool] = mapped_column(server_default=false())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("account_id", "gmail_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    gmail_id: Mapped[str] = mapped_column(String(32))
    internal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text)
    from_name: Mapped[str] = mapped_column(Text)
    from_email: Mapped[str] = mapped_column(Text)
    # Lists of {"name": ..., "email": ...}.
    to_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    cc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    bcc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reply_to: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Threading headers, for replies in Phase 5.
    message_id_header: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references: Mapped[str | None] = mapped_column(Text)
    # Plain text: the text/plain part, or text extracted from the HTML when there is none.
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)  # sanitized with nh3
    lang: Mapped[str | None] = mapped_column(String(16))  # BCP 47 hint, e.g. "ja", "zh-Hant"
    has_attachments: Mapped[bool] = mapped_column(server_default=false())
    size: Mapped[int] = mapped_column(server_default="0")
    # When the message was last fetched from Gmail; a full resync drops rows it didn't touch.
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search: Mapped[Any] = mapped_column(TSVECTOR)


class MessageLabel(Base):
    __tablename__ = "message_labels"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[int] = mapped_column(
        ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Attachment(Base):
    """Metadata only. The content is fetched from Gmail on demand (api/attachments.py)."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Position among the message's MIME leaf parts (kanso.gmail.mime), to find it again.
    part_index: Mapped[int]
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int]
    content_id: Mapped[str | None] = mapped_column(Text)  # for cid: references in the HTML
    inline: Mapped[bool] = mapped_column(server_default=false())


class Outbox(Base):
    """A change made in kanso, waiting to be written back to Gmail (kanso.sync.outbox). It is
    already applied to the mirror; the processor sends each account's entries in order. Every
    entry is a label delta on a fixed set of messages, so sending it twice changes nothing. Adding
    or removing TRASH goes out as messages.trash / messages.untrash."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))  # what the user did, e.g. "done", "undo"
    message_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))  # Gmail message ids
    add_labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # Gmail ids
    remove_labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    # "pending" until sent; "sent", or "failed" after the last attempt.
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(Base):
    """A message being written (kanso.compose). Its content is saved as a real Gmail draft (for
    the demo account: in the mirror only), whose current message is mirrored like any other.
    Attachment bytes are never stored here: `attachments` holds metadata, the bytes live in the
    Gmail draft (or the original being forwarded) with a short-lived file cache on disk."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(10))  # "new" | "reply" | "forward"
    gmail_draft_id: Mapped[str | None] = mapped_column(Text)  # None until saved (or demo)
    gmail_message_id: Mapped[str | None] = mapped_column(Text, index=True)  # the draft's message
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    message_id_header: Mapped[str] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references: Mapped[str | None] = mapped_column(Text)
    to_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    cc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    bcc_addrs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    subject: Mapped[str] = mapped_column(Text, server_default="")
    body_html: Mapped[str] = mapped_column(Text, server_default="")  # what you wrote, sanitized
    # The quoted original (reply) or forwarded message, below the body; not editable.
    quote_html: Mapped[str | None] = mapped_column(Text)
    quote_text: Mapped[str | None] = mapped_column(Text)
    # [{id, filename, mime_type, size, content_id, src}], src = {account_id, gmail_id,
    # part_index}: the Gmail message holding the bytes (None while only in the file cache).
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # Set once the final message is in the Gmail draft, right before drafts.send. A draft with
    # it set is never saved again, only sent: a retry after a crash can't send it twice.
    sending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiQueue(Base):
    """A thread waiting to be classified into a label (kanso.ai.labeling). New inbox mail is
    queued by the incremental sync; "Label existing inbox" queues a run (`run_id`). Rows stay a
    few days after they finish, for the run's progress."""

    __tablename__ = "ai_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(BigInteger)  # None: new mail
    # "pending", then "labeled", "unlabeled" (nothing fit well enough), "skipped" or "failed".
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    label: Mapped[str | None] = mapped_column(Text)  # the model's answer
    confidence: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiUsage(Base):
    """Tokens used per day, AI feature and model (kanso.ai.usage prices them)."""

    __tablename__ = "ai_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    feature: Mapped[str] = mapped_column(String(20), primary_key=True)  # e.g. "labeling"
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    requests: Mapped[int] = mapped_column(server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")


class Summary(Base):
    """A thread's summary (kanso.ai.summaries). It is current while `message_id` is still the
    thread's latest message (drafts left out); a new message makes it stale."""

    __tablename__ = "summaries"

    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[str] = mapped_column(String(32))  # Gmail id of the latest message
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))  # "demo" for the demo mailbox's canned ones
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantConversation(Base):
    """A conversation with the assistant (kanso.ai.assistant). Only the most recent ones are
    kept."""

    __tablename__ = "assistant_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, server_default="")  # the first question, shortened
    # The user's IANA time zone, from the browser: dates shown to Claude are in it.
    timezone: Mapped[str] = mapped_column(String(64), server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantMessage(Base):
    """One Messages API turn, stored exactly as sent or received (thinking, text, tool calls and
    tool results), so the conversation can be sent again unchanged and resumed."""

    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # API content blocks
    text: Mapped[str | None] = mapped_column(Text)  # user turns: what the user typed
    # Assistant turns: the changes it proposed, each waiting for the user's Confirm or Cancel
    # ({id (the tool_use id), tool, action, label, threads [{id, subject}], status, entries}).
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # While proposals wait: the results of the turn's other tool calls ({tool_use_id: block}).
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    """A setting changed in kanso itself (kanso.db.prefs), as opposed to .env: the login
    password's hash once it has been changed, and the assistant's confirmation opt-out."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    """A browser or installed PWA that asked for notifications of new inbox mail
    (kanso.sync.notify). The endpoint is the browser vendor's push service URL for it."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)  # the browser's public key, base64url
    auth: Mapped[str] = mapped_column(Text)  # the browser's auth secret, base64url
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
