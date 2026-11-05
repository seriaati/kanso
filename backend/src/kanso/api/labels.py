"""Label changes go to Gmail directly, not through the outbox: they are rare, and Gmail's answer
(a name it refuses, a clash) shows at once."""

import logging
import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db import redis as events
from kanso.db.models import Account, Label, Thread
from kanso.db.session import get_session
from kanso.gmail import client as gmail_client
from kanso.sync import store

log = logging.getLogger(__name__)

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


# Gmail accepts only its own palette. The mirror stores Gmail's color and the UI draws the
# nearest kanso color, which maps back to the one picked.
KansoColor = Literal["#A07338", "#4F6E8C", "#6B6B66", "#556B3F", "#87607E", "#A35A3A", "#B8B3A2"]
GMAIL_COLORS: dict[str, str] = {
    "#A07338": "#aa8831",  # Home, ochre
    "#4F6E8C": "#285bac",  # Personal, slate
    "#6B6B66": "#666666",  # Work, stone
    "#556B3F": "#1a764d",  # Travel, moss
    "#87607E": "#8e63ce",  # Finance, plum
    "#A35A3A": "#cc3a21",  # Health, rust
    "#B8B3A2": "#c2c2c2",  # sand
}
LabelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=225)]


def gmail_color(color: str) -> dict[str, str]:
    text_color = "#000000" if color == "#B8B3A2" else "#ffffff"  # dark text on the light sand
    return {"backgroundColor": GMAIL_COLORS[color], "textColor": text_color}


Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
# One of the UI's label icons (frontend/src/app/mail.ts labelIcons); "" is none, a colored dot.
IconName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z]{0,32}$")]


class LabelCreate(BaseModel):
    name: LabelName
    color: KansoColor | None = None
    account_id: int
    description: Description = ""
    icon: IconName = ""


class LabelUpdate(BaseModel):
    name: LabelName | None = None
    color: KansoColor | None = None
    # kanso-local settings (not stored in Gmail): changing only these makes no Gmail call.
    description: Description | None = None
    icon: IconName | None = None


class LabelOut(BaseModel):
    id: int
    account_id: int
    gmail_id: str
    name: str
    type: str  # "system" | "user"
    color: str | None
    description: str
    icon: str | None
    threads_total: int
    threads_unread: int
    messages_total: int


# Thread counts follow the thread list: threads entirely in Trash or Spam count only there.
_THREAD_COUNTS = text("""
SELECT u.label_id, count(*) AS total, count(*) FILTER (WHERE t.unread) AS unread
FROM threads t
CROSS JOIN LATERAL unnest(t.label_ids) AS u(label_id)
JOIN labels l ON l.id = u.label_id
WHERE t.account_id = :account_id
  AND (l.gmail_id IN ('TRASH', 'SPAM') OR NOT (t.in_trash OR t.in_spam))
GROUP BY u.label_id
""")
_MESSAGE_COUNTS = text("""
SELECT ml.label_id, count(*)
FROM message_labels ml JOIN labels l ON l.id = ml.label_id
WHERE l.account_id = :account_id
GROUP BY ml.label_id
""")


@router.get("/labels")
async def list_labels(db: DB, account_id: int) -> list[LabelOut]:
    stmt = select(Label).where(Label.account_id == account_id).order_by(Label.type, Label.name)
    params = {"account_id": account_id}
    threads = {row.label_id: row for row in await db.execute(_THREAD_COUNTS, params)}
    messages: dict[int, int] = dict((await db.execute(_MESSAGE_COUNTS, params)).all())
    result = []
    for label in await db.scalars(stmt):
        counts = threads.get(label.id)
        result.append(
            LabelOut(
                id=label.id,
                account_id=label.account_id,
                gmail_id=label.gmail_id,
                name=label.name,
                type=label.type,
                color=label.color,
                description=label.description,
                icon=label.icon,
                threads_total=counts.total if counts else 0,
                threads_unread=counts.unread if counts else 0,
                messages_total=messages.get(label.id, 0),
            )
        )
    return result


def _gmail_failed(error: gmail_client.GmailError) -> HTTPException:
    if error.status == 409:
        return HTTPException(status_code=409, detail="Gmail already has a label with that name")
    return HTTPException(status_code=502, detail=f"Gmail refused the change ({error.status})")


