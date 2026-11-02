import asyncio
from typing import Annotated

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.core.security import (
    clear_session_cookie,
    password_hash,
    remember_password_hash,
    remember_session_epoch,
    require_session,
    session_epoch,
    set_session_cookie,
    verify_password,
)
from kanso.core.settings import DEMO_PASSWORD, settings
from kanso.db import prefs
from kanso.db.redis import redis
from kanso.db.session import get_session

router = APIRouter(prefix="/auth")
DB = Annotated[AsyncSession, Depends(get_session)]

# Failed logins per client IP and window before 429s.
MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
MIN_PASSWORD = 8  # as kanso.hash_password


class Login(BaseModel):
    password: str


class DemoOut(BaseModel):
    password: str | None  # a demo instance's password, for its login page; None otherwise


class PasswordChange(BaseModel):
    current: str
    new: str = Field(min_length=MIN_PASSWORD, max_length=1000)


async def _check_password(request: Request, password: str) -> str:
    """Returns the current hash. The attempt counts before the slow check, so parallel guesses
    can't get past the limit."""
    key = f"login-failures:{request.client.host if request.client else 'unknown'}"
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, WINDOW_SECONDS)
    if attempts > MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(max(await redis.ttl(key), 1))},
        )
    current = await password_hash()
    # argon2 is deliberately slow; keep it off the event loop.
    if not await asyncio.to_thread(verify_password, password, current):
        raise HTTPException(status_code=401, detail="Wrong password")
    await redis.delete(key)
    return current


@router.get("/demo")
async def demo() -> DemoOut:
    """Public, like login: the login page of a demo instance shows its password."""
    return DemoOut(password=DEMO_PASSWORD if settings.demo_mode else None)


@router.post("/login", status_code=204)
async def login(body: Login, request: Request, response: Response) -> None:
    set_session_cookie(
        response, await _check_password(request, body.password), await session_epoch()
    )


@router.post("/logout", status_code=204, dependencies=[Depends(require_session)])
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.post("/password", status_code=204, dependencies=[Depends(require_session)])
async def change_password(
    body: PasswordChange, request: Request, response: Response, db: DB
) -> None:
    """Stored in the database (PASSWORD_HASH in .env is the starting value). Every session is
    signed out, except this browser, which gets a new one."""
    if settings.demo_mode:  # visitors share the password shown on the login page
        raise HTTPException(status_code=403, detail="The demo's password can't be changed")
    try:
        await _check_password(request, body.current)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        # Not 401: this session is fine, only the password typed was wrong.
        raise HTTPException(status_code=400, detail="The current password is wrong") from error
    new_hash = await asyncio.to_thread(PasswordHasher().hash, body.new)
    await prefs.put(db, prefs.PASSWORD_HASH, new_hash)
    await db.commit()
    remember_password_hash(new_hash)
    set_session_cookie(response, new_hash, await session_epoch())


@router.post("/logout-everywhere", status_code=204, dependencies=[Depends(require_session)])
async def logout_everywhere(response: Response, db: DB) -> None:
    """The session generation goes up, so every cookie signed before no longer matches."""
    epoch = await session_epoch() + 1
    await prefs.put(db, prefs.SESSION_EPOCH, epoch)
    await db.commit()
    remember_session_epoch(epoch)
    clear_session_cookie(response)
