from typing import Any

import httpx2 as httpx
import pytest
from conftest import run
from fake_gmail import FakeGmail, make_raw
from fastapi.testclient import TestClient
from test_photos import FakePeople, sync_photos
from test_sync import _create_account, sync

from kanso import logos

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
PNG = b"\x89PNG fake"


class FakeWeb:
    """DNS over HTTPS, Google's favicon service and the hosts BIMI records point at."""

    def __init__(self) -> None:
        self.bimi: dict[str, str] = {}  # domain -> logo URL
        self.files: dict[str, tuple[str, bytes]] = {}  # URL -> (content type, body)
        self.google: set[str] = set()  # domains Google has an icon for
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        params = request.url.params
        if request.url.host == "dns.google":
            domain = params["name"].removeprefix("default._bimi.")
            answers = [
                {"data": f'"v=BIMI1; l={self.bimi[domain]}; a=https://x/cert.pem"'}
                for _ in [1]
                if domain in self.bimi
            ]
            return httpx.Response(200, json={"Status": 0, "Answer": answers} if answers else {})
        if request.url.host == "www.google.com":
            found = params["domain"] in self.google
            return httpx.Response(
                200 if found else 404,
                content=PNG if found else b"globe",
                headers={"content-type": "image/png"},
            )
        content_type, body = self.files[str(request.url)]
        return httpx.Response(200, content=body, headers={"content-type": content_type})


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(logos, "_client", lambda: httpx.AsyncClient(transport=transport))

    async def public(host: str) -> bool:
        return not host.startswith("internal.")

    monkeypatch.setattr(logos, "_public", public)
    return fake


@pytest.mark.parametrize(
    ("email", "domain"),
    [
        ("no-reply@accounts.google.com", "google.com"),
        ("service@intl.paypal.com", "paypal.com"),
        ("mail@kuronekoyamato.co.jp", "kuronekoyamato.co.jp"),
        ("system@lms.waseda.jp", "waseda.jp"),
        ("news@bbc.co.uk", "bbc.co.uk"),
        ("Friend@Gmail.com", None),  # a personal address: no logo
        ("someone@yahoo.co.jp", None),
        ("broken@", None),
    ],
)
def test_domain_of(email: str, domain: str | None) -> None:
    assert logos.domain_of(email) == domain


def test_bimi_comes_first(web: FakeWeb) -> None:
    web.bimi["paypal.com"] = "https://logos.example/paypal.svg"
    web.files["https://logos.example/paypal.svg"] = ("text/plain", SVG)  # mislabeled, still SVG
    web.google.add("paypal.com")
    assert run(logos.fetch("paypal.com")) == (SVG, "image/svg+xml", "bimi")


def test_google_then_www(web: FakeWeb) -> None:
    web.google.add("www.waseda.jp")
    assert run(logos.fetch("waseda.jp")) == (PNG, "image/png", "google")
    assert run(logos.fetch("nothing.example")) is None


def test_bimi_on_the_servers_own_network_is_skipped(web: FakeWeb) -> None:
    web.bimi["evil.example"] = "https://internal.example/admin"
    assert run(logos.fetch("evil.example")) is None
    assert "https://internal.example/admin" not in web.requests


def threads(client: TestClient) -> dict[str, Any]:
    body = client.get("/api/threads", params={"account_id": 1, "mailbox": "inbox"}).json()
    return {t["subject"]: (t["sender_name"], t["sender_photo"]) for t in body["threads"]}


@pytest.mark.usefixtures("db")
def test_rows_link_photos_then_logos(logged_in: TestClient, web: FakeWeb) -> None:
    gmail = FakeGmail()
    gmail.add("m1", "t1", ["INBOX"], make_raw("Lease", "Hi"))  # daniel@example.com
    gmail.add("m2", "t2", ["INBOX"], make_raw("Sale", "Hi", "Shop <news@mail.shop.example>"))
    gmail.add("m3", "t3", ["INBOX"], make_raw("Hey", "Hi", "Bo <bo@gmail.com>"))
    gmail.add("m4", "t4", ["INBOX"], make_raw("Ad", "Hi", "Ads <ads@nologo.example>"))
    run(_create_account())
    sync(gmail)
    sync_photos(FakePeople())  # has a photo for daniel@example.com
    rows = threads(logged_in)
    assert rows["Lease"][1].startswith("/api/photos/")  # the contact photo wins
    assert rows["Sale"] == ("Shop", "/api/logos/shop.example")
    assert rows["Hey"] == ("Bo", None)  # personal mail: the initial
    assert rows["Ad"] == ("Ads", "/api/logos/nologo.example")

    web.google.add("shop.example")
    response = logged_in.get("/api/logos/shop.example")
    assert response.content == PNG
    assert "sandbox" in response.headers["content-security-policy"]
    fetched = len(web.requests)
    assert logged_in.get("/api/logos/shop.example").content == PNG
    assert len(web.requests) == fetched  # stored: not fetched again

    assert logged_in.get("/api/logos/nologo.example").status_code == 404
    assert threads(logged_in)["Ad"] == ("Ads", None)  # known to have none: no more requests


@pytest.mark.usefixtures("db")
def test_reader_shows_each_senders_image(logged_in: TestClient, web: FakeWeb) -> None:
    gmail = FakeGmail()
    gmail.add("m1", "t1", ["INBOX"], make_raw("Lease", "Hi"))  # daniel@example.com
    gmail.add("m2", "t1", ["INBOX"], make_raw("Re: Lease", "Hi", "Shop <news@mail.shop.example>"))
    gmail.add("m3", "t1", ["INBOX"], make_raw("Re: Lease", "Hi", "Bo <bo@gmail.com>"))
    run(_create_account())
    sync(gmail)
    sync_photos(FakePeople())
    thread_id = logged_in.get("/api/threads", params={"account_id": 1}).json()["threads"][0]["id"]
    messages = logged_in.get(f"/api/threads/{thread_id}").json()["messages"]
    photos = [m["sender_photo"] for m in messages]
    assert photos[0].startswith("/api/photos/")  # the same images as the rows
    assert photos[1:] == ["/api/logos/shop.example", None]


@pytest.mark.usefixtures("db")
def test_logo_endpoint_only_serves_sender_domains(logged_in: TestClient, web: FakeWeb) -> None:
    for domain in ("gmail.com", "mail.shop.example", "localhost"):
        assert logged_in.get(f"/api/logos/{domain}").status_code == 404
    assert web.requests == []
