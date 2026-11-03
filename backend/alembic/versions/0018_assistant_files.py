"""the names of the files attached to an assistant question

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_messages",
        sa.Column("files", postgresql.JSONB(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("assistant_messages", "files")
