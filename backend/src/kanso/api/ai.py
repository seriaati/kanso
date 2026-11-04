import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import icons, keys, labeling, suggestions, summaries, usage
from kanso.core.settings import settings
from kanso.db.models import Account, AiQueue, Thread
from kanso.db.session import get_session

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]
USAGE_DAYS = 30


class ConfigOut(BaseModel):
    ai: bool  # an Anthropic API key is set: AI features show
    labeling: bool  # AI labeling is on: an Anthropic or a TypeSafe key, and on in Settings


class KeysOut(BaseModel):
    """The last characters of each saved key, to recognize it; None: not set."""

    anthropic: str | None
    jev: str | None


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=500)


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
    estimate_usd: float | None  # what labeling them costs, roughly; None: unknown (Jev)
    run: RunOut | None  # the latest run


class LabelInboxIn(BaseModel):
    account_id: int
    # Only threads whose latest message is in this range, when given.
    after: datetime | None = None
    before: datetime | None = None


@router.get("/config")
async def config(db: DB) -> ConfigOut:
    found = await keys.load(db)
    return ConfigOut(ai=found.ai, labeling=await labeling.on(db))


async def _keys_out(db: AsyncSession) -> KeysOut:
    found = await keys.load(db)
    return KeysOut(**{p: getattr(found, p)[-4:] or None for p in keys.PROVIDERS})


@router.get("/ai/keys")
async def get_keys(db: DB) -> KeysOut:
    return await _keys_out(db)


@router.put("/ai/keys/{provider}")
async def put_key(provider: keys.Provider, body: KeyIn, db: DB) -> KeysOut:
    """Checked with the provider first, so a typo never turns AI on."""
    key = body.key.strip()
    if problem := await keys.check(provider, key):
        raise HTTPException(status_code=422, detail=problem)
    await keys.put(db, provider, key)
    await db.commit()
    await labeling.wake()
    return await _keys_out(db)


@router.delete("/ai/keys/{provider}")
async def delete_key(provider: keys.Provider, db: DB) -> KeysOut:
    await keys.remove(db, provider)
    await db.commit()
    await labeling.wake()
    return await _keys_out(db)


@router.get("/ai/usage")
async def ai_usage(db: DB) -> UsageOut:
    summary = await usage.summary(db, USAGE_DAYS)
    return UsageOut.model_validate(summary | {"key_rejected": await usage.key_was_rejected(db)})


async def _latest_run(db: AsyncSession, account_id: int) -> RunOut | None:
    scope = [AiQueue.run_id.is_not(None), AiQueue.account_id == account_id]
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
async def label_inbox_status(
    db: DB, account_id: int, after: datetime | None = None, before: datetime | None = None
) -> LabelInboxOut:
    stmt = labeling.eligible(after, before).where(Thread.account_id == account_id)
    eligible = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return LabelInboxOut(
        eligible=eligible,
        estimate_usd=await labeling.estimate(db, eligible),
        run=await _latest_run(db, account_id),
    )


@router.post("/ai/label-inbox")
async def label_inbox(body: LabelInboxIn, db: DB) -> LabelInboxOut:
    """Only the inbox: there is no AI pass over history."""
    if not await labeling.on(db):
        raise HTTPException(status_code=409, detail=keys.LABELING_OFF)
    await labeling.enqueue_inbox(db, body.account_id, body.after, body.before)
    await db.commit()
    await labeling.wake()
    run = await _latest_run(db, body.account_id)
    return LabelInboxOut(eligible=0, estimate_usd=None, run=run)


class SuggestIn(BaseModel):
    account_id: int
    model: str


@router.post("/ai/label-suggestions")
async def suggest_labels(body: SuggestIn, db: DB) -> list[suggestions.Suggestion]:
    """Claude only, with the model picked for this one call."""
    if not (await keys.load(db)).ai:
        raise HTTPException(status_code=409, detail=keys.AI_OFF)
    if body.model not in usage.PRICES:
        raise HTTPException(status_code=422, detail=f"Unknown model: {body.model}")
    account = await db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    # Demo mail is made up: never sent to the AI, except on a demo instance, where all mail is.
    if account.demo and not settings.demo_mode:
        raise HTTPException(status_code=400, detail="The demo mailbox isn’t sent to the AI.")
    try:
        result = await suggestions.suggest(db, account, body.model)
    except suggestions.Failed as error:
        await db.commit()  # the usage, if Claude answered
        raise HTTPException(status_code=502, detail=str(error)) from error
    await db.commit()
    return result


class IconsIn(BaseModel):
    account_id: int


@router.post("/ai/label-icons")
async def pick_label_icons(body: IconsIn, db: DB) -> list[icons.Picked]:
    """Icons for the account's labels that have none, with the Claude labeling model. Sends
    Claude only the labels' names and descriptions."""
    if not (await keys.load(db)).ai:
        raise HTTPException(status_code=409, detail=keys.AI_OFF)
    account = await db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        result = await icons.pick(db, account, await labeling.claude_model(db))
    except icons.Failed as error:
        await db.commit()  # the usage, if Claude answered
        raise HTTPException(status_code=502, detail=str(error)) from error
    await db.commit()
    return result


class SummaryOut(BaseModel):
    text: str
    model: str
    created_at: datetime


class SummaryState(BaseModel):
    messages: int  # drafts left out; 0: nothing to summarize
    summary: SummaryOut | None  # the stored summary, if it covers the latest message


async def _summary_context(db: AsyncSession, thread_id: int) -> summaries.Context:
    if not (await keys.load(db)).ai:
        raise HTTPException(status_code=409, detail=keys.AI_OFF)
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
