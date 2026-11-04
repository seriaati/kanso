"""Settings changed in kanso itself; `.env` holds everything else.

`password_hash` overrides PASSWORD_HASH from .env until `python -m kanso.reset_password`.
"""

from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import Setting

PASSWORD_HASH = "password_hash"
ASSISTANT_CONFIRM = "assistant_confirm"
SESSION_EPOCH = "session_epoch"
AI_KEY_REJECTED = "ai_key_rejected"
# AI labeling of new inbox mail, with an API key set. On unless turned off.
AI_LABELING = "ai_labeling"
# The Claude model per AI feature, keyed by the usage table's feature names.
MODEL_DEFAULTS = {
    "labeling": "claude-haiku-5-5",
    "summary": "claude-haiku-5-5",
    "assistant": "claude-sonnet-5-5",
}


async def get(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.get(Setting, key, populate_existing=True)
    return default if row is None else row.value


async def put(db: AsyncSession, key: str, value: Any) -> None:
    stmt = insert(Setting).values(key=key, value=value)
    changes = {"value": stmt.excluded.value, "updated_at": func.now()}
    await db.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_=changes))


async def remove(db: AsyncSession, key: str) -> None:
    await db.execute(delete(Setting).where(Setting.key == key))


async def assistant_confirm(db: AsyncSession) -> bool:
    return bool(await get(db, ASSISTANT_CONFIRM, True))


async def ai_labeling(db: AsyncSession) -> bool:
    return bool(await get(db, AI_LABELING, True))


async def model(db: AsyncSession, feature: str) -> str:
    return str(await get(db, f"model_{feature}", MODEL_DEFAULTS[feature]))