async def _check_name(db: AsyncSession, account_id: int, name: str, label_id: int = 0) -> None:
    """Names are unique per account, ignoring case (Gmail's rule), system labels included."""
    clash = await db.scalar(
        select(Label.id).where(
            Label.account_id == account_id,
            func.lower(Label.name) == name.lower(),
            Label.id != label_id,
        )
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail=f"A label named “{name}” already exists")


def _out(label: Label) -> LabelOut:
    return LabelOut(
        id=label.id,
        account_id=label.account_id,
        gmail_id=label.gmail_id,
        name=label.name,
        type=label.type,
        color=label.color,
        description=label.description,
        icon=label.icon,
        threads_total=0,
        threads_unread=0,
        messages_total=0,
    )


async def _user_label(db: AsyncSession, label_id: int) -> tuple[Label, Account]:
    row = (
        await db.execute(select(Label, Account).join(Account).where(Label.id == label_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Label not found")
    label, account = row
    if label.type != "user":
        raise HTTPException(status_code=400, detail="System labels can't be changed")
    return label, account


@router.post("/labels", status_code=201)
async def create_label(body: LabelCreate, db: DB) -> LabelOut:
    account = await db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await _check_name(db, account.id, body.name)
    color = gmail_color(body.color) if body.color else None
    if account.demo:
        gmail_id = f"Label_{secrets.token_hex(6)}"
    else:
        request: dict[str, Any] = {
            "name": body.name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        if color:
            request["color"] = color
        async with gmail_client.http_client() as http:
            try:
                response = await gmail_client.Gmail(account, http).create_label(request)
            except gmail_client.GmailError as error:
                raise _gmail_failed(error) from error
        gmail_id = response["id"]
    background = color["backgroundColor"] if color else None
    await store.save_label(db, account.id, gmail_id, body.name, background)
    label = await db.scalar(
        select(Label).where(Label.account_id == account.id, Label.gmail_id == gmail_id)
    )
    assert label is not None
    label.description = body.description
    label.icon = body.icon or None
    await db.commit()
    return _out(label)


@router.patch("/labels/{label_id}")
async def update_label(label_id: int, body: LabelUpdate, db: DB) -> LabelOut:
    label, account = await _user_label(db, label_id)
    await edit(db, label, account, body)
    await db.commit()
    return _out(label)


async def edit(db: AsyncSession, label: Label, account: Account, body: LabelUpdate) -> None:
    """Changes a user label in Gmail and the mirror (also the assistant's edit_label). The caller
    commits."""
    request: dict[str, Any] = {}
    if body.name is not None and body.name != label.name:
        await _check_name(db, account.id, body.name, label.id)
        request["name"] = body.name
    if body.color is not None:
        request["color"] = gmail_color(body.color)
    if body.description is not None:
        label.description = body.description
    if body.icon is not None:
        label.icon = body.icon or None
    if request and not account.demo:
        async with gmail_client.http_client() as http:
            try:
                await gmail_client.Gmail(account, http).update_label(label.gmail_id, request)
            except gmail_client.GmailError as error:
                raise _gmail_failed(error) from error
    if "name" in request:
        label.name = request["name"]
    if "color" in request:
        label.color = request["color"]["backgroundColor"]


@router.delete("/labels/{label_id}", status_code=204)
async def delete_label(label_id: int, db: DB) -> None:
    label, account = await _user_label(db, label_id)
    thread_ids = await remove(db, label, account)
    await db.commit()
    try:
        await events.threads_changed(account.id, thread_ids)
    except Exception as error:
        log.warning("could not publish to Redis: %s", error)


async def remove(db: AsyncSession, label: Label, account: Account) -> set[int]:
    """Deletes a user label in Gmail and the mirror (also the assistant's delete_label); the mail
    stays. Returns the threads that had it. The caller commits."""
    if not account.demo:
        async with gmail_client.http_client() as http:
            try:
                await gmail_client.Gmail(account, http).delete_label(label.gmail_id)
            except gmail_client.NotFound:
                pass  # already gone from Gmail
            except gmail_client.GmailError as error:
                raise _gmail_failed(error) from error
    labeled = select(Thread.id).where(Thread.label_ids.contains([label.id]))
    thread_ids = set(await db.scalars(labeled))
    await db.delete(label)  # its message_labels rows cascade
    await db.flush()
    await store.refresh_threads(db, thread_ids)
    return thread_ids
