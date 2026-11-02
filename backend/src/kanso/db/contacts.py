"""Recipient autocomplete: the `contacts` materialized view (see migration 0005) ranks everyone
you wrote to, and everyone who wrote to you a little lower, by how often and how recently."""

import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import Message

log = logging.getLogger(__name__)

_SEARCH = text("""
SELECT name, email FROM contacts
WHERE (name || ' ' || email) ILIKE :anywhere
ORDER BY (email LIKE :prefix OR lower(name) LIKE :prefix OR lower(name) LIKE :word) DESC,
    score DESC, email
LIMIT :limit
""")


async def search(db: AsyncSession, query: str, limit: int = 8) -> list[dict[str, str]]:
    q = query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await db.execute(
        _SEARCH, {"anywhere": f"%{q}%", "prefix": f"{q}%", "word": f"% {q}%", "limit": limit}
    )
    return [{"name": name, "email": email} for name, email in rows]


# The newest message id at the last refresh: new mail since then means a refresh is due.
_refreshed_at_id: int | None = None


async def refresh(db: AsyncSession, if_changed: bool = False) -> bool:
    """Recompute the view (a few seconds on a very large mailbox; reads go on meanwhile)."""
    global _refreshed_at_id
    newest = await db.scalar(select(func.max(Message.id)))
    if if_changed and newest == _refreshed_at_id:
        return False
    await db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY contacts"))
    await db.commit()
    _refreshed_at_id = newest
    return True
