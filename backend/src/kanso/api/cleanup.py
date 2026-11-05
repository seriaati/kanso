"""The Clean up page, for an inbox that piled up before Kanso: archive what is older than a date,
and clear the inbox sender by sender. Its actions go through /threads/actions (an InboxScope)."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import Account, Message, Thread
from kanso.db.session import get_session

router = APIRouter(prefix="/cleanup")
DB = Annotated[AsyncSession, Depends(get_session)]
SENDERS = 25


class InboxScope(BaseModel):
    """Inbox threads of an account, instead of thread ids."""

    account_id: int
    before: datetime | None = None  # the latest message is older than this
    sender: str | None = None  # one of the messages is from this address


def inbox_threads(scope: InboxScope) -> Any:
    stmt = select(Thread.id).where(
        Thread.account_id == scope.account_id,
        Thread.in_inbox.is_(True),
        Thread.in_trash.is_(False),
        Thread.in_spam.is_(False),
    )
    if scope.before is not None:
        stmt = stmt.where(Thread.last_message_at < scope.before)
    if scope.sender:
        stmt = stmt.where(
            exists().where(
                Message.thread_id == Thread.id,
                func.lower(Message.from_email) == scope.sender.lower(),
            )
        )
    return stmt


async def inbox_thread_ids(db: AsyncSession, scope: InboxScope) -> list[int]:
    return list(await db.scalars(inbox_threads(scope)))


# Per sender (never the account itself): its name, its inbox threads and its newest one's id.
_SENDERS = """
SELECT lower(m.from_email) AS email,
       (array_agg(m.from_name ORDER BY m.internal_date DESC))[1] AS name,
       count(DISTINCT m.thread_id) AS threads,
       (array_agg(m.thread_id ORDER BY m.internal_date DESC))[1] AS thread_id
FROM messages m JOIN threads t ON t.id = m.thread_id
WHERE t.account_id = :account_id AND t.in_inbox AND NOT t.in_trash AND NOT t.in_spam
  AND m.from_email <> '' AND lower(m.from_email) <> lower(:me) {unlabeled}
GROUP BY 1
ORDER BY threads DESC, email
LIMIT :limit
"""
_UNLABELED = """AND NOT t.drafts_only AND NOT EXISTS (
    SELECT 1 FROM labels l WHERE l.id = ANY(t.label_ids) AND l.type = 'user')"""


async def top_senders(
    db: AsyncSession, account: Account, limit: int, unlabeled: bool = False
) -> list[Any]:
    """Rows of email, name, threads and thread_id, most threads first."""
    query = text(_SENDERS.format(unlabeled=_UNLABELED if unlabeled else ""))
    params = {"account_id": account.id, "me": account.email, "limit": limit}
    return list(await db.execute(query, params))


class InboxCount(BaseModel):
    threads: int


class Sender(BaseModel):
    email: str
    name: str
    threads: int
    thread_id: int  # the newest, to unsubscribe from


class SendersOut(BaseModel):
    # Pass it as the scope's `before`: mail that arrives later is left alone.
    as_of: datetime
    senders: list[Sender]


async def _account(db: AsyncSession, account_id: int) -> Account:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("/inbox")
async def inbox_count(db: DB, account_id: int, before: datetime | None = None) -> InboxCount:
    stmt = inbox_threads(InboxScope(account_id=account_id, before=before))
    count = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    return InboxCount(threads=count or 0)


@router.get("/senders")
async def senders(db: DB, account_id: int) -> SendersOut:
    """Senders of two or more inbox threads."""
    account = await _account(db, account_id)
    as_of = datetime.now(UTC)
    rows = await top_senders(db, account, SENDERS)
    return SendersOut(
        as_of=as_of,
        senders=[
            Sender(email=r.email, name=r.name or "", threads=r.threads, thread_id=r.thread_id)
            for r in rows
            if r.threads > 1
        ],
    )
