"""The Android app's latest release, for its update check. kanso asks GitHub instead of the app:
the page's CSP lets it reach only its own server, and GitHub sees the server rather than phones."""

import logging
import time
from typing import Any

import httpx2 as httpx
from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()

REPO = "seriaati/kanso"  # where .github/workflows/android.yml publishes the app
CACHE_SECONDS = 3600  # GitHub allows 60 requests an hour without a token
_cache: dict[str, Any] = {}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def _fetch() -> dict[str, str]:
    async with _client() as http:
        response = await http.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
    response.raise_for_status()
    release = response.json()
    apks = [a["browser_download_url"] for a in release["assets"] if a["name"].endswith(".apk")]
    return {"tag": release["tag_name"], "url": apks[0] if apks else release["html_url"]}


@router.get("/app/release")
async def latest_release() -> dict[str, str]:
    """The tag and the APK's download URL (else the release page)."""
    if time.monotonic() - _cache.get("at", -CACHE_SECONDS) >= CACHE_SECONDS:
        try:
            _cache.update(release=await _fetch(), at=time.monotonic())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            log.info("could not get the app's latest release: %r", error)
            if "release" not in _cache:
                raise HTTPException(502, "Could not reach GitHub") from error
    return _cache["release"]
