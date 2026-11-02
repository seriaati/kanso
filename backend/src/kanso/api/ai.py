import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import labeling, summaries, usage
from kanso.core.settings import settings
from kanso.db.models import AiQueue, Thread
from kanso.db.session import get_session

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]
USAGE_DAYS = 30


class ConfigOut(BaseModel):
    ai: bool  # an Anthropic API key is set: AI features show


class FeatureUsage(BaseModel):
    feature: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None  # None: no price known for the model


class UsageOut(BaseModel):
    since: date
    features: list[FeatureUsage]
    cost_usd: float  # estimated, from list prices
    complete: bool  # False when a model's price is unknown
    key_rejected: bool  # Anthropic refused the API key on the latest call


class RunOut(BaseModel):
    id: int
    total: int
    pending: int
    labeled: int
    failed: int
    error: str | None  # the latest failure, if any


class LabelInboxOut(BaseModel):
    eligible: int  # inbox threads a new run would classify
    run: RunOut | None  # the latest run


class LabelInboxIn(BaseModel):
    account_id: int | None = None  # None: every account


@router.get("/config")
async def config() -> ConfigOut:
    return ConfigOut(ai=settings.ai_enabled)


@router.get("/ai/usage")
async def ai_usage(db: DB) -> UsageOut:
    summary = await usage.summary(db, USAGE_DAYS)
    return UsageOut.model_validate(summary | {"key_rejected": await usage.key_was_rejected(db)})


async def _latest_run(db: AsyncSession, account_id: int | None) -> RunOut | None:
    scope: list[ColumnElement[bool]] = [AiQueue.run_id.is_not(None)]
    if account_id is not None:
        scope.append(AiQueue.account_id == account_id)
    run_id = await db.scalar(select(func.max(AiQueue.run_id)).where(*scope))
    if run_id is None:
        return None
    rows = await db.execute(
        select(AiQueue.status, func.count())
        .where(AiQueue.run_id == run_id, *scope)
        .group_by(AiQueue.status)
    )
    counts = dict(rows.all())
    error = await db.scalar(
        select(AiQueue.last_error)
        .where(AiQueue.run_id == run_id, AiQueue.status == "failed", *scope)
        .order_by(AiQueue.finished_at.desc())
        .limit(1)
    )
    return RunOut(
        id=run_id,
        total=sum(counts.values()),
        pending=counts.get("pending", 0),
        labeled=counts.get("labeled", 0),
        failed=counts.get("failed", 0),
        error=error,
    )


@router.get("/ai/label-inbox")
async def label_inbox_status(db: DB, account_id: int | None = None) -> LabelInboxOut:
    stmt = labeling.eligible()
    if account_id is not None:
        stmt = stmt.where(Thread.account_id == account_id)
    eligible = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return LabelInboxOut(eligible=eligible, run=await _latest_run(db, account_id))


@router.post("/ai/label-inbox")
async def label_inbox(body: LabelInboxIn, db: DB) -> LabelInboxOut:
    """Only the inbox: there is no AI pass over history."""
    if not settings.ai_enabled:
        raise HTTPException(status_code=409, detail="AI is off: set ANTHROPIC_API_KEY")
    await labeling.enqueue_inbox(db, body.account_id)
    await db.commit()
    await labeling.wake()
    return LabelInboxOut(eligible=0, run=await _latest_run(db, body.account_id))


class SummaryOut(BaseModel):
    text: str
    model: str
    created_at: datetime


class SummaryState(BaseModel):
    messages: int  # drafts left out; 0: nothing to summarize
    long: bool  # long enough to be summarized on open
    lang: str | None  # the thread's language hint
    summary: SummaryOut | None  # the stored summary, if it covers the latest message


async def _summary_context(db: AsyncSession, thread_id: int) -> summaries.Context:
    if not settings.ai_enabled:
        raise HTTPException(status_code=409, detail="AI is off: set ANTHROPIC_API_KEY")
    context = await summaries.load(db, thread_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return context


@router.get("/threads/{thread_id}/summary")
async def thread_summary(thread_id: int, db: DB) -> SummaryState:
    """Never calls Claude."""
    context = await _summary_context(db, thread_id)
    row = await summaries.fresh(db, context)
    return SummaryState(
        messages=len(context.entries),
        long=summaries.is_long(context.entries),
        lang=context.lang,
        summary=None if row is None else SummaryOut.model_validate(summaries.public(row)),
    )


@router.post("/threads/{thread_id}/summary")
async def summarize(thread_id: int, db: DB) -> StreamingResponse:
    context = await _summary_context(db, thread_id)
    if not context.entries:
        raise HTTPException(status_code=409, detail="Nothing to summarize")
    await db.close()  # no connection held while Claude writes

    async def events() -> AsyncIterator[str]:
        async for event in summaries.stream(context):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
