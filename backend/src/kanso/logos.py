"""Logos for senders without a contact photo, by their domain: the domain's BIMI logo, else its
icon from Google's favicon service. Fetched by the server the first time a row needs one and
stored, so the browser never asks anyone else."""

import asyncio
import ipaddress
import json
import logging
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import httpx2 as httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.db.models import DomainLogo

log = logging.getLogger(__name__)

REFRESH = timedelta(days=30)  # also for domains that had no logo
SIZE = 64
MAX_IMAGE = 256 * 1024
DOH = "https://dns.google/resolve"
FAVICONS = "https://www.google.com/s2/favicons"

# Personal addresses: their domain's logo would stand for the mail provider, not the person.
FREEMAIL = frozenset(
    """
    gmail.com googlemail.com outlook.com hotmail.com hotmail.co.uk live.com msn.com yahoo.com
    yahoo.co.jp yahoo.com.tw ymail.com icloud.com me.com mac.com aol.com proton.me protonmail.com
    pm.me gmx.com gmx.de gmx.net web.de mail.com yandex.com yandex.ru mail.ru qq.com 163.com
    126.com naver.com daum.net hey.com fastmail.com zoho.com tutanota.com tuta.io
    """.split()
)
# Second levels under a country code that are a suffix themselves: bbc.co.uk, not co.uk.
SUFFIX_SECOND_LEVELS = frozenset("co com net org ac gov edu ne or go".split())


def domain_of(email: str) -> str | None:
    """The organization's domain (accounts.google.com -> google.com), None for personal mail."""
    domain = email.rpartition("@")[2].strip().strip(".").lower()
    labels = domain.split(".")
    if len(labels) < 2 or not all(labels):
        return None
    country_suffix = len(labels[-1]) == 2 and labels[-2] in SUFFIX_SECOND_LEVELS
    org = ".".join(labels[-3:] if country_suffix and len(labels) > 2 else labels[-2:])
    return None if org in FREEMAIL or domain in FREEMAIL else org


async def _public(host: str) -> bool:
    """A BIMI record names any URL it likes, which could point at the server's own network."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


async def _get(http: httpx.AsyncClient, url: str, limit: int) -> tuple[httpx.Response, bytes]:
    """Public https hosts only, also after redirects; at most `limit` bytes."""
    for _ in range(5):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443):
            raise ValueError(f"not a public https URL: {url}")
        if not await _public(parts.hostname):
            raise ValueError(f"not a public host: {parts.hostname}")
        async with http.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise ValueError(f"larger than {limit} bytes: {url}")
            return response, body
    raise ValueError(f"too many redirects: {url}")


def _image(response: httpx.Response, body: bytes) -> str | None:
    """The image's type, None if it isn't one."""
    if response.status_code != 200 or not body:
        return None
    if b"<svg" in body[:2048]:  # BIMI hosts don't always label their SVG
        return "image/svg+xml"
    mime_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    return mime_type if mime_type.startswith("image/") else None


async def _bimi(http: httpx.AsyncClient, domain: str) -> tuple[bytes, str] | None:
    response, body = await _get(
        http, f"{DOH}?{httpx.QueryParams(name=f'default._bimi.{domain}', type='TXT')}", MAX_IMAGE
    )
    for answer in json.loads(body).get("Answer", []):
        record = answer.get("data", "").replace('" "', "").strip('"')
        tags = dict(tag.strip().partition("=")[::2] for tag in record.split(";"))
        if tags.get("v") == "BIMI1" and tags.get("l", "").startswith("https://"):
            response, body = await _get(http, tags["l"], MAX_IMAGE)
            if _image(response, body) == "image/svg+xml":
                return body, "image/svg+xml"
    return None


async def _google(http: httpx.AsyncClient, domain: str) -> tuple[bytes, str] | None:
    """404 (with a generic globe) when Google knows no icon for the domain."""
    url = f"{FAVICONS}?{httpx.QueryParams(domain=domain, sz=SIZE)}"
    response, body = await _get(http, url, MAX_IMAGE)
    mime_type = _image(response, body)
    return (body, mime_type) if mime_type else None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, headers={"User-Agent": "Kanso (self-hosted mail client)"})


_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def lock(domain: str) -> asyncio.Lock:
    """One fetch per domain, though a page of rows asks for it at once."""
    return _locks[domain]


async def fetch(domain: str) -> tuple[bytes, str, str] | None:
    """(content, mime type, source), None when no source has a logo."""
    steps = [
        ("bimi", _bimi, domain),
        ("google", _google, domain),
        # Some sites live only at www (waseda.jp doesn't resolve), and Google knows them only there.
        ("google", _google, f"www.{domain}"),
    ]
    async with _client() as http:
        for source, find, host in steps:
            try:
                if found := await find(http, host):
                    return (*found, source)
            except (httpx.HTTPError, ValueError) as error:
                log.info("logo for %s: %s failed (%s)", host, source, error)
    return None


async def load(db: AsyncSession, domain: str) -> DomainLogo | None:
    """The stored logo, fetched first when it's new or older than REFRESH."""
    async with lock(domain):
        logo = await db.get(DomainLogo, domain)
        now = await db.scalar(func.now())
        if logo is None or logo.fetched_at < now - REFRESH:
            found = await fetch(domain)
            values = {
                "content": found[0] if found else None,
                "mime_type": found[1] if found else None,
                "source": found[2] if found else None,
                "fetched_at": now,
            }
            await db.execute(
                insert(DomainLogo)
                .values(domain=domain, **values)
                .on_conflict_do_update(index_elements=[DomainLogo.domain], set_=values)
            )
            await db.commit()
            logo = await db.get(DomainLogo, domain, populate_existing=True)
    return logo
