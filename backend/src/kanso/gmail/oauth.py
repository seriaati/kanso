"""Google OAuth 2.0 web flow and access tokens for connected Gmail accounts."""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx2 as httpx

from kanso.core.security import decrypt
from kanso.core.settings import settings
from kanso.db.models import Account

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# gmail.modify covers reading, labels, history, watch, drafts and send. It only lacks permanent
# deletion that bypasses Trash, which kanso never does.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SCOPES = f"openid email {GMAIL_SCOPE}"


class OAuthError(Exception):
    pass


class ReconnectRequired(OAuthError):
    """Google rejected the refresh token (revoked, or expired): the account must reconnect."""


@dataclass
class Grant:
    email: str
    refresh_token: str


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


def authorization_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # offline + consent: Google returns a refresh token every time, also on reconnect.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    form |= {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret}
    async with _client() as client:
        response = await client.post(TOKEN_URL, data=form)
    body = response.json()
    if body.get("error") == "invalid_grant":
        raise ReconnectRequired(body.get("error_description", "invalid_grant"))
    if response.is_error:
        raise OAuthError(f"Google token endpoint returned {response.status_code}: {body}")
    return body


async def exchange_code(code: str) -> Grant:
    body = await _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.redirect_uri}
    )
    # Google lets the user untick individual permissions on the consent screen.
    if GMAIL_SCOPE not in body.get("scope", "").split():
        raise OAuthError("Gmail access was not granted. Connect again and allow access to Gmail.")
    if "refresh_token" not in body:
        raise OAuthError("Google did not return a refresh token.")
    # The ID token comes straight from Google's token endpoint over TLS, so per OpenID Connect
    # its signature needn't be checked.
    payload = body["id_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return Grant(email=claims["email"], refresh_token=body["refresh_token"])


# account id -> (access token, expiry). Per process: the app and worker each refresh their own.
_access_tokens: dict[int, tuple[str, float]] = {}


async def access_token(account: Account) -> str:
    """A valid access token for the account, refreshed from its refresh token when needed."""
    cached = _access_tokens.get(account.id)
    if cached and cached[1] > time.time():
        return cached[0]
    body = await _token_request(
        {"grant_type": "refresh_token", "refresh_token": decrypt(account.refresh_token)}
    )
    # Refresh a minute early so a token never expires mid-request.
    _access_tokens[account.id] = (body["access_token"], time.time() + body["expires_in"] - 60)
    return body["access_token"]


def forget_access_token(account: Account) -> None:
    _access_tokens.pop(account.id, None)


async def revoke(account: Account) -> None:
    """Best effort: remove kanso's access from the Google account."""
    forget_access_token(account)
    try:
        async with _client() as client:
            await client.post(REVOKE_URL, data={"token": decrypt(account.refresh_token)})
    except httpx.HTTPError as error:
        log.warning("could not revoke the Google grant for account %s: %s", account.id, error)
