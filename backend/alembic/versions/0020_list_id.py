"""messages.list_id, the List-Id header, for list: searches

Existing rows stay NULL until the worker fetches their headers.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("list_id", sa.Text()))
    op.execute("CREATE INDEX messages_list_trgm_idx ON messages USING gin (list_id gin_trgm_ops)")


def downgrade() -> None:
    op.drop_index("messages_list_trgm_idx")
    op.drop_column("messages", "list_id")
