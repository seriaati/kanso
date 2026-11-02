import io
import os
import time

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from kanso import hash_password
from kanso.core import security
from kanso.core.security import SESSION_COOKIE, decrypt, encrypt
from kanso.main import app

PASSWORD_HASH = os.environ["PASSWORD_HASH"]
EXPIRED = int(time.time()) - 60
# /api/auth/demo: a demo instance shows its password on the login page (null elsewhere).
PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/demo"}


def test_login_sets_session_cookie(client: TestClient, password: str) -> None:
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie  # PUBLIC_URL is http:// in tests
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/logout").status_code == 401  # cookie cleared


def test_wrong_password(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_login_is_rate_limited(client: TestClient, password: str) -> None:
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 429  # even the right password, until the window passes
    assert 0 < int(response.headers["retry-after"]) <= 15 * 60


def test_successful_login_resets_failures(client: TestClient, password: str) -> None:
    for _ in range(4):
        client.post("/api/auth/login", json={"password": "nope"})
    assert client.post("/api/auth/login", json={"password": password}).status_code == 204
    for _ in range(4):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_every_api_route_requires_a_session(client: TestClient) -> None:
    paths = app.openapi()["paths"]
    assert set(paths) >= PUBLIC | {"/api/accounts"}
    for path, operations in paths.items():
        if path in PUBLIC:
            continue
        for method in operations:
            response = client.request(method, path.replace("{account_id}", "1"))
            assert response.status_code == 401, f"{method} {path} is not protected"


@pytest.mark.parametrize(
    "cookie",
    [
        "garbage",
        f"{int(time.time()) + 60}.{'0' * 64}",  # forged signature
        f"{EXPIRED}.{security._sign(EXPIRED, PASSWORD_HASH, 0)}",  # expired
    ],
)
def test_invalid_session_cookie(client: TestClient, cookie: str) -> None:
    client.cookies.set(SESSION_COOKIE, cookie)
    assert client.post("/api/auth/logout").status_code == 401


def test_non_ascii_session_cookie_is_invalid_not_an_error() -> None:
    later = int(time.time()) + 60
    assert not security.valid_session(f"{later}.é{'0' * 63}", PASSWORD_HASH, 0)
    assert not security.valid_session(f"{'١' * 10}.{'0' * 64}", PASSWORD_HASH, 0)  # Arabic digits
    signed = f"{later}.{security._sign(later, PASSWORD_HASH, 0)}"
    assert security.valid_session(signed, PASSWORD_HASH, 0)
    assert not security.valid_session(signed, PASSWORD_HASH, 1)  # after "Log out everywhere"


def test_token_encryption_round_trip() -> None:
    ciphertext = encrypt("1//refresh-token")
    assert b"refresh-token" not in ciphertext
    assert encrypt("1//refresh-token") != ciphertext  # random IV
    assert decrypt(ciphertext) == "1//refresh-token"


def test_hash_password_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a long password\n"))
    hash_password.main()
    line = capsys.readouterr().out.strip()
    assert line.startswith("PASSWORD_HASH='$argon2id$") and line.endswith("'")
    assert PasswordHasher().verify(line.split("'")[1], "a long password")


def test_hash_password_cli_prompts_at_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["a long password", "a different one"])
    monkeypatch.setattr(hash_password, "getpass", lambda _: next(answers))
    with pytest.raises(SystemExit, match="don't match"):
        hash_password.main()
    assert capsys.readouterr().out == ""
