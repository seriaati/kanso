import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from kanso.core.security import decrypt
from kanso.core.settings import settings
from kanso.db.models import Account
from kanso.gmail import oauth

pytestmark = pytest.mark.usefixtures("db")


PHOTO = "https://lh3.googleusercontent.com/a/photo=s96-c"


def id_token(email: str, name: str | None) -> str:
    claims = {"email": email, "name": name, "picture": PHOTO}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


class FakeGoogle:
    def __init__(self, email: str = "me@gmail.com", scope: str = oauth.SCOPES) -> None:
        self.email = email
        self.name: str | None = "Me Myself"
        self.scope = scope
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url == oauth.REVOKE_URL:
            return httpx.Response(200)
        form = parse_qs(request.content.decode())
        assert form["client_secret"] == ["client-secret"]
        assert form["code"] == ["auth-code"]
        assert form["redirect_uri"] == ["http://testserver/api/accounts/callback"]
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "expires_in": 3599,
                "refresh_token": f"refresh-for-{self.email}",
                "scope": self.scope,
                "id_token": id_token(self.email, self.name),
            },
        )


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(oauth, "_client", lambda: httpx.AsyncClient(transport=transport))
    return fake


def connect(client: TestClient) -> httpx.Response:
    """Run the OAuth flow: kanso -> (fake) Google consent -> callback."""
    response = client.get("/api/accounts/connect", follow_redirects=False)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    params = parse_qs(location.query)
    assert f"{location.netloc}{location.path}" == "accounts.google.com/o/oauth2/v2/auth"
    assert params["scope"] == [
        "openid email profile https://www.googleapis.com/auth/gmail.modify"
        " https://www.googleapis.com/auth/contacts.readonly"
        " https://www.googleapis.com/auth/contacts.other.readonly"
    ]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["redirect_uri"] == ["http://testserver/api/accounts/callback"]
    return client.get(
        "/api/accounts/callback",
        params={"state": params["state"][0], "code": "auth-code"},
        follow_redirects=False,
    )


async def _stored_accounts() -> list[Account]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = (await conn.execute(select(Account).order_by(Account.id))).all()
    await engine.dispose()
    return [Account(**row._asdict()) for row in rows]


def test_connect_creates_account(logged_in: TestClient, google: FakeGoogle) -> None:
    assert connect(logged_in).headers["location"] == "/"
    assert logged_in.get("/api/accounts").json() == [
        {
            "id": 1,
            "email": "me@gmail.com",
            "display_name": "Me Myself",
            "color": "#2F4B6E",
            "avatar_url": PHOTO,
        }
    ]
    [stored] = asyncio.run(_stored_accounts())
    assert b"refresh-for" not in stored.refresh_token  # encrypted at rest
    assert decrypt(stored.refresh_token) == "refresh-for-me@gmail.com"

    google.email = "work@example.com"
    connect(logged_in)
    accounts = logged_in.get("/api/accounts").json()
    assert [(a["email"], a["color"]) for a in accounts] == [
        ("me@gmail.com", "#2F4B6E"),
        ("work@example.com", "#A07338"),
    ]


def test_reorder_accounts(logged_in: TestClient, google: FakeGoogle) -> None:
    for email in ("a@gmail.com", "b@gmail.com", "c@gmail.com"):
        google.email = email
        connect(logged_in)
    response = logged_in.put("/api/accounts/order", json={"ids": [3, 1, 2]})
    assert [a["id"] for a in response.json()] == [3, 1, 2]
    google.email = "d@gmail.com"
    connect(logged_in)  # a new account goes last
    assert [a["id"] for a in logged_in.get("/api/accounts").json()] == [3, 1, 2, 4]


def test_connect_without_profile_name(logged_in: TestClient, google: FakeGoogle) -> None:
    google.name = None
    connect(logged_in)
    assert logged_in.get("/api/accounts").json()[0]["display_name"] == "me"


def test_reconnect_refreshes_name(logged_in: TestClient, google: FakeGoogle) -> None:
    connect(logged_in)
    google.name = "Renamed In Google"
    connect(logged_in)
    assert [a["display_name"] for a in logged_in.get("/api/accounts").json()] == [
        "Renamed In Google"
    ]


def test_callback_rejects_unknown_state(logged_in: TestClient, google: FakeGoogle) -> None:
    response = logged_in.get("/api/accounts/callback", params={"state": "forged", "code": "x"})
    assert response.status_code == 400
    assert google.requests == []


def test_callback_state_is_single_use(logged_in: TestClient, google: FakeGoogle) -> None:
    location = logged_in.get("/api/accounts/connect", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    params = {"state": state, "code": "auth-code"}
    first = logged_in.get("/api/accounts/callback", params=params, follow_redirects=False)
    assert first.status_code == 303
    assert logged_in.get("/api/accounts/callback", params=params).status_code == 400


def test_callback_requires_gmail_scope(logged_in: TestClient, google: FakeGoogle) -> None:
    google.scope = "openid email"  # the user unticked Gmail on the consent screen
    response = connect(logged_in)
    assert response.status_code == 400
    assert "Gmail access was not granted" in response.text
    assert logged_in.get("/api/accounts").json() == []


def test_user_cancels_consent(logged_in: TestClient, google: FakeGoogle) -> None:
    location = logged_in.get("/api/accounts/connect", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    response = logged_in.get(
        "/api/accounts/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
    assert logged_in.get("/api/accounts").json() == []


def test_disconnect_deletes_and_revokes(logged_in: TestClient, google: FakeGoogle) -> None:
    connect(logged_in)
    assert logged_in.delete("/api/accounts/1").status_code == 204
    assert logged_in.get("/api/accounts").json() == []
    assert asyncio.run(_stored_accounts()) == []
    revoke = google.requests[-1]
    assert revoke.url == oauth.REVOKE_URL
    assert parse_qs(revoke.content.decode())["token"] == ["refresh-for-me@gmail.com"]
    assert logged_in.delete("/api/accounts/1").status_code == 404
