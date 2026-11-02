"""Notifications of new inbox mail (kanso.sync.notify): the browser subscribes with the push
service of its vendor, then hands kanso the subscription here."""

import binascii
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import PushSubscription
from kanso.db.session import get_session
from kanso.sync import notify

router = APIRouter(prefix="/notifications")
DB = Annotated[AsyncSession, Depends(get_session)]


class Keys(BaseModel):
    p256dh: str
    auth: str


class Subscription(BaseModel):
    """The JSON of a browser's PushSubscription."""

    endpoint: str
    keys: Keys


class Endpoint(BaseModel):
    endpoint: str


@router.get("/key")
async def key() -> dict[str, str]:
    """The public key browsers subscribe with (applicationServerKey)."""
    return {"public_key": notify.vapid_public_key()}


@router.put("/subscription", status_code=204)
async def subscribe(body: Subscription, db: DB) -> Response:
    if not body.endpoint.startswith("https://"):
        raise HTTPException(400, "The push endpoint must be an https:// URL")
    try:
        sizes = (
            len(notify.b64url_decode(body.keys.p256dh)),
            len(notify.b64url_decode(body.keys.auth)),
        )
    except (binascii.Error, ValueError):
        sizes = (0, 0)
    if sizes != (65, 16):
        raise HTTPException(400, "Malformed subscription keys")
    stmt = insert(PushSubscription).values(
        endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth
    )
    keys = {"p256dh": stmt.excluded.p256dh, "auth": stmt.excluded.auth}
    await db.execute(
        stmt.on_conflict_do_update(index_elements=[PushSubscription.endpoint], set_=keys)
    )
    await db.commit()
    return Response(status_code=204)


@router.delete("/subscription", status_code=204)
async def unsubscribe(body: Endpoint, db: DB) -> Response:
    await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    await db.commit()
    return Response(status_code=204)
