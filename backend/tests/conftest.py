import asyncio
import os
import tempfile
from collections.abc import Iterator

import fakeredis
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

# Settings are read at import time, so the test environment goes in before Kanso is imported.
PASSWORD = "correct horse battery staple"
os.environ |= {
    "SECRET_KEY": "test-secret-key-0123456789abcdef0123456789",
    "PASSWORD_HASH": PasswordHasher(time_cost=1, memory_cost=1024).hash(PASSWORD),
    "PUBLIC_URL": "http://testserver",
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    # Env vars win over the repo's .env, so a real Pub/Sub config there can't leak in.
    "GOOGLE_PUBSUB_TOPIC": "",
    "GOOGLE_PUBSUB_SUBSCRIPTION": "",
    "GOOGLE_PUBSUB_CREDENTIALS": "",
}
# A stand-in for the built frontend, which CI's backend job doesn't build.
_dist = tempfile.mkdtemp(prefix="kanso-dist-")
with open(os.path.join(_dist, "index.html"), "w") as index:
    index.write("<!doctype html><title>Kanso</title>")
os.environ["FRONTEND_DIST"] = _dist
# Database tests drop and recreate the schema, so use a disposable Postgres, e.g.
# TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/postgres
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from collections.abc import Coroutine  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from kanso.ai import keys  # noqa: E402
from kanso.api import accounts, auth  # noqa: E402
from kanso.core import security  # noqa: E402
from kanso.core.hardening import CSRF_HEADER, CSRF_VALUE  # noqa: E402
from kanso.core.settings import settings  # noqa: E402
from kanso.db import redis as redis_module  # noqa: E402
from kanso.db import session  # noqa: E402
from kanso.db.models import Base  # noqa: E402
from kanso.gmail import client as gmail_client  # noqa: E402
from kanso.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeAsyncRedis:
    fake = fakeredis.FakeAsyncRedis()
    monkeypatch.setattr(auth, "redis", fake)
    monkeypatch.setattr(accounts, "redis", fake)
    monkeypatch.setattr(redis_module, "redis", fake)
    return fake


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_seconds: float) -> None:
        pass

    monkeypatch.setattr(gmail_client, "pace_sleep", no_wait)


@pytest.fixture(autouse=True)
def env_password() -> None:
    """So tests without a database don't look up the password hash or session epoch."""
    security.remember_password_hash(settings.password_hash)
    security.remember_session_epoch(0)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    async def wrapper() -> T:
        # Connections a TestClient left in the pool belong to its loop: forget, don't close them.
        await session.engine.dispose(close=False)
        try:
            return await coro
        finally:
            await session.engine.dispose()

    return asyncio.run(wrapper())


@pytest.fixture
def password() -> str:
    return PASSWORD


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, headers={CSRF_HEADER: CSRF_VALUE}) as c:
        yield c


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 204
    return client


def set_key(provider: keys.Provider, key: str | None) -> None:
    """Saves an AI API key as Settings does, without the provider's check; None removes it."""

    async def go() -> None:
        async with session.Session() as db:
            if key is None:
                await keys.remove(db, provider)
            else:
                await keys.put(db, provider, key)
            await db.commit()

    run(go())


async def _execute(*statements: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL or "", poolclass=NullPool)
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    await engine.dispose()


@pytest.fixture(scope="session")
def migrated() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run database tests")
    asyncio.run(_execute("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"))
    command.upgrade(Config(toml_file=Path(__file__).parent.parent / "pyproject.toml"), "head")


@pytest.fixture
def db(migrated: None) -> None:
    tables = ", ".join(Base.metadata.tables)
    asyncio.run(_execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
