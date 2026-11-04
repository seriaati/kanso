import json

import httpx2 as httpx
import pytest
from conftest import PASSWORD, TEST_DATABASE_URL, run, set_key
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from kanso import reset_password
from kanso.ai import keys
from kanso.core import security
from kanso.core.hardening import CSRF_HEADER, CSRF_VALUE
from kanso.db import prefs, session
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

    # The stored hash outlives a restart.
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
    assert logged_in.get("/api/accounts").status_code == 401
    assert other.get("/api/accounts").status_code == 401
    assert with_cookie(old_cookie).get("/api/accounts").status_code == 401
    # Logging in again works, also after a restart (the epoch is read from the database).
    again = login(PASSWORD)
    assert again.get("/api/accounts").status_code == 200
    security.remember_session_epoch(None)
    assert again.get("/api/accounts").status_code == 200
    assert with_cookie(old_cookie).get("/api/accounts").status_code == 401
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
    assert logged_in.get("/api/settings").json()["assistant_confirm"] is True  # default: ask
    response = logged_in.patch("/api/settings", json={"assistant_confirm": False})
    assert response.json()["assistant_confirm"] is False
    assert logged_in.get("/api/settings").json()["assistant_confirm"] is False
    assert logged_in.patch("/api/settings", json={}).json()["assistant_confirm"] is False
    logged_in.patch("/api/settings", json={"assistant_confirm": True})
    assert logged_in.get("/api/settings").json()["assistant_confirm"] is True


def test_model_settings(logged_in: TestClient) -> None:
    models = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert logged_in.get("/api/settings").json() == {
        "assistant_confirm": True,
        "ai_labeling": True,
        "labeling_model": "claude-haiku-5-5",
        "summary_model": "claude-haiku-5-5",
        "assistant_model": "claude-sonnet-5-5",
        "models": models,
        "labeling_models": [],  # no API key yet
    }
    response = logged_in.patch("/api/settings", json={"assistant_model": "claude-opus-5-5"})
    assert response.json()["assistant_model"] == "claude-opus-5-5"
    assert response.json()["summary_model"] == "claude-haiku-5-5"  # the others stay
    assert logged_in.patch("/api/settings", json={"labeling_model": "gpt-4"}).status_code == 422
    assert logged_in.get("/api/settings").json()["labeling_model"] == "claude-haiku-5-5"


# AI API keys: set in Settings, checked with the provider, stored encrypted; never from .env.

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


class FakeProviders:
    """The providers' model lists, which a key is checked against before it is saved."""

    def __init__(self) -> None:
        self.status = 200  # 0: unreachable
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status == 0:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(self.status, json={"data": []})


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> FakeProviders:
    fake = FakeProviders()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(keys, "_http", lambda: httpx.AsyncClient(transport=transport))
    return fake


async def _stored(key: str) -> object:
    async with session.Session() as db:
        return await prefs.get(db, key)


def test_api_keys_are_checked_then_stored_encrypted(
    logged_in: TestClient, providers: FakeProviders
) -> None:
    assert logged_in.get("/api/ai/keys").json() == {"anthropic": None, "jev": None}
    assert logged_in.get("/api/config").json() == {"ai": False, "labeling": False}

    for status, problem in (
        (401, "Anthropic rejected this key."),
        (0, "Couldn’t reach Anthropic to check the key. Try again in a moment."),
        (500, "Anthropic answered 500 while checking the key. Try again later."),
    ):
        providers.status = status
        response = logged_in.put("/api/ai/keys/anthropic", json={"key": "sk-ant-wrong"})
        assert (response.status_code, response.json()["detail"]) == (422, problem)
    assert logged_in.get("/api/ai/keys").json() == {"anthropic": None, "jev": None}

    providers.status = 200
    response = logged_in.put("/api/ai/keys/anthropic", json={"key": "  sk-ant-test-1234 "})
    assert response.json() == {"anthropic": "1234", "jev": None}  # only the last characters
    check = providers.requests[-1]
    assert str(check.url) == "https://api.anthropic.com/v1/models?limit=1"
    assert check.headers["x-api-key"] == "sk-ant-test-1234"
    assert logged_in.get("/api/config").json() == {"ai": True, "labeling": True}
    assert "sk-ant-test-1234" not in json.dumps(run(_stored("api_key_anthropic")))

    logged_in.put("/api/ai/keys/jev", json={"key": "ts-key-5678"})
    check = providers.requests[-1]
    assert str(check.url) == "https://api.typesafe.ai/v1/models"
    assert check.headers["authorization"] == "Bearer ts-key-5678"
    assert logged_in.get("/api/ai/keys").json() == {"anthropic": "1234", "jev": "5678"}

    response = logged_in.delete("/api/ai/keys/anthropic")
    assert response.json() == {"anthropic": None, "jev": "5678"}
    assert logged_in.get("/api/config").json() == {"ai": False, "labeling": True}
    assert logged_in.put("/api/ai/keys/openai", json={"key": "x"}).status_code == 422
    assert logged_in.put("/api/ai/keys/jev", json={"key": ""}).status_code == 422


def test_api_keys_in_the_environment_are_ignored(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setenv("JEV_API_KEY", "")
    assert logged_in.get("/api/config").json() == {"ai": False, "labeling": False}
    assert keys.ignored_env() == ["ANTHROPIC_API_KEY"]  # the worker logs a warning


def test_a_key_that_no_longer_decrypts_counts_as_unset(logged_in: TestClient) -> None:
    set_key("anthropic", "sk-ant-test-1234")
    assert logged_in.get("/api/config").json()["ai"] is True

    async def garble() -> None:  # as after SECRET_KEY changed
        async with session.Session() as db:
            await prefs.put(db, "api_key_anthropic", "gAAAAAnot-a-token")
            await db.commit()

    run(garble())
    assert logged_in.get("/api/config").json()["ai"] is False
    assert logged_in.get("/api/ai/keys").json()["anthropic"] is None


def test_labeling_model_choices_follow_the_keys(logged_in: TestClient) -> None:
    def current() -> tuple[str, list[str]]:
        data = logged_in.get("/api/settings").json()
        return data["labeling_model"], data["labeling_models"]

    set_key("jev", "jev-key")
    assert current() == ("jev-latest", ["jev-latest"])  # the only key
    set_key("anthropic", "test-key")
    assert current() == ("claude-haiku-5-5", [*MODELS, "jev-latest"])  # Claude stays the default

    response = logged_in.patch("/api/settings", json={"labeling_model": "jev-latest"})
    assert response.json()["labeling_model"] == "jev-latest"
    assert logged_in.patch("/api/settings", json={"summary_model": "jev-latest"}).status_code == 422

    set_key("jev", None)  # Jev picked, its key removed: back to Claude
    assert current() == ("claude-haiku-5-5", MODELS)
