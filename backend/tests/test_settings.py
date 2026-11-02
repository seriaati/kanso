"""The settings page's backend: changing the password (stored in the database, every other
session signed out, reset from the CLI) and the assistant's confirmation opt-out."""

import pytest
from conftest import PASSWORD, TEST_DATABASE_URL, run
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso import reset_password
from kanso.core import security
from kanso.core.hardening import CSRF_HEADER, CSRF_VALUE
from kanso.db import session
from kanso.main import app

pytestmark = pytest.mark.usefixtures("db", "unpooled")


@pytest.fixture
def unpooled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several clients here, each with its own event loop: no connection pool shared by them."""
    maker = async_sessionmaker(
        create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool), expire_on_commit=False
    )
    for module in (session, security, reset_password):
        monkeypatch.setattr(module, "Session", maker)


NEW = "a brand new password"


def login(password: str) -> TestClient:
    client = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})
    client.post("/api/auth/login", json={"password": password})
    return client


def change(api: TestClient, current: str, new: str) -> int:
    return api.post("/api/auth/password", json={"current": current, "new": new}).status_code


def test_change_password(logged_in: TestClient) -> None:
    other = login(PASSWORD)  # another device
    assert other.get("/api/accounts").status_code == 200

    assert change(logged_in, "not it", NEW) == 400  # the session stays: not a 401
    assert change(logged_in, PASSWORD, "short") == 422
    assert logged_in.get("/api/accounts").status_code == 200

    assert change(logged_in, PASSWORD, NEW) == 204
    # Every other session is signed out; this browser got a new cookie.
    assert other.get("/api/accounts").status_code == 401
    assert logged_in.get("/api/accounts").status_code == 200
    assert login(PASSWORD).get("/api/accounts").status_code == 401
    assert login(NEW).get("/api/accounts").status_code == 200

    # The stored password outlives a restart (the hash is read from the database again).
    security.remember_password_hash(None)
    assert login(NEW).get("/api/accounts").status_code == 200
    assert login(PASSWORD).get("/api/accounts").status_code == 401


def test_log_out_everywhere(logged_in: TestClient) -> None:
    other = login(PASSWORD)  # another device
    old_cookie = logged_in.cookies[security.SESSION_COOKIE]

    def with_cookie(cookie: str, csrf: bool = True) -> TestClient:
        client = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE} if csrf else {})
        client.cookies.set(security.SESSION_COOKIE, cookie)
        return client

    assert (
        with_cookie(old_cookie, csrf=False).post("/api/auth/logout-everywhere").status_code == 403
    )
    assert logged_in.post("/api/auth/logout-everywhere").status_code == 204
    # Every session is signed out, this browser's too, and its old cookie is refused.
    assert logged_in.get("/api/accounts").status_code == 401
    assert other.get("/api/accounts").status_code == 401
    assert with_cookie(old_cookie).get("/api/accounts").status_code == 401
    # Logging in again works, also after a restart (the generation is read from the database).
    again = login(PASSWORD)
    assert again.get("/api/accounts").status_code == 200
    security.remember_session_epoch(None)
    assert again.get("/api/accounts").status_code == 200
    assert with_cookie(old_cookie).get("/api/accounts").status_code == 401
    # Changing the password still signs the others out.
    assert change(again, PASSWORD, NEW) == 204
    assert again.get("/api/accounts").status_code == 200
    assert login(NEW).get("/api/accounts").status_code == 200


def test_wrong_current_passwords_are_rate_limited(logged_in: TestClient) -> None:
    for _ in range(5):
        assert change(logged_in, "guess", NEW) == 400
    assert change(logged_in, PASSWORD, NEW) == 429


def test_reset_password_cli(logged_in: TestClient) -> None:
    assert change(logged_in, PASSWORD, NEW) == 204
    assert run(reset_password.reset()) is True
    security.remember_password_hash(None)  # the app restarts
    assert logged_in.get("/api/accounts").status_code == 401  # signed out
    assert login(NEW).get("/api/accounts").status_code == 401
    assert login(PASSWORD).get("/api/accounts").status_code == 200
    assert run(reset_password.reset()) is False  # nothing left to reset


@pytest.mark.parametrize(("had", "says"), [(True, "from .env again"), (False, "never changed")])
def test_reset_password_cli_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], had: bool, says: str
) -> None:
    async def fake() -> bool:
        return had

    monkeypatch.setattr(reset_password, "_main", fake)
    reset_password.main()
    assert says in capsys.readouterr().out


def test_assistant_confirmation_setting(logged_in: TestClient) -> None:
    assert logged_in.get("/api/settings").json() == {"assistant_confirm": True}  # default: ask
    response = logged_in.patch("/api/settings", json={"assistant_confirm": False})
    assert response.json() == {"assistant_confirm": False}
    assert logged_in.get("/api/settings").json() == {"assistant_confirm": False}
    assert logged_in.patch("/api/settings", json={}).json() == {"assistant_confirm": False}
    logged_in.patch("/api/settings", json={"assistant_confirm": True})
    assert logged_in.get("/api/settings").json() == {"assistant_confirm": True}
