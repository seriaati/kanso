"""assistant conversations belong to one account

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Earlier conversations searched every account at once, so none belongs to one account.
    op.execute("DELETE FROM assistant_conversations")
    op.add_column(
        "assistant_conversations",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_assistant_conversations_account_id", "assistant_conversations", ["account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_conversations_account_id", "assistant_conversations")
    op.drop_column("assistant_conversations", "account_id")
