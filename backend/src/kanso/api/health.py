import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Response

from kanso.db.redis import check_redis
from kanso.db.session import check_db

router = APIRouter()


async def _status(check: Callable[[], Awaitable[None]]) -> str:
    try:
        async with asyncio.timeout(2):
            await check()
    except Exception:
        return "error"
    return "ok"


@router.get("/health")
async def health(response: Response) -> dict[str, str]:
    db, redis = await asyncio.gather(_status(check_db), _status(check_redis))
    if "error" in (db, redis):
        response.status_code = 503
    # "app" tells the Android app that a kanso server answers at the address it was given.
    return {"app": "kanso", "db": db, "redis": redis}
