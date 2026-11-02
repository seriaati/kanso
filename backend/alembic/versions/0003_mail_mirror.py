"""mail mirror: sync state, labels, threads, messages, attachments

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _account_fk() -> sa.Column:
    return sa.Column(
        "account_id",
        sa.Integer(),
        sa.ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # Trigram matching for from: searches (a trusted extension: the database owner may create it).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for column in (
        sa.Column("demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sync_state", sa.String(10), server_default="backfill", nullable=False),
        sa.Column("sync_error", sa.Text()),
        sa.Column("history_id", sa.BigInteger()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("backfill_started_at", sa.DateTime(timezone=True)),
        sa.Column("backfill_page_token", sa.Text()),
        sa.Column("backfill_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("backfill_total", sa.Integer()),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("accounts", column)

    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("gmail_id", sa.String(255), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(10), nullable=False),
        sa.Column("color", sa.String(7)),
        sa.UniqueConstraint("account_id", "gmail_id"),
    )

    false = sa.false()
    op.create_table(
        "threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("gmail_id", sa.String(32), nullable=False),
        sa.Column("subject", sa.Text(), server_default="", nullable=False),
        sa.Column("snippet", sa.Text(), server_default="", nullable=False),
        sa.Column("senders", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("label_ids", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
        *(
            sa.Column(flag, sa.Boolean(), server_default=false, nullable=False)
            for flag in (
                "unread",
                "starred",
                "important",
                "in_inbox",
                "in_trash",
                "in_spam",
                "has_attachments",
            )
        ),
        sa.Column("lang", sa.String(16)),
        sa.UniqueConstraint("account_id", "gmail_id"),
    )
    # Thread lists are newest first, unified or per account, optionally by label.
    op.execute("CREATE INDEX threads_list_idx ON threads (last_message_at DESC, id DESC)")
    op.execute(
        "CREATE INDEX threads_account_list_idx "
        "ON threads (account_id, last_message_at DESC, id DESC)"
    )
    op.execute("CREATE INDEX threads_label_ids_idx ON threads USING gin (label_ids)")

    jsonb = postgresql.JSONB()
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("gmail_id", sa.String(32), nullable=False),
        sa.Column("internal_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("from_name", sa.Text(), nullable=False),
        sa.Column("from_email", sa.Text(), nullable=False),
        sa.Column("to_addrs", jsonb, nullable=False),
        sa.Column("cc_addrs", jsonb, nullable=False),
        sa.Column("bcc_addrs", jsonb, nullable=False),
        sa.Column("reply_to", jsonb, nullable=False),
        sa.Column("message_id_header", sa.Text()),
        sa.Column("in_reply_to", sa.Text()),
        sa.Column("references", sa.Text()),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text()),
        sa.Column("lang", sa.String(16)),
        sa.Column("has_attachments", sa.Boolean(), server_default=false, nullable=False),
        sa.Column("size", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("search", postgresql.TSVECTOR()),
        sa.UniqueConstraint("account_id", "gmail_id"),
    )
    op.execute("CREATE INDEX messages_search_idx ON messages USING gin (search)")
    op.execute(
        "CREATE INDEX messages_from_trgm_idx ON messages "
        "USING gin ((from_name || ' ' || from_email) gin_trgm_ops)"
    )

    op.create_table(
        "message_labels",
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "label_id",
            sa.Integer(),
            sa.ForeignKey("labels.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Text()),
        sa.Column("inline", sa.Boolean(), server_default=false, nullable=False),
    )


def downgrade() -> None:
    for table in ("attachments", "message_labels", "messages", "threads", "labels"):
        op.drop_table(table)
    for column in (
        "demo",
        "sync_state",
        "sync_error",
        "history_id",
        "last_synced_at",
        "backfill_started_at",
        "backfill_page_token",
        "backfill_done",
        "backfill_total",
        "watch_expires_at",
    ):
        op.drop_column("accounts", column)
