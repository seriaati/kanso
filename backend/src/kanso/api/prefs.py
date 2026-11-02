"""Settings changed on the settings page (kanso.db.prefs). The password has its own route
(POST /api/auth/password)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db import prefs
from kanso.db.session import get_session

router = APIRouter(prefix="/settings")
DB = Annotated[AsyncSession, Depends(get_session)]


class SettingsOut(BaseModel):
    # The assistant asks before it changes mail (archive, label, star, mark read). Off: it
    # changes mail at once; each change is still shown in the chat and can be undone.
    assistant_confirm: bool


class SettingsIn(BaseModel):
    assistant_confirm: bool | None = None


@router.get("")
async def get_settings(db: DB) -> SettingsOut:
    return SettingsOut(assistant_confirm=await prefs.assistant_confirm(db))


@router.patch("")
async def update_settings(body: SettingsIn, db: DB) -> SettingsOut:
    if body.assistant_confirm is not None:
        await prefs.put(db, prefs.ASSISTANT_CONFIRM, body.assistant_confirm)
        await db.commit()
    return await get_settings(db)
