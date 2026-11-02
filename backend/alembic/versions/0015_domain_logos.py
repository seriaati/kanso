"""sender logos by domain: BIMI, else Google's favicon service

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_logos",
        sa.Column("domain", sa.Text(), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("source", sa.String(10), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("domain_logos")
