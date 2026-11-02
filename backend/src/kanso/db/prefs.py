"""Settings changed in kanso itself, stored in the `settings` table (key → JSON value). `.env`
holds everything else; these few are changed from the settings page:

- `password_hash`: the login password, once changed in kanso. PASSWORD_HASH in .env is the
  starting value, and `python -m kanso.reset_password` goes back to it.
- `assistant_confirm`: whether the assistant asks before it changes mail (default: it asks).
- `session_epoch`: the login session generation; "Log out everywhere" adds one (default 0).
- `ai_key_rejected`: Anthropic refused the API key (kanso.ai.usage); gone after a call works.
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
