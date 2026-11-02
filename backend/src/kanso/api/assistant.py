import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import assistant
from kanso.api.actions import publish
from kanso.core.settings import settings
from kanso.db.models import AssistantConversation
from kanso.db.session import get_session

DB = Annotated[AsyncSession, Depends(get_session)]
HISTORY = 20  # conversations in the history list


def _ai_on() -> None:
    if not settings.ai_enabled:
        raise HTTPException(status_code=409, detail="AI is off: set ANTHROPIC_API_KEY")


router = APIRouter(prefix="/assistant", dependencies=[Depends(_ai_on)])


class ConversationOut(BaseModel):
    id: int
    title: str
    updated_at: datetime
    pending: bool  # a proposal waits for Confirm or Cancel


class ConversationDetail(BaseModel):
    id: int
    title: str
    # {"kind": "user" | "text" | "activity", "text"} or {"kind": "action", "action": {...}}
    items: list[dict[str, Any]]
    pending: bool


class AskIn(BaseModel):
    conversation_id: int | None = None  # None: a new conversation
    text: str = Field(min_length=1, max_length=4000)
    thread_id: int | None = None  # the open thread, when "this thread" is on
    timezone: str = Field("UTC", max_length=64)  # the browser's IANA time zone


class DecideIn(BaseModel):
    confirm: bool


class UndoOut(BaseModel):
    thread_ids: list[int]


def _sse(first: list[dict[str, Any]], rest: AsyncIterator[dict[str, Any]] | None) -> Response:
    async def body() -> AsyncIterator[str]:
        for event in first:
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        if rest is not None:
            async for event in rest:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
async def conversations(db: DB) -> list[ConversationOut]:
    return [ConversationOut.model_validate(c) for c in await assistant.recent(db, HISTORY)]


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: int, db: DB) -> ConversationDetail:
    return ConversationDetail.model_validate(await assistant.conversation(db, conversation_id))


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, db: DB) -> None:
    found = await db.get(AssistantConversation, conversation_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(found)
    await db.commit()


@router.post("/messages")
async def ask(body: AskIn, db: DB) -> Response:
    lock = None
    if body.conversation_id is not None:
        lock = await assistant.acquire(body.conversation_id)
    try:
        conversation_id = await assistant.ask(
            db, body.conversation_id, body.text, body.thread_id, body.timezone
        )
        await db.commit()
    except BaseException:
        if lock is not None:
            await assistant.events.redis.delete(lock)
        raise
    lock = lock or await assistant.acquire(conversation_id)
    found = await db.get(AssistantConversation, conversation_id)
    assert found is not None
    first = [{"type": "conversation", "id": found.id, "title": found.title}]
    await db.close()  # no connection held while Claude writes
    return _sse(first, assistant.run(conversation_id, lock))


@router.post("/conversations/{conversation_id}/actions/{action_id}")
async def decide(conversation_id: int, action_id: str, body: DecideIn, db: DB) -> Response:
    lock = await assistant.acquire(conversation_id)
    try:
        action, finished, touched = await assistant.decide(
            db, conversation_id, action_id, body.confirm
        )
        await db.commit()
    except BaseException:
        await assistant.events.redis.delete(lock)
        raise
    if touched:
        await publish(touched, wake=True)
    await db.close()
    first = [{"type": "action", "action": action}]
    if not finished:
        await assistant.events.redis.delete(lock)
        return _sse([*first, {"type": "done", "pending": True}], None)
    return _sse(first, assistant.run(conversation_id, lock))


@router.post("/conversations/{conversation_id}/actions/{action_id}/undo")
async def undo(conversation_id: int, action_id: str, db: DB) -> UndoOut:
    touched = await assistant.undo(db, conversation_id, action_id)
    await db.commit()
    await publish(touched, wake=True)
    return UndoOut(thread_ids=sorted({t for ids in touched.values() for t in ids}))
