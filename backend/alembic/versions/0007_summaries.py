"""thread summaries

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "summaries",
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("message_id", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("summaries")
