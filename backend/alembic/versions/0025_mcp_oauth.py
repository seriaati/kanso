"""OAuth clients and tokens for the MCP server

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_clients",
        sa.Column("client_id", sa.Text(), primary_key=True),
        sa.Column("info", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.String(7), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False, index=True),
        sa.Column(
            "client_id",
            sa.Text(),
            sa.ForeignKey("mcp_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("resource", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mcp_tokens")
    op.drop_table("mcp_clients")
