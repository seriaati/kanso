import asyncio
import base64
import json
import logging
import random
import secrets
from typing import Any

import httpx2 as httpx

from kanso.db.models import Account
from kanso.gmail import oauth

log = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Drafts with attachments, up to 35 MB.
UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
RETRY_STATUSES = {429, 500, 502, 503, 504}
# 403s that mean "slow down" rather than "not allowed".
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
MAX_ATTEMPTS = 7  # backoff 1+2+4+8+16+32 s ≈ a minute before giving up
MAX_BACKOFF = 32.0
# Gmail's quota is 6,000 units per user per minute (100 a second); 90 leaves headroom.
UNITS_PER_SECOND = 90
# From Gmail's "Usage limits" page. "*" stands for an id.
COSTS = {
    ("GET", "/profile"): 1,
    ("GET", "/labels"): 1,
    ("GET", "/settings/sendAs"): 1,
    ("GET", "/history"): 2,
    ("GET", "/messages"): 5,
    ("GET", "/drafts"): 5,
    ("POST", "/labels"): 5,
    ("PATCH", "/labels/*"): 5,
    ("DELETE", "/labels/*"): 5,
    ("POST", "/messages/batchModify"): 50,
    ("POST", "/messages/*/trash"): 20,
    ("POST", "/messages/*/untrash"): 5,
    ("POST", "/drafts"): 10,
    ("PUT", "/drafts/*"): 15,
    ("DELETE", "/drafts/*"): 10,
    ("POST", "/drafts/send"): 100,
    ("POST", "/watch"): 100,
}
DEFAULT_COST = 20

# Replaced in tests so retries and pacing don't wait.
sleep = asyncio.sleep
pace_sleep = asyncio.sleep


def cost(method: str, path: str, params: dict[str, Any] | None = None) -> int:
    if method == "GET" and path.startswith("/messages/"):
        # 60 for format full (the default), raw or html; 20 for minimal or metadata.
        return 20 if (params or {}).get("format") in ("minimal", "metadata") else 60
    parts = path.split("/")
    wildcard = "/".join([*parts[:2], "*", *parts[3:]]) if len(parts) > 2 else path
    return COSTS.get((method, path), COSTS.get((method, wildcard), DEFAULT_COST))


class Pacer:
    def __init__(self) -> None:
        self.next = 0.0

    async def wait(self, units: int) -> None:
        now = asyncio.get_running_loop().time()
        start = max(now, self.next)
        self.next = start + units / UNITS_PER_SECOND
        if start > now:
            await pace_sleep(start - now)

    def pause(self, seconds: float) -> None:
        self.next = max(self.next, asyncio.get_running_loop().time() + seconds)


# Per account id: Gmail's quota is per user, shared by every Gmail client for that account.
_pacers: dict[int, Pacer] = {}


class GmailError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Gmail API returned {status}: {message}")
        self.status = status


class NotFound(GmailError):
    pass


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=20))


def _reason(response: httpx.Response) -> str:
    try:
        errors = response.json()["error"].get("errors") or [{}]
        return errors[0].get("reason", "")
    except (ValueError, KeyError, AttributeError, TypeError):
        return ""


def backoff(attempt: int, retry_after: str | None = None) -> float:
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return min(MAX_BACKOFF, 2.0**attempt) + random.uniform(0, 1)


