"""the order of accounts in the account switcher

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("position", sa.Integer(), server_default="0", nullable=False)
    )
    op.execute("UPDATE accounts SET position = id")  # the order they were connected in


def downgrade() -> None:
    op.drop_column("accounts", "position")
