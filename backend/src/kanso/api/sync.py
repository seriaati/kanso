from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db import redis as events
from kanso.db.models import Account
from kanso.db.session import get_session

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


class SyncStatus(BaseModel):
    account_id: int
    email: str
    state: str  # "backfill" | "ready" | "error" | "demo"
    error: str | None
    backfill_done: int  # messages mirrored so far in the current backfill
    backfill_total: int | None  # Gmail's estimate of the mailbox size
    last_synced_at: datetime | None
    push: bool  # a Gmail push watch is active


@router.get("/sync")
async def sync_status(db: DB) -> list[SyncStatus]:
    """The worker also streams changes as "sync" events."""
    now = datetime.now(UTC)
    return [
        SyncStatus(
            account_id=a.id,
            email=a.email,
            state="demo" if a.demo else "error" if a.sync_error else a.sync_state,
            error=a.sync_error,
            backfill_done=a.backfill_done,
            backfill_total=a.backfill_total,
            last_synced_at=a.last_synced_at,
            push=a.watch_expires_at is not None and a.watch_expires_at > now,
        )
        for a in await db.scalars(select(Account).order_by(Account.id))
    ]


@router.post("/sync/{account_id}", status_code=204)
async def sync_now(account_id: int) -> Response:
    """Refresh: the worker syncs the account with Gmail now; changes arrive as live events."""
    await events.request_sync(account_id)
    return Response(status_code=204)
