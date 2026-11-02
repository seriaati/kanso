"""outbox for write-backs to Gmail; threads.drafts_only

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("message_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("add_labels", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "remove_labels", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    # The processor picks the oldest pending entry of each account.
    op.execute(
        "CREATE INDEX outbox_pending_idx ON outbox (account_id, id) WHERE status = 'pending'"
    )

    op.add_column(
        "threads",
        sa.Column("drafts_only", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute("""
        UPDATE threads t SET drafts_only = NOT EXISTS (
            SELECT 1 FROM messages m WHERE m.thread_id = t.id AND NOT EXISTS (
                SELECT 1 FROM message_labels ml JOIN labels l ON l.id = ml.label_id
                WHERE ml.message_id = m.id AND l.gmail_id = 'DRAFT'
            )
        )
    """)


def downgrade() -> None:
    op.drop_column("threads", "drafts_only")
    op.drop_table("outbox")
