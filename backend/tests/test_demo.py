from typing import Any

import pytest
from argon2 import PasswordHasher
from conftest import run
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from kanso import demo_seed
from kanso.core.security import verify_password
from kanso.core.settings import DEMO_PASSWORD, Settings, settings
from kanso.db.models import Account, Label, Thread
from kanso.db.session import Session


@pytest.fixture
def demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)


def test_demo_settings_need_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("PASSWORD_HASH", PasswordHasher().hash("another password"))  # ignored
    demo = Settings(_env_file=None)
    assert demo.demo_mode and len(demo.secret_key) >= 32
    assert verify_password(DEMO_PASSWORD, demo.password_hash)
    # The same on every start: the session signature covers it, so a restart keeps visitors in.
    assert Settings(_env_file=None).password_hash == demo.password_hash
    monkeypatch.delenv("SECRET_KEY")
    monkeypatch.delenv("PASSWORD_HASH")
    assert Settings(_env_file=None).demo_mode
    monkeypatch.setenv("DEMO_MODE", "false")
    with pytest.raises(ValidationError):  # a real instance still needs both
        Settings(_env_file=None)


def test_the_login_page_learns_the_demo_password(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/auth/demo").json() == {"password": None}
    monkeypatch.setattr(settings, "demo_mode", True)
    assert client.get("/api/auth/demo").json() == {"password": DEMO_PASSWORD}  # no session needed


@pytest.mark.usefixtures("demo_mode")
def test_a_demo_connects_no_gmail_and_keeps_its_password(
    logged_in: TestClient, password: str
) -> None:
    assert logged_in.get("/api/accounts/connect", follow_redirects=False).status_code == 403
    change = {"current": password, "new": "a brand new password"}
    assert logged_in.post("/api/auth/password", json=change).status_code == 403


async def _state() -> dict[str, Any]:
    async with Session() as db:
        threads = await db.execute(
            select(Thread.id, Thread.subject, Thread.in_inbox, Thread.in_trash, Thread.starred)
        )
        labels = await db.execute(select(Label.id, Label.name, Label.color))
        return {"threads": sorted(threads.all()), "labels": sorted(labels.all())}


@pytest.mark.usefixtures("db", "demo_mode")
def test_reset_goes_back_to_the_starting_state(logged_in: TestClient) -> None:
    run(demo_seed.reset())
    start = run(_state())
    assert len(start["threads"]) == len(demo_seed.THREADS)

    inbox = [t for t in start["threads"] if t.in_inbox]
    for action, thread in (("done", inbox[0]), ("delete", inbox[1]), ("star", inbox[2])):
        response = logged_in.post(
            "/api/threads/actions", json={"action": action, "thread_ids": [thread.id]}
        )
        assert response.status_code == 200, response.text
    assert logged_in.post("/api/labels", json={"name": "Visitor"}).status_code == 201
    assert run(_state()) != start

    run(demo_seed.reset())
    assert run(_state()) == start  # same threads under the same ids


@pytest.mark.usefixtures("db", "demo_mode")
def test_reset_never_touches_a_real_mailbox() -> None:
    async def connect() -> None:
        async with Session() as db:
            db.add(
                Account(email="me@gmail.com", display_name="me", color="#2F4B6E", refresh_token=b"")
            )
            await db.commit()

    run(connect())
    with pytest.raises(RuntimeError, match="connected Gmail account"):
        run(demo_seed.reset())
    assert run(_state())["threads"] == []
