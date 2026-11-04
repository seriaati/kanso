import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, TypeIs

import anthropic
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kanso.ai import keys
from kanso.db import prefs
from kanso.db.models import AiUsage

# USD per million tokens: input, output, cache read, cache write (5-minute TTL). Checked 2026-11-02.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}

COUNTERS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def cost(
    model: str, input_: float, output: float, cache_read: float, cache_write: float
) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    tokens = (input_, output, cache_read, cache_write)
    return sum(n * p for n, p in zip(tokens, price, strict=True)) / 1_000_000


async def record(db: AsyncSession, feature: str, model: str, usage: Any) -> None:
    """A response means the key works, so this clears the rejected notice. The caller commits."""
    await prefs.remove(db, prefs.AI_KEY_REJECTED)
    values = {
        "requests": 1,
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_tokens": usage.cache_read_input_tokens or 0,
        "cache_write_tokens": usage.cache_creation_input_tokens or 0,
    }
    stmt = insert(AiUsage).values(
        day=datetime.now(UTC).date(), feature=feature, model=model, **values
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["day", "feature", "model"],
            set_={k: getattr(AiUsage, k) + stmt.excluded[k] for k in values},
        )
    )


async def summary(db: AsyncSession, days: int) -> dict[str, Any]:
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = await db.scalars(select(AiUsage).where(AiUsage.day >= since))
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        entry = totals.setdefault(
            (row.feature, row.model),
            {"feature": row.feature, "model": row.model, **dict.fromkeys(COUNTERS, 0)},
        )
        for key in COUNTERS:
            entry[key] += getattr(row, key)
    features = []
    for entry in totals.values():
        entry["cost_usd"] = cost(
            entry["model"],
            entry["input_tokens"],
            entry["output_tokens"],
            entry["cache_read_tokens"],
            entry["cache_write_tokens"],
        )
        features.append(entry)
    features.sort(key=lambda e: (e["feature"], e["model"]))
    priced = [e["cost_usd"] for e in features if e["cost_usd"] is not None]
    return {
        "since": since,
        "features": features,
        "cost_usd": sum(priced) if priced else 0.0,
        "complete": len(priced) == len(features),  # False: a model without a known price
    }


KEY_REJECTED = "Anthropic API key was rejected; replace it in Settings"


def key_rejected(error: BaseException) -> TypeIs[anthropic.APIStatusError]:
    return isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError)


def _key_id(key: str) -> str:
    """Tells keys apart without storing one."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


async def reject_key(db: AsyncSession, error: anthropic.APIStatusError) -> None:
    """The caller commits."""
    key = _key_id(await keys.get(db, "anthropic"))
    value = {"key": key, "status": error.status_code, "at": datetime.now(UTC).isoformat()}
    await prefs.put(db, prefs.AI_KEY_REJECTED, value)


async def key_was_rejected(db: AsyncSession) -> bool:
    """A new key, a different id, clears it."""
    key = await keys.get(db, "anthropic")
    value = await prefs.get(db, prefs.AI_KEY_REJECTED)
    return bool(key and value and value.get("key") == _key_id(key))
