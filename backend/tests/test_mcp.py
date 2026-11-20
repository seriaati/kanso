import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import test_summaries as mail
from conftest import run
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from kanso import demo_seed
from kanso.api import mcp
from kanso.core.settings import settings
from kanso.db.models import Account
from kanso.db.session import Session
from kanso.sync import store

pytestmark = pytest.mark.usefixtures("db")

WORK = "work@example.com"
CALLBACK = "http://localhost:6274/callback"
MCP = {"Accept": "application/json, text/event-stream"}


def register(api: TestClient) -> str:
    response = api.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


def start(api: TestClient, client_id: str, verifier: str) -> str:
    """The agent opens /authorize; Kanso sends the browser to its consent page."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    response = api.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": "s1",
            "scope": "mail.read",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    consent = urlsplit(response.headers["location"])
    assert (consent.netloc, consent.path) == ("localhost", "/oauth/authorize")  # PUBLIC_URL
    return parse_qs(consent.query)["request"][0]


def exchange(api: TestClient, client_id: str, code: str, verifier: str) -> Any:
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": CALLBACK,
        "code_verifier": verifier,
    }
    return api.post("/token", data=form)


def sign_in(api: TestClient) -> dict[str, Any]:
    client_id = register(api)
    verifier = secrets.token_urlsafe(40)
    request = start(api, client_id, verifier)
    assert api.get(f"/api/oauth/requests/{request}").json() == {
        "client_name": "Claude",
        "redirect_uri": CALLBACK,
    }
    back = api.post(f"/api/oauth/requests/{request}/approve").json()["redirect_uri"]
    query = parse_qs(urlsplit(back).query)
    assert query["state"] == ["s1"]
    tokens = exchange(api, client_id, query["code"][0], verifier)
    assert tokens.status_code == 200, tokens.text
    return tokens.json() | {"client_id": client_id}


def rpc(api: TestClient, token: str, method: str, **params: Any) -> Any:
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
    headers = MCP | {"Authorization": f"Bearer {token}"}
    return api.post("/api/mcp", json=body, headers=headers)


def call(api: TestClient, token: str, name: str, **arguments: Any) -> tuple[Any, bool]:
    response = rpc(api, token, "tools/call", name=name, arguments=arguments)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    [content] = result["content"]
    error = result.get("isError", False)
    return (content["text"] if error else json.loads(content["text"])), error


async def _accounts() -> None:
    async with Session() as db:
        for email in (mail.ME, WORK):
            db.add(Account(email=email, display_name="me", color="#2F4B6E", refresh_token=b"x"))
        await db.flush()
        for account_id in (1, 2):
            labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}]
            labels.append({"id": "Label_1", "name": f"Label {account_id}", "type": "user"})
            await store.save_labels(db, account_id, labels)
        await db.commit()


@pytest.fixture
def mailbox() -> dict[str, int]:
    run(_accounts())
    run(demo_seed.seed())
    raw = mail._raw("Mika <mika@example.com>", "Sign it by Friday?", subject="Lease")
    lease = mail.add("l1", raw, 5, thread="t-lease")
    raw = mail._raw("Boss <boss@example.com>", "The Q3 plan is attached.", subject="Q3 plan")
    plan = run(mail._add(2, "w1", "t-plan", raw, 3, ["INBOX"]))
    return {"lease": lease, "plan": plan}


def test_agents_find_how_to_sign_in(logged_in: TestClient) -> None:
    # The session cookie isn't enough: the 401 points to the OAuth metadata.
    response = logged_in.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    metadata_url = "http://localhost/.well-known/oauth-protected-resource/api/mcp"
    assert f'resource_metadata="{metadata_url}"' in challenge
    resource = logged_in.get(urlsplit(metadata_url).path).json()
    assert resource["resource"] == "http://localhost/api/mcp"
    assert resource["authorization_servers"] == ["http://localhost/"]
    server = logged_in.get("/.well-known/oauth-authorization-server").json()
    assert server["authorization_endpoint"] == "http://localhost/authorize"
    assert server["registration_endpoint"] == "http://localhost/register"
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert logged_in.get("/authorize").status_code == 400  # not the frontend's index.html


def test_agents_can_use_the_backends_own_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the frontend on Cloudflare Pages, which forwards only /api."""
    monkeypatch.setattr(settings, "api_url", "https://kanso-api.example.com")
    assert mcp.url() == "https://kanso-api.example.com/api/mcp"
    with TestClient(Starlette(routes=mcp.routes())) as api:
        server = api.get("/.well-known/oauth-authorization-server").json()
        assert server["issuer"] == "https://kanso-api.example.com/"
        assert server["token_endpoint"] == "https://kanso-api.example.com/token"
        resource = api.get("/.well-known/oauth-protected-resource/api/mcp").json()
        assert resource["resource"] == "https://kanso-api.example.com/api/mcp"
        start(api, register(api), secrets.token_urlsafe(40))  # consent stays on PUBLIC_URL


