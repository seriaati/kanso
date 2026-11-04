"""Drop labels.is_bundle and labels.ai_managed: every label is a bundle in the inbox, and AI
labeling uses every label (one switch in Settings turns it on or off)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("labels", "is_bundle")
    op.drop_column("labels", "ai_managed")


def downgrade() -> None:
    for column in ("ai_managed", "is_bundle"):
        op.add_column(
            "labels", sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False)
        )
