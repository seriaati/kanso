import mimetypes

import pytest
from fastapi.testclient import TestClient

from kanso.api import health
from kanso.main import app

client = TestClient(app)


async def ok() -> None:
    pass


async def fail() -> None:
    raise ConnectionError("down")


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "check_db", ok)
    monkeypatch.setattr(health, "check_redis", ok)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"app": "kanso", "db": "ok", "redis": "ok"}


def test_health_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "check_db", ok)
    monkeypatch.setattr(health, "check_redis", fail)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"app": "kanso", "db": "ok", "redis": "error"}


def test_fonts_are_served_as_woff2() -> None:
    # GZipMiddleware skips font/woff2; served as application/octet-stream it would recompress.
    assert mimetypes.guess_type("font.woff2")[0] == "font/woff2"
