import json
from typing import Any

from redis.asyncio import Redis

from kanso.core.settings import settings

redis = Redis.from_url(settings.redis_url)

# The API streams these to the browser.
EVENTS_CHANNEL = "kanso:events"
# Wakes the worker's outbox processor.
OUTBOX_CHANNEL = "kanso:outbox"
# Wakes the worker's AI labeling.
AI_CHANNEL = "kanso:ai"
# Asks the worker to sync an account with Gmail now (the message is its id).
SYNC_CHANNEL = "kanso:sync"


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


async def request_sync(account_id: int) -> None:
    await redis.publish(SYNC_CHANNEL, str(account_id))
