"""labels.icon, a kanso-local icon drawn in the label's color (NULL: a colored dot)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("labels", sa.Column("icon", sa.String(32)))


def downgrade() -> None:
    op.drop_column("labels", "icon")