def test_sign_in_and_read(logged_in: TestClient, mailbox: dict[str, int]) -> None:
    token = sign_in(logged_in)["access_token"]
    hello = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    }
    started = rpc(logged_in, token, "initialize", **hello)
    assert started.status_code == 200, started.text
    assert started.json()["result"]["serverInfo"]["name"] == "kanso"

    listed = rpc(logged_in, token, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in listed] == ["search_threads", "get_thread", "list_labels"]
    assert all(t["annotations"]["readOnlyHint"] for t in listed)
    search = listed[0]["inputSchema"]["properties"]
    assert search["account"]["enum"] == [mail.ME, WORK]  # the demo mailbox isn't offered
    assert "account" not in listed[1]["inputSchema"]["properties"]

    found, error = call(logged_in, token, "search_threads", query="")
    assert not error and [t["subject"] for t in found["threads"]] == ["Lease"]
    found, _ = call(logged_in, token, "search_threads", query="", account=WORK.upper())
    assert [t["subject"] for t in found["threads"]] == ["Q3 plan"]
    labels, _ = call(logged_in, token, "list_labels", account=WORK)
    assert labels == {"labels": [{"name": "Label 2"}]}
    # A thread is read from its own account, without naming it.
    thread, _ = call(logged_in, token, "get_thread", thread_id=mailbox["plan"])
    assert thread["messages"][0]["text"] == "The Q3 plan is attached."
    # Mistakes come back as tool errors the agent can read.
    text, error = call(logged_in, token, "search_threads", query="", account="who@example.com")
    assert error and WORK in text
    assert call(logged_in, token, "get_thread", thread_id=99999) == (
        "There is no thread 99999",
        True,
    )
    assert call(logged_in, token, "search_threads", query="", limit=500)[1] is True
    # A change tool can't be called, even though the assistant has it.
    archive = call(logged_in, token, "archive", thread_ids=[mailbox["lease"]])
    assert archive == ("There is no tool named archive", True)


def test_codes_need_the_verifier_and_work_once(logged_in: TestClient) -> None:
    client_id = register(logged_in)
    verifier = secrets.token_urlsafe(40)
    request = start(logged_in, client_id, verifier)
    back = logged_in.post(f"/api/oauth/requests/{request}/approve").json()["redirect_uri"]
    code = parse_qs(urlsplit(back).query)["code"][0]
    assert exchange(logged_in, client_id, code, "wrong" * 10).status_code == 400
    assert exchange(logged_in, client_id, code, verifier).status_code == 200
    assert exchange(logged_in, client_id, code, verifier).json()["error"] == "invalid_grant"
    # A request is answered once.
    assert logged_in.post(f"/api/oauth/requests/{request}/approve").status_code == 404


def test_deny(logged_in: TestClient) -> None:
    request = start(logged_in, register(logged_in), secrets.token_urlsafe(40))
    back = logged_in.post(f"/api/oauth/requests/{request}/deny").json()["redirect_uri"]
    assert parse_qs(urlsplit(back).query) == {"error": ["access_denied"], "state": ["s1"]}
    assert logged_in.get(f"/api/oauth/requests/{request}").status_code == 404


def test_refresh_rotates_and_logout_everywhere_signs_agents_out(
    logged_in: TestClient, password: str
) -> None:
    first = sign_in(logged_in)
    form = {
        "grant_type": "refresh_token",
        "client_id": first["client_id"],
        "refresh_token": first["refresh_token"],
    }
    second = logged_in.post("/token", data=form)
    assert second.status_code == 200, second.text
    assert logged_in.post("/token", data=form).json()["error"] == "invalid_grant"
    assert rpc(logged_in, first["access_token"], "ping").status_code == 401
    token = second.json()["access_token"]
    assert rpc(logged_in, token, "ping").status_code == 200

    assert logged_in.post("/api/auth/logout-everywhere").status_code == 204
    assert rpc(logged_in, token, "ping").status_code == 401
