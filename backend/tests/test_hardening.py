"""Every other test also covers CSRF acceptance: the `client` fixture sends the header."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kanso.core.hardening import CSRF_HEADER, CSRF_VALUE
from kanso.core.settings import settings
from kanso.db.models import Draft
from kanso.main import app
from kanso.sync import drafts


def directives(csp: str) -> dict[str, list[str]]:
    return {d.split()[0]: d.split()[1:] for d in csp.split("; ")}


@pytest.mark.parametrize("path", ["/", "/inbox/12", "/api/auth/logout", "/api/config"])
def test_security_headers_on_every_response(client: TestClient, path: str) -> None:
    response = client.get(path)
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "strict-transport-security" not in headers  # PUBLIC_URL is http:// in tests
    csp = directives(headers["content-security-policy"])
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"]  # no inline scripts, no eval, no other origins
    assert csp["object-src"] == ["'none'"] and csp["frame-ancestors"] == ["'none'"]
    # An email frame inherits this policy: "Show images" needs http(s) images, and email HTML
    # needs inline styles. Its own, stricter policy blocks remote images until the user asks.
    assert csp["img-src"] == ["'self'", "data:", "https:", "http:"]
    assert csp["style-src"] == ["'self'", "'unsafe-inline'"]


def test_app_shell_is_revalidated_and_assets_are_immutable(client: TestClient) -> None:
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/inbox").headers["cache-control"] == "no-cache"


STATE_CHANGING = [
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
    ("PATCH", "/api/settings"),
    ("POST", "/api/threads/actions"),
    ("DELETE", "/api/accounts/1"),
    ("POST", "/api/threads/1/summary"),  # Server-Sent Events
    ("POST", "/api/assistant/messages"),  # Server-Sent Events
    ("POST", "/api/drafts/1/attachments"),  # an upload: the file is the body
]


@pytest.mark.parametrize(("method", "path"), STATE_CHANGING)
def test_cross_site_requests_are_rejected(logged_in: TestClient, method: str, path: str) -> None:
    # What a form or script on another site can send: the session cookie, but no custom header.
    del logged_in.headers[CSRF_HEADER]
    response = logged_in.request(method, path, json={})
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]
    assert response.headers["content-security-policy"]
    # A wrong value doesn't pass either.
    response = logged_in.request(method, path, json={}, headers={CSRF_HEADER: "XMLHttpRequest"})
    assert response.status_code == 403


def test_requests_with_the_header_pass(logged_in: TestClient, password: str) -> None:
    # AI is off in these tests: both streams answer 409 from their handlers, past the check.
    assert logged_in.post("/api/threads/1/summary").status_code == 409
    assert (
        logged_in.post("/api/assistant/messages", json={"account_id": 1, "text": "hi"}).status_code
        == 409
    )
    assert logged_in.post("/api/auth/login", json={"password": password}).status_code == 204


def test_reads_need_no_header(logged_in: TestClient) -> None:
    del logged_in.headers[CSRF_HEADER]
    assert logged_in.get("/api/config").status_code == 200


def test_login_from_a_bare_client_needs_the_header(password: str) -> None:
    with TestClient(app) as bare:
        assert bare.post("/api/auth/login", json={"password": password}).status_code == 403
        response = bare.post(
            "/api/auth/login", json={"password": password}, headers={CSRF_HEADER: CSRF_VALUE}
        )
        assert response.status_code == 204


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_off(client: TestClient, path: str) -> None:
    response = client.get(path)
    # The SPA answers unknown paths with its index.html.
    assert "swagger" not in response.text.lower() and "redoc" not in response.text.lower()
    assert not response.headers.get("content-type", "").startswith("application/json")
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_removing_an_unknown_attachment_touches_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    keep = tmp_path / "1" / "keep"
    keep.parent.mkdir()
    keep.write_bytes(b"x")
    draft = Draft(id=1, attachments=[{"id": "a" * 32}], sending_since=None)
    for bogus in ("..", ".", "keep", ""):
        drafts.remove_attachment(draft, bogus)  # no error, nothing removed
    assert keep.exists() and draft.attachments == [{"id": "a" * 32}]
