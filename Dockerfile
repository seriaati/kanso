# syntax=docker/dockerfile:1

FROM oven/bun:1.4-slim AS frontend
WORKDIR /frontend
RUN --mount=type=cache,target=/root/.bun/install/cache \
    --mount=type=bind,source=frontend/package.json,target=package.json \
    --mount=type=bind,source=frontend/bun.lock,target=bun.lock \
    bun install --frozen-lockfile
COPY frontend/ .
RUN bun run build

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY backend/ /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Must use the same Python as the builder: the venv points to it.
FROM python:3.13-slim-trixie
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot
COPY --from=backend --chown=nonroot:nonroot /app /app
COPY --from=frontend --chown=nonroot:nonroot /frontend/dist /app/static
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/static
USER nonroot
WORKDIR /app
EXPOSE 8000
# Migrations run on every start; the worker container overrides this command.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn kanso.main:app --host 0.0.0.0 --port 8000"]
