# kanso (簡素)

簡素 means *simplicity*. It is the product's name and its main design rule.

kanso is a self-hosted, open source (AGPL-3.0) Gmail client for reaching **inbox zero**. It is
inspired by Shortwave but drops the parts we don't need, and keeps **your full email history with
no 90-day limit and no paid tier**.

It is a personal project for personal use, not a SaaS product. Optimize for one person running it
on their own machine or server with `docker compose up`.

This file is a stable, high-level guide. Don't update it as part of feature work; change it only
when the app's structure or direction changes.

## Core principles

Check every change against these. If a change conflicts with one, stop and ask.

1. **Simplicity first.** Each feature has to earn its place. When unsure, leave it out. Prefer
   fewer dependencies, fewer services and fewer settings.
2. **Full history, always.** Every message in the mailbox is synced and searchable. Never add an
   artificial limit on history.
3. **Lightweight and fast.** The UI feels instant, actions are optimistic, and the bundle and
   container footprint stay small. Serve reads from the local mirror, not from round trips to Gmail.
4. **Minimal, elegant, aesthetic.** A calm UI with plenty of whitespace, restrained color, clear
   typography and no clutter. Fewer buttons and more keyboard.
5. **Easy to self-host.** One `docker compose up`, configured through a single `.env`. No external
   services beyond Gmail, Google Cloud Pub/Sub (for instant mail notifications) and the Anthropic API.
6. **Personal scale.** One user. Don't build for multi-tenancy, horizontal scaling or enterprise needs.

## Product decisions (settled)

| Area | Decision |
|---|---|
| Email provider | **Gmail only**, via the Gmail API with OAuth. No IMAP/Outlook. |
| Sync model | **Two-way sync.** Gmail is the source of truth. kanso mirrors *all* of it in Postgres and writes changes back to Gmail, so other Gmail clients stay consistent. |
| Accounts | **One user, multiple Gmail accounts**, one in view at a time, with no combined view. |
| AI provider | **Anthropic (Claude)** with the user's own API key. **Optional**: without a key, kanso is a plain email client and AI features are hidden. |
| Search | **Postgres full-text search.** No vector search. |
| Clients | **Web app plus an installable PWA**, desktop-first and usable on a phone. No native apps. |
| Auth | A simple single-user password login. |

## Scope

**In:** read, compose, reply and forward · a done/archive flow with undo · bundles (group the
inbox by label and clear a group at once) · keyboard-first navigation and a command palette ·
AI auto-labeling into user-defined labels (real Gmail labels) · an assistant chat that works on the
local mirror · thread summaries.

**Out (until the user decides otherwise):** snooze and reminders · AI-drafted replies · semantic
search · non-Gmail providers · multi-user or teams · calendar · todos · scheduled send · read
receipts or tracking · native apps · billing or usage limits. Don't add these "while you're at
it"; propose them first.

## UI principles

kanso is a tool for working through email, not a dashboard. **Function comes before looks.**

- **Email comes first.** The thread list is the main surface: compact, scannable rows, many at once.
- **No decorative real estate.** No hero numbers, banners or stat tiles.
- **Key controls always in view, in the same place**: account switcher, Compose, search, labels.
- **The assistant docks beside the mail and never covers it.**
- **Edit in a modal, not inline.** Editing or creating something (a label, an account) opens a
  dialog; a list never turns into a form in place.
- **Zen, paper and calm**: warm paper tones, ink-like text, restrained accents, quiet typography,
  light and dark themes, subtle motion that is off under `prefers-reduced-motion`.

The layout is a sidebar, a thread list and an email pane (split view); phones get thumb-reach
controls at the bottom. Reference mockups: desktop
https://claude.ai/artifact/V1JQgMWnzg11fKx9W61eUb (the "3 · Chosen" artboard), phone
https://claude.ai/artifact/Uurj4UQ6CnFdF4PV8Jezt2. Design tokens (colors, type, radii, motion)
live in `frontend/src/styles/tokens.css`; use them, don't hard-code values.