class Gmail:
    def __init__(self, account: Account, http: httpx.AsyncClient) -> None:
        self.account = account
        self.http = http
        self.pacer = _pacers.setdefault(account.id, Pacer())

    async def request(
        self,
        method: str,
        path: str,
        base: str = API,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            token = await oauth.access_token(self.account)
            all_headers = {**(headers or {}), "Authorization": f"Bearer {token}"}
            await self.pacer.wait(cost(method, path, kwargs.get("params")))
            try:
                response = await self.http.request(
                    method, base + path, headers=all_headers, **kwargs
                )
            except httpx.TransportError as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                log.info("Gmail %s %s failed (%s), retrying", method, path, error)
                await sleep(backoff(attempt))
                continue
            status = response.status_code
            if status == 401 and not refreshed:  # token revoked or expired early
                oauth.forget_access_token(self.account)
                refreshed = True
                continue
            rate_limited = status == 403 and _reason(response) in RATE_LIMIT_REASONS
            if status in RETRY_STATUSES or rate_limited:
                if attempt < MAX_ATTEMPTS - 1:
                    delay = backoff(attempt, response.headers.get("Retry-After"))
                    log.info("Gmail %s %s: %s, retrying in %.1fs", method, path, status, delay)
                    if rate_limited or status == 429:
                        # The quota is per user: every request for this account waits it out.
                        self.pacer.pause(delay)
                    await sleep(delay)
                    continue
            if status == 404:
                raise NotFound(status, response.text[:200])
            if response.is_error:
                raise GmailError(status, response.text[:500])
            return response.json() if response.content else {}
        raise GmailError(0, f"{method} {path} kept failing")

    async def profile(self) -> dict[str, Any]:
        return await self.request("GET", "/profile")

    async def labels(self) -> list[dict[str, Any]]:
        return (await self.request("GET", "/labels")).get("labels", [])

    async def list_messages(self, page_token: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": 500, "includeSpamTrash": "true"}
        if page_token:
            params["pageToken"] = page_token
        return await self.request("GET", "/messages", params=params)

    async def message_raw(self, gmail_id: str) -> tuple[dict[str, Any], bytes]:
        body = await self.request("GET", f"/messages/{gmail_id}", params={"format": "raw"})
        raw = body.pop("raw", "")
        return body, base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    async def message_headers(self, gmail_id: str, names: list[str]) -> dict[str, str]:
        """By lowercase name; a repeated header keeps its first value."""
        params = {"format": "metadata", "metadataHeaders": names}
        body = await self.request("GET", f"/messages/{gmail_id}", params=params)
        headers: dict[str, str] = {}
        for header in body.get("payload", {}).get("headers", []):
            headers.setdefault(header["name"].lower(), header["value"])
        return headers

    async def send_message(self, raw: bytes) -> dict[str, Any]:
        body = {"raw": base64.urlsafe_b64encode(raw).decode()}
        return await self.request("POST", "/messages/send", json=body)

    async def message_labels(self, gmail_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/messages/{gmail_id}", params={"format": "minimal"})

    async def history(self, start_history_id: int, page_token: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"startHistoryId": start_history_id, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        return await self.request("GET", "/history", params=params)

    async def watch(self, topic: str) -> dict[str, Any]:
        return await self.request("POST", "/watch", json={"topicName": topic})

    # Write-backs are idempotent: repeating one changes nothing.

    async def batch_modify(self, ids: list[str], add: list[str], remove: list[str]) -> None:
        """Up to 1000 messages."""
        body = {"ids": ids, "addLabelIds": add, "removeLabelIds": remove}
        await self.request("POST", "/messages/batchModify", json=body)

    async def trash(self, gmail_id: str) -> None:
        await self.request("POST", f"/messages/{gmail_id}/trash")

    async def untrash(self, gmail_id: str) -> None:
        await self.request("POST", f"/messages/{gmail_id}/untrash")

    async def create_label(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/labels", json=body)

    async def update_label(self, gmail_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PATCH", f"/labels/{gmail_id}", json=body)

    async def delete_label(self, gmail_id: str) -> None:
        await self.request("DELETE", f"/labels/{gmail_id}")

    async def _upload_draft(
        self, method: str, path: str, raw: bytes, thread_id: str | None
    ) -> dict[str, Any]:
        """Multipart, so a draft with attachments isn't limited by the JSON request size."""
        boundary = f"kanso-{secrets.token_hex(12)}"
        metadata: dict[str, Any] = {"message": {"threadId": thread_id} if thread_id else {}}
        body = (
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n"
            ).encode()
            + raw
            + f"\r\n--{boundary}--".encode()
        )
        return await self.request(
            method,
            path,
            base=UPLOAD,
            params={"uploadType": "multipart"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )

    async def create_draft(self, raw: bytes, thread_id: str | None) -> dict[str, Any]:
        return await self._upload_draft("POST", "/drafts", raw, thread_id)

    async def update_draft(
        self, draft_id: str, raw: bytes, thread_id: str | None
    ) -> dict[str, Any]:
        """The draft's message gets a new id."""
        return await self._upload_draft("PUT", f"/drafts/{draft_id}", raw, thread_id)

    async def delete_draft(self, draft_id: str) -> None:
        await self.request("DELETE", f"/drafts/{draft_id}")

    async def send_draft(self, draft_id: str) -> dict[str, Any]:
        """Returns the sent message (a new id). A draft sent already raises NotFound."""
        return await self.request("POST", "/drafts/send", json={"id": draft_id})

    async def list_drafts(self, page_token: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        return await self.request("GET", "/drafts", params=params)

    async def send_as(self) -> list[dict[str, Any]]:
        return (await self.request("GET", "/settings/sendAs")).get("sendAs", [])
