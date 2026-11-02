"""AI labeling and bundles: label settings, the manual-label marker, the classification queue and
AI usage

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # kanso-local label settings; Gmail doesn't store them.
    op.add_column("labels", sa.Column("description", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "labels", sa.Column("ai_managed", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "labels", sa.Column("is_bundle", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "threads",
        sa.Column("labels_manual", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.create_table(
        "ai_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("label", sa.Text()),
        sa.Column("confidence", sa.Float()),
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
    # A thread waits in the queue at most once.
    op.create_index(
        "ai_queue_pending_thread",
        "ai_queue",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ai_queue_run", "ai_queue", ["run_id"])

    op.create_table(
        "ai_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("feature", sa.String(20), primary_key=True),
        sa.Column("model", sa.String(100), primary_key=True),
        sa.Column("requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("ai_queue")
    op.drop_column("threads", "labels_manual")
    for column in ("is_bundle", "ai_managed", "description"):
        op.drop_column("labels", column)
