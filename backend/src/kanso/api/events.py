"""Live updates over Server-Sent Events: everything the worker publishes to Redis (see
kanso.db.redis), e.g. {"type": "threads_changed", "account_id": 1, "thread_ids": [4, 9]}."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from kanso.db import redis as events

router = APIRouter()

# A comment line this often keeps proxies from closing an idle stream.
KEEPALIVE_SECONDS = 20


async def stream() -> AsyncGenerator[str]:
    async with events.redis.pubsub() as pubsub:
        await pubsub.subscribe(events.EVENTS_CHANNEL)
        await pubsub.get_message(timeout=5)  # the subscribe confirmation
        yield "retry: 3000\n\n"  # reconnect quickly after a restart
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS
            )
            if message is None:
                yield ": keepalive\n\n"
            else:
                data = message["data"]
                yield f"data: {data.decode() if isinstance(data, bytes) else data}\n\n"


@router.get("/events")
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