## Architecture

| Component | Tech | Role |
|---|---|---|
| Database | PostgreSQL | Full mail mirror, sync state, full-text index |
| Cache / queue | Redis | Caching, background jobs, live events to the browser |
| Backend | Python + FastAPI | REST API, Gmail OAuth, AI orchestration; serves the built frontend |
| Worker | Same codebase, separate container | Gmail sync, write-back to Gmail, AI labeling |
| Frontend | React + Vite (Bun) | SPA plus PWA |
| Deploy | Docker Compose | One image for app and worker, plus Postgres and Redis |

- **Sync**: a background full backfill, then incremental syncs via the Gmail History API,
  triggered by Gmail push through a Pub/Sub *pull* subscription (no public endpoint needed), with a
  slow safety-net poll. The UI works while the backfill runs.
- **Write-back**: user actions change the mirror at once and queue the Gmail change in an outbox
  that the worker sends and retries.
- **AI** runs only in the backend or worker; the API key stays server-side. Send Claude only what
  each feature needs. Assistant actions that change mail need explicit user confirmation.
- Keep the module layout boring and flat. No abstraction layers for providers we don't support.

## Code layout

```
backend/                     Python package (uv), tests, migrations
  src/kanso/
    main.py                  FastAPI app; mounts the API routers and the built frontend
    worker.py                worker entry point: runs the sync, outbox, push and AI labeling loops
    api/                     one router per area: threads, actions, compose, labels, accounts,
                             assistant, ai, auth, events (SSE), notifications, prefs, sync, health
    sync/                    engine (backfill + history sync), store (writes to the mirror),
                             outbox (write-back), push (Pub/Sub), scheduler, drafts, photos,
                             notify (web push)
    gmail/                   Gmail API client, OAuth, MIME parsing, message building
    ai/                      labeling, summaries, assistant + its tools, body trimming, usage
    db/                      SQLAlchemy models, session, Redis, full-text search, contacts, prefs
    core/                    settings (.env), password/session security, CSRF hardening
    demo_seed.py, hash_password.py, reset_password.py   CLI helpers (python -m kanso.<name>)
  alembic/versions/          migrations, the source of truth for the schema
  tests/                     pytest; fake_gmail.py stands in for the Gmail API
frontend/
  src/main.tsx               entry point
  src/app/                   App shell, API client and shared state hooks (mail, accounts,
                             labels, actions, compose, theme, accent, density, …)
  src/components/            shared UI pieces (Icon, Menu, Dialog, Avatar, Resizer)
  src/features/<area>/       one folder per UI area: sidebar, threads, reader, compose,
                             assistant, labels, settings, phone, login, help, install
  src/styles/                tokens.css (design tokens), base.css, fonts
  public/                    service-worker push handler, theme bootstrap, headers
  functions/                 optional Cloudflare Pages proxy for /api
docs/SETUP.md                self-hosting guide (Google Cloud, OAuth, Pub/Sub, .env)
docker-compose.yml           production stack; docker-compose.demo.yml + demo.sh run a demo
Dockerfile                   builds the frontend, then the backend image that serves it
```

## Commands

- Backend (in `backend/`): `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyrefly check`, `uv run pytest` (needs `TEST_DATABASE_URL` pointing at Postgres).
- Frontend (in `frontend/`): `bun run dev` (proxies `/api` to `localhost:8000`), `bun run lint`,
  `bun run build` (type-checks first).
- These are what CI runs (`.github/workflows/ci.yml`).

## Known constraints

- Each self-hoster needs their **own Google Cloud OAuth client**. Gmail scopes are "restricted",
  and an OAuth app left in *Testing* issues refresh tokens that expire after about 7 days, so the
  setup docs must cover publishing it to *In production* (unverified) for personal use.
- The PWA is reached from outside localhost over HTTPS.
