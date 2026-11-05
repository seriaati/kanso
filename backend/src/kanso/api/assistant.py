import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Self

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import assistant, keys
from kanso.api.actions import publish
from kanso.db.models import AssistantConversation
from kanso.db.session import get_session
from kanso.sync import notify

DB = Annotated[AsyncSession, Depends(get_session)]
HISTORY = 20  # conversations in the history list


async def _ai_on(db: DB) -> None:
    if not (await keys.load(db)).ai:
        raise HTTPException(status_code=409, detail=keys.AI_OFF)


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


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=100)
    data: str = Field(max_length=34_000_000)  # base64


class AskIn(BaseModel):
    account_id: int  # the account the conversation is about
    conversation_id: int | None = None  # None: a new conversation
    text: str = Field(default="", max_length=4000)
    thread_id: int | None = None  # the open thread, if any
    timezone: str = Field("UTC", max_length=64)  # the browser's IANA time zone
    files: list[FileIn] = Field(default=[], max_length=assistant.FILES_PER_QUESTION)

    @model_validator(mode="after")
    def _asks_something(self) -> Self:
        if not self.text.strip() and not self.files:
            raise ValueError("Write a question or attach a file.")
        return self


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
async def conversations(db: DB, account_id: int) -> list[ConversationOut]:
    return [
        ConversationOut.model_validate(c) for c in await assistant.recent(db, account_id, HISTORY)
    ]


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
            db,
            body.account_id,
            body.conversation_id,
            body.text.strip(),
            body.thread_id,
            body.timezone,
            [f.model_dump() for f in body.files],
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
async def decide(
    conversation_id: int, action_id: str, body: DecideIn, db: DB, background: BackgroundTasks
) -> Response:
    lock = await assistant.acquire(conversation_id)
    try:
        action, finished, touched, unread_before = await assistant.decide(
            db, conversation_id, action_id, body.confirm
        )
        await db.commit()
    except BaseException:
        await assistant.events.redis.delete(lock)
        raise
    if touched:
        await publish(touched, wake=True)
    # Runs once the stream below ends: a clear isn't urgent.
    background.add_task(notify.clear_soon, unread_before)
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
