"""sender photos from Google Contacts

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("photos_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "contact_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.UniqueConstraint("account_id", "email"),
    )


def downgrade() -> None:
    op.drop_table("contact_photos")
    op.drop_column("accounts", "photos_synced_at")
