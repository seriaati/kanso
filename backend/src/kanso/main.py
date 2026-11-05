import mimetypes
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from kanso.api import (
    accounts,
    actions,
    ai,
    assistant,
    auth,
    cleanup,
    compose,
    events,
    health,
    labels,
    notifications,
    prefs,
    releases,
    sync,
    threads,
    unsubscribe,
)
from kanso.core.hardening import Hardening
from kanso.core.security import require_session
from kanso.core.settings import settings
from kanso.db.redis import redis
from kanso.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield
    await engine.dispose()
    await redis.aclose()


# No /docs, /redoc or /openapi.json: kanso's API is for its own frontend only.
app = FastAPI(title="kanso", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Starlette skips text/event-stream, so SSE isn't buffered.
app.add_middleware(GZipMiddleware, minimum_size=1000)
# Added last, so it runs first.
app.add_middleware(Hardening)
app.include_router(health.router, prefix="/api")
app.include_router(releases.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
protected = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
for router in (
    accounts.router,
    threads.router,
    actions.router,
    cleanup.router,
    unsubscribe.router,
    labels.router,
    compose.router,
    sync.router,
    events.router,
    ai.router,
    assistant.router,
    prefs.router,
    notifications.router,
):
    protected.include_router(router)
app.include_router(protected)


# Slim images have no /etc/mime.types; fonts would go out as octet-stream and be gzipped again.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def serve_frontend(dist: Path) -> None:
    dist = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist):
            # Vite's assets/ have content-hashed names, so they never change.
            immutable = path.startswith("assets/")
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})


if settings.frontend_dist.is_dir():
    serve_frontend(settings.frontend_dist)
