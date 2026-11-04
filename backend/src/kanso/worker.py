import asyncio
import logging
import signal
from pathlib import Path

from kanso import demo_seed
from kanso.ai import keys, labeling
from kanso.core.settings import settings
from kanso.db.session import engine
from kanso.gmail.client import http_client
from kanso.sync import drafts, outbox, photos, push
from kanso.sync.scheduler import Scheduler

log = logging.getLogger("kanso.worker")

# The compose healthcheck reads its age.
HEARTBEAT = Path("/tmp/kanso-worker-heartbeat")
ACCOUNTS_REFRESH_SECONDS = 15
# The safety-net poll behind push; without Pub/Sub, polling is all there is.
POLL_WITH_PUSH_SECONDS = 15 * 60
POLL_WITHOUT_PUSH_SECONDS = 2 * 60


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    http = http_client()
    if settings.pubsub_configured:
        scheduler = Scheduler(http, POLL_WITH_PUSH_SECONDS)
        pull = asyncio.create_task(push.run(http, scheduler))
        log.info("worker started: Gmail push via Pub/Sub, safety-net poll every 15 min")
    else:
        scheduler = Scheduler(http, POLL_WITHOUT_PUSH_SECONDS)
        pull = None
        log.warning(
            "worker started: Pub/Sub not configured, polling Gmail every 2 min only "
            "(set GOOGLE_PUBSUB_* in .env for instant updates, see docs/SETUP.md)"
        )
    writes = asyncio.create_task(outbox.run(http))
    housekeeping = asyncio.create_task(drafts.run(http))
    contact_photos = asyncio.create_task(photos.run(http))
    classify = asyncio.create_task(labeling.run())  # idle until an API key is set in Settings
    if ignored := keys.ignored_env():
        log.warning("ignoring %s: AI API keys are set in Settings now", ", ".join(ignored))
    if settings.demo_mode:
        demo_reset = asyncio.create_task(demo_seed.reset_daily())
        log.info("demo instance: the mailbox resets every day at midnight UTC")
    else:
        demo_reset = None
    while not stop.is_set():
        await asyncio.to_thread(HEARTBEAT.touch)
        try:
            await scheduler.refresh()
        except Exception:
            log.exception("could not load accounts")
        try:
            await asyncio.wait_for(stop.wait(), timeout=ACCOUNTS_REFRESH_SECONDS)
        except TimeoutError:
            pass
    scheduler.stop()
    writes.cancel()
    housekeeping.cancel()
    contact_photos.cancel()
    classify.cancel()
    if demo_reset:
        demo_reset.cancel()
    if pull:
        pull.cancel()
    await http.aclose()
    await engine.dispose()
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx2 logs every request at INFO; that is a lot during a backfill.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
