import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from kanso.api import releases
from kanso.main import app

client = TestClient(app)


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Response]:
    """The answers GitHub gives, in order; the cache starts empty."""
    answers: list[httpx.Response] = []
    calls = iter(answers)
    transport = httpx.MockTransport(lambda request: next(calls))
    monkeypatch.setattr(releases, "_client", lambda: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(releases, "_cache", {})
    return answers


def release(*assets: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/seriaati/kanso/releases/tag/v0.2.0",
            "assets": [
                {"name": name, "browser_download_url": f"https://github.com/dl/{name}"}
                for name in assets
            ],
        },
    )


def test_latest_release_points_at_the_apk(github: list[httpx.Response]) -> None:
    github.append(release("notes.txt", "kanso.apk"))
    response = client.get("/api/app/release")
    assert response.status_code == 200
    assert response.json() == {"tag": "v0.2.0", "url": "https://github.com/dl/kanso.apk"}


def test_without_an_apk_it_points_at_the_release_page(github: list[httpx.Response]) -> None:
    github.append(release())
    assert client.get("/api/app/release").json()["url"].endswith("/releases/tag/v0.2.0")


def test_cached_and_kept_when_github_fails(
    github: list[httpx.Response], monkeypatch: pytest.MonkeyPatch
) -> None:
    github.extend([release("kanso.apk"), httpx.Response(403)])
    assert client.get("/api/app/release").status_code == 200
    assert client.get("/api/app/release").status_code == 200  # cached: GitHub not asked
    monkeypatch.setattr(releases, "CACHE_SECONDS", 0)
    response = client.get("/api/app/release")  # asked again; it fails, the last answer stays
    assert response.json()["url"] == "https://github.com/dl/kanso.apk"


def test_github_unreachable_without_a_cached_answer(github: list[httpx.Response]) -> None:
    github.append(httpx.Response(500))
    assert client.get("/api/app/release").status_code == 502
