"""drafts being composed; contacts for recipient autocomplete

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Refreshed by the worker when new mail has arrived (kanso.compose.contacts).
CONTACTS = """
CREATE MATERIALIZED VIEW contacts AS
WITH mine AS (
    SELECT lower(email) AS email FROM accounts
), mail AS (
    SELECT m.* FROM messages m
    WHERE NOT EXISTS (
        SELECT 1 FROM message_labels ml JOIN labels l ON l.id = ml.label_id
        WHERE ml.message_id = m.id AND l.gmail_id = 'DRAFT'
    )
), seen AS (
    SELECT lower(a->>'email') AS email, coalesce(a->>'name', '') AS name, m.internal_date,
        1.0 AS weight
    FROM mail m
    CROSS JOIN LATERAL jsonb_array_elements(m.to_addrs || m.cc_addrs || m.bcc_addrs) a
    WHERE lower(m.from_email) IN (SELECT email FROM mine)
    UNION ALL
    SELECT lower(m.from_email), m.from_name, m.internal_date, 0.2
    FROM mail m
    WHERE lower(m.from_email) NOT IN (SELECT email FROM mine)
)
SELECT email,
    (array_agg(name ORDER BY (name <> '') DESC, internal_date DESC))[1] AS name,
    sum(weight * (1 + 9 * exp(
        least(0, extract(epoch FROM internal_date - now())) / (86400 * 60)
    ))) AS score
FROM seen
WHERE email LIKE '%_@_%'
GROUP BY email
"""


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("gmail_draft_id", sa.Text()),
        sa.Column("gmail_message_id", sa.Text(), index=True),
        sa.Column("gmail_thread_id", sa.Text()),
        sa.Column("message_id_header", sa.Text(), nullable=False),
        sa.Column("in_reply_to", sa.Text()),
        sa.Column("references", sa.Text()),
        *(
            sa.Column(name, postgresql.JSONB(), server_default="[]", nullable=False)
            for name in ("to_addrs", "cc_addrs", "bcc_addrs")
        ),
        sa.Column("subject", sa.Text(), server_default="", nullable=False),
        sa.Column("body_html", sa.Text(), server_default="", nullable=False),
        sa.Column("quote_html", sa.Text()),
        sa.Column("quote_text", sa.Text()),
        sa.Column("attachments", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("sending_since", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.execute(CONTACTS)
    op.execute("CREATE UNIQUE INDEX contacts_email_idx ON contacts (email)")
    op.execute(
        "CREATE INDEX contacts_trgm_idx ON contacts USING gin ((name || ' ' || email) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW contacts")
    op.drop_table("drafts")
