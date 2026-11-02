"""Each action changes the mirror at once and queues the Gmail write-back in the outbox, in one
transaction."""

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.api.threads import section_thread_ids
from kanso.db import redis as events
from kanso.db.models import Label, Message, MessageLabel, Thread
from kanso.db.session import get_session
from kanso.sync import outbox, store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/threads")
DB = Annotated[AsyncSession, Depends(get_session)]

Action = Literal[
    "done",
    "inbox",
    "delete",
    "untrash",
    "star",
    "unstar",
    "important",
    "unimportant",
    "read",
    "unread",
    "label",
    "unlabel",
]

# action -> (add or remove, Gmail label id (None: the named user label), which messages).
# "latest": the latest message only, as Gmail does for stars and "mark unread".
ACTIONS: dict[str, tuple[Literal["add", "remove"], str | None, Literal["all", "latest"]]] = {
    "done": ("remove", "INBOX", "all"),
    "inbox": ("add", "INBOX", "all"),
    "delete": ("add", "TRASH", "all"),
    "untrash": ("remove", "TRASH", "all"),
    "star": ("add", "STARRED", "latest"),
    "unstar": ("remove", "STARRED", "all"),
    "important": ("add", "IMPORTANT", "all"),
    "unimportant": ("remove", "IMPORTANT", "all"),
    "read": ("remove", "UNREAD", "all"),
    "unread": ("add", "UNREAD", "latest"),
    "label": ("add", None, "all"),
    "unlabel": ("remove", None, "all"),
}


class Section(BaseModel):
    """A section or bundle of the grouped inbox (kanso.api.threads), for "Done all"."""

    name: str | None  # the label; None for the section of unlabeled threads
    bundle: bool = False
    account_id: int
    # The newest thread the user saw in it: anything that arrived later is left alone.
    until: datetime


class ActionIn(BaseModel):
    action: Action
    thread_ids: list[int] = Field(default=[], max_length=1000)
    section: Section | None = None  # instead of thread_ids
    label: str | None = None  # the user label's name, for "label" and "unlabel"

    @model_validator(mode="after")
    def _targets(self) -> "ActionIn":
        if not self.thread_ids and self.section is None:
            raise ValueError("thread_ids or section is required")
        return self


class ActionOut(BaseModel):
    entries: list[int]  # outbox entry ids: pass them to /threads/undo


class UndoIn(BaseModel):
    entries: list[int] = Field(min_length=1, max_length=1000)


class UndoOut(BaseModel):
    thread_ids: list[int]


async def publish(touched: dict[int, set[int]], wake: bool) -> None:
    """Tell browsers and the worker; the change is committed either way."""
    try:
        for account_id, thread_ids in touched.items():
            await events.threads_changed(account_id, thread_ids)
        if wake:
            await events.wake_outbox()
    except Exception as error:
        log.warning("could not publish to Redis (%s); the worker polls the outbox anyway", error)


async def apply_action(
    db: AsyncSession, action: str, thread_ids: list[int], label: str | None = None
) -> tuple[list[int], dict[int, set[int]]]:
    """Returns the outbox entry ids and the touched thread ids by account. The caller commits."""
    mode, gmail_label, which = ACTIONS[action]
    threads = list(await db.scalars(select(Thread).where(Thread.id.in_(thread_ids))))
    if not threads:
        raise HTTPException(status_code=404, detail="Thread not found")
    by_account: dict[int, list[int]] = {}
    for thread in threads:
        by_account.setdefault(thread.account_id, []).append(thread.id)

    # The Gmail label id per account; a user label goes by name.
    label_ids: dict[int, str] = {}
    for account_id in by_account:
        if gmail_label is not None:
            label_ids[account_id] = gmail_label
            continue
        if not label:
            raise HTTPException(status_code=422, detail="Which label?")
        gmail_id = await db.scalar(
            select(Label.gmail_id).where(
                Label.account_id == account_id, Label.type == "user", Label.name == label
            )
        )
        if gmail_id is None:
            raise HTTPException(status_code=400, detail=f"There is no label named “{label}”")
        label_ids[account_id] = gmail_id

    entries: list[int] = []
    touched: dict[int, set[int]] = {}
    for account_id in sorted(by_account):  # a fixed lock order
        await outbox.lock_account(db, account_id)
        target = label_ids[account_id]
        rows = (
            await db.execute(
                select(Message.gmail_id, Message.thread_id, Message.internal_date, Message.id)
                .where(Message.thread_id.in_(by_account[account_id]))
                .order_by(Message.thread_id, Message.internal_date, Message.id)
            )
        ).all()
        label_rows = await db.execute(
            select(MessageLabel.message_id, Label.gmail_id)
            .join(Label)
            .where(MessageLabel.message_id.in_([r.id for r in rows]))
        )
        has: dict[int, set[str]] = {}
        for message_id, gmail_id in label_rows:
            has.setdefault(message_id, set()).add(gmail_id)

        message_ids: list[str] = []
        if mode == "remove":
            message_ids = [r.gmail_id for r in rows if target in has.get(r.id, ())]
        else:
            by_thread: dict[int, list] = {}
            for r in rows:
                by_thread.setdefault(r.thread_id, []).append(r)
            for messages in by_thread.values():
                lacking = [
                    m
                    for m in messages
                    if target not in has.get(m.id, ()) and "DRAFT" not in has.get(m.id, ())
                ]
                if which == "latest":
                    if lacking and not any(target in has.get(m.id, ()) for m in messages):
                        message_ids.append(lacking[-1].gmail_id)
                else:
                    message_ids += [m.gmail_id for m in lacking]
        if not message_ids:
            continue  # already so

        add, remove = ([target], []) if mode == "add" else ([], [target])
        changed = await store.apply_delta(db, account_id, message_ids, add, remove)
        await store.refresh_threads(db, changed)
        entry = outbox.enqueue(db, account_id, action, message_ids, add, remove)
        await db.flush()
        entries.append(entry.id)
        touched[account_id] = changed
    return entries, touched


@router.post("/actions")
async def thread_action(body: ActionIn, db: DB) -> ActionOut:
    thread_ids = body.thread_ids
    if body.section is not None:
        s = body.section
        thread_ids = await section_thread_ids(db, s.name, s.bundle, s.account_id, s.until)
        if not thread_ids:
            return ActionOut(entries=[])
    entries, touched = await apply_action(db, body.action, thread_ids, body.label)
    if body.action in ("label", "unlabel"):
        await store.mark_manual(db, thread_ids)  # AI labeling never overrides the user
    await db.commit()
    await publish(touched, wake=bool(entries))
    return ActionOut(entries=entries)


@router.post("/undo")
async def undo(body: UndoIn, db: DB) -> UndoOut:
    touched = await outbox.undo(db, body.entries)
    await db.commit()
    await publish(touched, wake=True)
    return UndoOut(thread_ids=sorted({t for ids in touched.values() for t in ids}))
