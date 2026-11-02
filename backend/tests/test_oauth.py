import asyncio
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest

from kanso.core.security import encrypt
from kanso.db.models import Account
from kanso.gmail import oauth


@pytest.fixture
def account() -> Account:
    oauth._access_tokens.clear()
    return Account(id=7, email="me@gmail.com", refresh_token=encrypt("the-refresh-token"))


def mock_google(monkeypatch: pytest.MonkeyPatch, *responses: httpx.Response) -> list[dict]:
    """Serve the given token responses in order; return the forms Google received."""
    forms: list[dict] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        forms.append(parse_qs(request.content.decode()))
        return queue.pop(0)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(oauth, "_client", lambda: httpx.AsyncClient(transport=transport))
    return forms


def token(value: str, expires_in: int = 3599) -> httpx.Response:
    return httpx.Response(200, json={"access_token": value, "expires_in": expires_in})


def test_access_token_is_refreshed_and_cached(
    monkeypatch: pytest.MonkeyPatch, account: Account
) -> None:
    forms = mock_google(monkeypatch, token("first"))
    assert asyncio.run(oauth.access_token(account)) == "first"
    assert asyncio.run(oauth.access_token(account)) == "first"  # cached, no second request
    assert len(forms) == 1
    assert forms[0]["grant_type"] == ["refresh_token"]
    assert forms[0]["refresh_token"] == ["the-refresh-token"]


def test_expired_access_token_is_refreshed(
    monkeypatch: pytest.MonkeyPatch, account: Account
) -> None:
    forms = mock_google(monkeypatch, token("short", expires_in=30), token("second"))
    assert asyncio.run(oauth.access_token(account)) == "short"
    assert asyncio.run(oauth.access_token(account)) == "second"  # 30s is inside the margin
    assert len(forms) == 2


def test_revoked_refresh_token(monkeypatch: pytest.MonkeyPatch, account: Account) -> None:
    mock_google(monkeypatch, httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(oauth.ReconnectRequired):
        asyncio.run(oauth.access_token(account))
