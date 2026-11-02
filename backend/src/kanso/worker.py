import asyncio
import logging
import signal
from pathlib import Path

from kanso.ai import labeling
from kanso.core.settings import settings
from kanso.db.session import engine
from kanso.gmail.client import http_client
from kanso.sync import drafts, outbox, push
from kanso.sync.scheduler import Scheduler

log = logging.getLogger("kanso.worker")

# Touched while the main loop runs; the compose healthcheck reads its age.
HEARTBEAT = Path("/tmp/kanso-worker-heartbeat")
# How often the worker looks for connected or disconnected accounts.
ACCOUNTS_REFRESH_SECONDS = 15
# The safety-net poll behind push notifications; without Pub/Sub, polling is all there is.
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
    # Write-backs to Gmail (archive, labels, read state…) queued by the API.
    writes = asyncio.create_task(outbox.run(http))
    # Sends left unfinished by a crash, and the contacts for recipient autocomplete.
    housekeeping = asyncio.create_task(drafts.run(http))
    # AI labeling of new inbox mail, only with an Anthropic API key.
    if settings.ai_enabled:
        classify = asyncio.create_task(labeling.run())
        log.info("AI labeling on (%s)", settings.ai_label_model)
    else:
        classify = None
        log.info("AI labeling off (no ANTHROPIC_API_KEY)")
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
    if classify:
        classify.cancel()
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
