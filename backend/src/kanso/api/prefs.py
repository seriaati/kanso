from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import usage
from kanso.db import prefs
from kanso.db.session import get_session

router = APIRouter(prefix="/settings")
DB = Annotated[AsyncSession, Depends(get_session)]


class SettingsOut(BaseModel):
    # Off: the assistant changes mail at once; each change is still shown and can be undone.
    assistant_confirm: bool
    labeling_model: str
    summary_model: str
    assistant_model: str
    # The choices: the models with known prices, so usage costs stay complete.
    models: list[str]


class SettingsIn(BaseModel):
    assistant_confirm: bool | None = None
    labeling_model: str | None = None
    summary_model: str | None = None
    assistant_model: str | None = None


@router.get("")
async def get_settings(db: DB) -> SettingsOut:
    return SettingsOut(
        assistant_confirm=await prefs.assistant_confirm(db),
        labeling_model=await prefs.model(db, "labeling"),
        summary_model=await prefs.model(db, "summary"),
        assistant_model=await prefs.model(db, "assistant"),
        models=list(usage.PRICES),
    )


@router.patch("")
async def update_settings(body: SettingsIn, db: DB) -> SettingsOut:
    if body.assistant_confirm is not None:
        await prefs.put(db, prefs.ASSISTANT_CONFIRM, body.assistant_confirm)
    for feature in prefs.MODEL_DEFAULTS:
        model = getattr(body, f"{feature}_model")
        if model is None:
            continue
        if model not in usage.PRICES:
            raise HTTPException(status_code=422, detail=f"Unknown model: {model}")
        await prefs.put(db, f"model_{feature}", model)
    await db.commit()
    return await get_settings(db)
