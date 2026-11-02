import json
from typing import Any

from redis.asyncio import Redis

from kanso.core.settings import settings

redis = Redis.from_url(settings.redis_url)

# Live updates: the worker publishes here and the API streams them to the browser (api/events.py).
EVENTS_CHANNEL = "kanso:events"
# The API publishes here after queueing a write-back, to wake the worker's outbox processor.
OUTBOX_CHANNEL = "kanso:outbox"
# The sync and the API publish here after queueing threads for AI labeling, to wake the worker.
AI_CHANNEL = "kanso:ai"


async def check_redis() -> None:
    await redis.ping()


async def publish(event: dict[str, Any]) -> None:
    await redis.publish(EVENTS_CHANNEL, json.dumps(event))


async def threads_changed(account_id: int, thread_ids: set[int] | list[int]) -> None:
    if thread_ids:
        await publish(
            {"type": "threads_changed", "account_id": account_id, "thread_ids": sorted(thread_ids)}
        )


async def sync_changed(account_id: int) -> None:
    await publish({"type": "sync", "account_id": account_id})


async def wake_outbox() -> None:
    await redis.publish(OUTBOX_CHANNEL, "1")
