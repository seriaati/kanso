import base64
import binascii
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.core.settings import settings
from kanso.db.models import FcmToken, PushSubscription
from kanso.db.session import get_session
from kanso.sync import notify

router = APIRouter(prefix="/notifications")
DB = Annotated[AsyncSession, Depends(get_session)]
log = logging.getLogger(__name__)

ANDROID_PACKAGE = "app.kanso"


class Keys(BaseModel):
    p256dh: str
    auth: str


class Subscription(BaseModel):
    """The JSON of a browser's PushSubscription."""

    endpoint: str
    keys: Keys


class Endpoint(BaseModel):
    endpoint: str


class Token(BaseModel):
    """The Android app's FCM registration token."""

    token: str = Field(min_length=1, max_length=4096)


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


@router.get("/firebase")
async def firebase() -> dict[str, str]:
    """What the Android app starts Firebase with, from FIREBASE_ANDROID_CONFIG (the app's
    google-services.json). 404 when kanso can't send to the app: no config, or no service
    account to send with."""
    if not settings.firebase_android_config or not settings.google_pubsub_credentials:
        raise HTTPException(404, "This kanso server isn't set up for notifications in the app")
    try:
        config = json.loads(base64.b64decode(settings.firebase_android_config))
        project = config["project_info"]
        app = next(
            client
            for client in config["client"]
            if client["client_info"]["android_client_info"]["package_name"] == ANDROID_PACKAGE
        )
        return {
            "project_id": project["project_id"],
            "sender_id": project["project_number"],
            "app_id": app["client_info"]["mobilesdk_app_id"],
            "api_key": app["api_key"][0]["current_key"],
        }
    except (ValueError, KeyError, IndexError, TypeError, StopIteration):
        log.warning(
            "FIREBASE_ANDROID_CONFIG isn't a base64 google-services.json for %s", ANDROID_PACKAGE
        )
        raise HTTPException(
            404, "FIREBASE_ANDROID_CONFIG isn't usable: see the server's log"
        ) from None


@router.put("/fcm", status_code=204)
async def register_app(body: Token, db: DB) -> Response:
    await db.execute(insert(FcmToken).values(token=body.token).on_conflict_do_nothing())
    await db.commit()
    return Response(status_code=204)


@router.delete("/fcm", status_code=204)
async def unregister_app(body: Token, db: DB) -> Response:
    await db.execute(delete(FcmToken).where(FcmToken.token == body.token))
    await db.commit()
    return Response(status_code=204)
