"""A trigram index on the names and addresses in To and Cc, for to: searches.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-02

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The expression must match kanso.db.search.RECIPIENTS for the planner to use it.
    op.execute(
        "CREATE INDEX messages_recipients_trgm_idx ON messages USING gin "
        "((jsonb_path_query_array(to_addrs || cc_addrs, '$[*].*'::jsonpath)::text) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX messages_recipients_trgm_idx")
