"""The AI providers' API keys. They are set in Settings, never in .env, and stored in the
settings table encrypted with SECRET_KEY, like the Gmail refresh tokens."""

import os
from dataclasses import dataclass
from typing import Literal

import httpx2 as httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.core.security import decrypt, encrypt
from kanso.db import prefs

Provider = Literal["anthropic", "jev"]
PROVIDERS: tuple[Provider, ...] = ("anthropic", "jev")
NAMES: dict[Provider, str] = {"anthropic": "Anthropic", "jev": "TypeSafe"}
# A cheap authenticated call per provider, to check a key before it is saved.
CHECK_URLS: dict[Provider, str] = {
    "anthropic": "https://api.anthropic.com/v1/models?limit=1",
    "jev": "https://api.typesafe.ai/v1/models",
}
AI_OFF = "AI is off: add an Anthropic API key in Settings"
LABELING_OFF = "AI labeling is off: add an API key, or turn it on, in Settings"
# Keys once read from the environment, now ignored.
OLD_ENV = ("ANTHROPIC_API_KEY", "JEV_API_KEY")


@dataclass(frozen=True)
class Keys:
    anthropic: str = ""
    jev: str = ""

    @property
    def ai(self) -> bool:
        """Summaries and the assistant: Claude only."""
        return bool(self.anthropic)

    @property
    def labeling(self) -> bool:
        return bool(self.anthropic or self.jev)


def _pref(provider: Provider) -> str:
    return f"api_key_{provider}"


async def get(db: AsyncSession, provider: Provider) -> str:
    """The key, or "" when it isn't set."""
    stored = await prefs.get(db, _pref(provider))
    if not stored:
        return ""
    try:
        return decrypt(str(stored).encode())
    except InvalidToken:  # SECRET_KEY changed since: as good as unset
        return ""


async def load(db: AsyncSession) -> Keys:
    return Keys(anthropic=await get(db, "anthropic"), jev=await get(db, "jev"))


async def put(db: AsyncSession, provider: Provider, key: str) -> None:
    """The caller commits."""
    await prefs.put(db, _pref(provider), encrypt(key).decode())


async def remove(db: AsyncSession, provider: Provider) -> None:
    """The caller commits."""
    await prefs.remove(db, _pref(provider))


def _headers(provider: Provider, key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


async def check(provider: Provider, key: str) -> str | None:
    """None when the provider accepts the key, else why not."""
    name = NAMES[provider]
    try:
        async with _http() as http:
            response = await http.get(CHECK_URLS[provider], headers=_headers(provider, key))
    except httpx.TransportError:
        return f"Couldn’t reach {name} to check the key. Try again in a moment."
    if response.status_code in (401, 403):
        return f"{name} rejected this key."
    if response.status_code >= 400:
        return f"{name} answered {response.status_code} while checking the key. Try again later."
    return None


def ignored_env() -> list[str]:
    return [name for name in OLD_ENV if os.environ.get(name)]
