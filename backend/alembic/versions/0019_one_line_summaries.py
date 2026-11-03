"""summaries are now one English sentence: drop the old ones so they are made again

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-03

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM summaries")


def downgrade() -> None:
    pass
