import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.core.security import encrypt
from kanso.core.settings import settings
from kanso.db.models import Account
from kanso.db.redis import redis
from kanso.db.session import get_session
from kanso.gmail import oauth

router = APIRouter(prefix="/accounts")
DB = Annotated[AsyncSession, Depends(get_session)]

COLORS = ("#2F4B6E", "#A07338", "#556B3F", "#87607E", "#A35A3A", "#4F6E8C", "#6B6B66")
STATE_TTL_SECONDS = 10 * 60


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    color: str
    avatar_url: str | None


class AccountUpdate(BaseModel):
    display_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


async def _get(db: AsyncSession, account_id: int) -> Account:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("")
async def list_accounts(db: DB) -> list[AccountOut]:
    accounts = await db.scalars(select(Account).order_by(Account.id))
    return [AccountOut.model_validate(a) for a in accounts]


@router.patch("/{account_id}")
async def update_account(account_id: int, body: AccountUpdate, db: DB) -> AccountOut:
    account = await _get(db, account_id)
    if body.display_name is not None:
        account.display_name = body.display_name
    if body.color is not None:
        account.color = body.color.upper()
    await db.commit()
    return AccountOut.model_validate(account)


@router.delete("/{account_id}", status_code=204)
async def disconnect_account(account_id: int, db: DB) -> None:
    account = await _get(db, account_id)
    if not account.demo:
        await oauth.revoke(account)
    await db.delete(account)  # mirrored data cascades from the account row
    await db.commit()


@router.get("/connect")
async def connect() -> Response:
    if settings.demo_mode:
        return PlainTextResponse(
            "This is a kanso demo: it shows made-up mail and can't connect Gmail accounts.",
            status_code=403,
        )
    if not settings.google_client_id or not settings.google_client_secret:
        return PlainTextResponse(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in "
            ".env (see docs/SETUP.md) and restart kanso.",
            status_code=503,
        )
    state = secrets.token_urlsafe(32)
    await redis.set(f"oauth-state:{state}", 1, ex=STATE_TTL_SECONDS)
    return RedirectResponse(oauth.authorization_url(state), status_code=303)


@router.get("/callback")
async def callback(db: DB, state: str = "", code: str = "", error: str = "") -> Response:
    if not state or not await redis.getdel(f"oauth-state:{state}"):
        return PlainTextResponse(
            "This connect link has expired. Go back to kanso and connect again.", status_code=400
        )
    if error or not code:  # e.g. the user cancelled on Google's consent screen
        return RedirectResponse("/", status_code=303)
    try:
        grant = await oauth.exchange_code(code)
    except oauth.OAuthError as e:
        return PlainTextResponse(f"Could not connect the account: {e}", status_code=400)

    account = await db.scalar(select(Account).where(Account.email == grant.email))
    if account:  # reconnecting an existing account keeps its name, color and mail
        account.refresh_token = encrypt(grant.refresh_token)
        account.avatar_url = grant.picture
        account.photos_synced_at = None  # Contacts access may be new: sync photos again now
    else:
        count = await db.scalar(select(func.count()).select_from(Account)) or 0
        db.add(
            Account(
                email=grant.email,
                display_name=grant.email.split("@")[0],
                color=COLORS[count % len(COLORS)],
                avatar_url=grant.picture,
                refresh_token=encrypt(grant.refresh_token),
            )
        )
    await db.commit()
    return RedirectResponse("/", status_code=303)
