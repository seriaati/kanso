# kanso — implementation plan

This is the phased build plan. Read `CLAUDE.md` first: it holds the product principles, scope and
design system this plan builds on. If this plan conflicts with `CLAUDE.md`, `CLAUDE.md` wins.

## How to use this plan

- Work through the phases **in order**. Each phase ends with a usable, verified state of the app.
- Before starting a phase, re-read its **Done when** list. That list is the definition of finished.
- When you finish a task, tick its checkbox here in the same change. When a phase is done, update
  the status table.
- If you need to deviate (a library doesn't work, a better approach turns up), stop and ask the user,
  then record the decision in **Decision log** at the bottom.
- Stay inside the phase. Don't pull in work from later phases "while you're there".

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Foundations | done |
| 1 | Login and Gmail accounts | built — awaiting user verification with real Google account |
| 2 | Sync engine (read-only mirror) | built — awaiting user verification with real Google account |
| 3 | Reading UI (split view) | built — awaiting user verification with real Google account |
| 4 | Triage actions and write-back | built — awaiting user verification with real Google account |
| 5 | Compose, reply, forward | built — awaiting user verification with real Google account |
| 6 | AI labeling and bundles | built — awaiting user verification with real Google account and API key |
| 7 | Thread summaries | built — awaiting user verification with real API key |
| 8 | Assistant | built — awaiting user verification with real API key |
| 9 | PWA, mobile layout, release | built — awaiting user verification (phone install over HTTPS) |

Phases 0–5 produce a complete, AI-free email client, which is also the "no API key" mode.
Phases 6–8 add AI on top of it. Phase 9 makes it installable and ready to self-host.

## Technical baseline

These choices are settled (confirmed by the user 2026-11-01). Changing one needs the user's OK and a Decision log entry.

| Area | Choice | Why |
|---|---|---|
| Backend | Python 3.13, FastAPI, managed with **uv** | Settled stack |
| DB access | SQLAlchemy 2.x (async) + asyncpg, **Alembic** migrations | Standard and well supported |
| Settings | pydantic-settings, all config from `.env` | One config file for self-hosters |
| Gmail client | **httpx** calling the Gmail REST API directly, not `google-api-python-client` | Async, lighter, and we only need ~10 endpoints |
| OAuth | Google OAuth 2.0 web flow (authlib or a small hand-rolled flow) | |
| Background work | **Worker process with asyncio loops plus a Postgres outbox table. No job-queue library.** | Fewer dependencies. Postgres gives durability and retries; Redis stays a cache and pub/sub |
| Mail change notifications | **Gmail push**: `users.watch` → a Cloud Pub/Sub topic → a **pull** subscription the worker reads (Pub/Sub REST via httpx, `google-auth` for the service-account token). A slow safety-net poll (~15 min) catches dropped notifications | Instant updates without exposing kanso to the internet (pull, not push) |
| Live updates | Worker publishes changes to Redis pub/sub; the API streams them to the browser over **SSE** | Simple and one-directional |
| Search | Postgres full-text search (`tsvector` + GIN) plus `pg_trgm` for sender and email matching | Settled decision |
| HTML email | Sanitized server-side with **nh3**, rendered in a sandboxed `<iframe srcdoc>` with no scripts | Safety |
| AI | Official `anthropic` Python SDK. Model IDs come from env: a small, fast model for labeling and a stronger one for the assistant and summaries. Check current model IDs (claude-api skill or docs) when implementing | Settled decision |
| Frontend | React + TypeScript, **Vite run with Bun** (Bun as package manager and runtime) | Vite has the best PWA tooling (`vite-plugin-pwa`) |
| Frontend data | TanStack Query for server state, React Router, TanStack Virtual for long lists | Small, focused libraries |
| Styling | Plain CSS Modules + CSS custom properties holding the design tokens from `CLAUDE.md`. No UI kit and no Tailwind | Tokens map 1:1 and keep the bundle small |
| Serving | FastAPI serves the built frontend. **One image, two containers** (`app`, `worker`) plus `postgres` and `redis` | Smallest compose file |
| HTTPS | Not handled by kanso. The self-hoster puts it behind their own reverse proxy (Caddy, Tailscale…) | Out of scope; document it |
| Quality | ruff (lint and format), a type checker, pytest; ESLint + `tsc` on the frontend | |

### Repository layout (target)

```
kanso/
├── CLAUDE.md
├── docs/            PLAN.md, SETUP.md (Google Cloud + deploy), ARCHITECTURE.md (optional, later)
├── backend/
│   ├── pyproject.toml, uv.lock
│   ├── alembic/
│   ├── src/kanso/
│   │   ├── api/       FastAPI routers (auth, accounts, threads, actions, compose, ai, events)
│   │   ├── core/      settings, security (sessions, encryption), logging
│   │   ├── db/        models, session
│   │   ├── gmail/     OAuth, REST client, MIME parsing
│   │   ├── sync/      backfill, incremental sync, outbox processor
│   │   ├── ai/        labeling, summaries, assistant + tools
│   │   └── worker.py  worker entry point
│   └── tests/
├── frontend/
│   ├── package.json, bun.lock, vite.config.ts
│   └── src/  app/, components/, features/{sidebar,threads,reader,assistant,compose}, styles/tokens.css
├── Dockerfile          multi-stage: bun builds the frontend, uv installs the backend, slim runtime
├── docker-compose.yml
└── .env.example
```

### Core data model (sketch, refined in Phase 2)

- `accounts`: Gmail address, display name, color, **encrypted** refresh token, last `historyId`, sync status.
- `labels`: per account, Gmail label id, name, type (system/user), color. Phase 6 adds an AI
  description and an `is_bundle` flag.
- `threads`: per account, Gmail thread id, subject, snippet, last message time, and derived flags
  (in inbox, unread, starred, important).
- `messages`: Gmail message id, thread, from/to/cc, date, subject, snippet, text body, sanitized
  HTML body, selected headers (Message-ID, References…), search `tsvector`.
- `message_labels`: Gmail labels belong to messages; thread-level state is derived from them.
- `attachments`: **metadata only**. Content is fetched from Gmail on demand, which keeps the DB small.
- `outbox`: queued write-backs to Gmail (archive, label, star…) with attempts and last error.
- Later phases add `summaries`, `assistant_conversations`, `assistant_messages` and `settings`.

Gmail mapping to remember: **Done = remove `INBOX`**, Delete = move to `TRASH`, Star = `STARRED`,
Important = `IMPORTANT`, read state = `UNREAD`.

---

## Phase 0 — Foundations

**Goal:** an empty but real app. `docker compose up` brings up every service, and both halves have
tooling, tests and the design tokens in place.

- [x] Backend skeleton with uv: FastAPI app, `/api/health`, settings from `.env`, ruff, type checker, pytest
- [x] Postgres and Redis connections; Alembic set up with an empty first migration
- [x] Worker entry point (`python -m kanso.worker`) that starts and idles
- [x] Frontend skeleton (Vite + React + TS via Bun), ESLint, `tsc`
- [x] `styles/tokens.css` with every token from the `CLAUDE.md` design system, plus the two fonts
- [x] Static app shell matching the chosen mockup (sidebar | thread list | email pane | docked
      assistant) with placeholder content and no data
- [x] Multi-stage `Dockerfile`, `docker-compose.yml` (`app`, `worker`, `postgres`, `redis`), `.env.example`
- [x] Optional: GitHub Actions running lint, type check and tests on both halves

**Done when:** `docker compose up` gives you a shell at `http://localhost:<port>` that visually
matches the mockup layout, `/api/health` reports DB and Redis as OK, and lint, type checks and tests
pass.

## Phase 1 — Login and Gmail accounts

**Goal:** the single user can log in and connect one or more Gmail accounts.

- [x] Single-user password login. The password hash (argon2) comes from env, stored in a signed,
      httpOnly, SameSite cookie session. Login attempts are rate-limited through Redis. A CLI or
      script generates the hash
- [x] Every `/api` route requires the session, except login and health
- [x] Google OAuth "Connect account" flow with offline access, scopes `gmail.modify` +
      `openid email` (verify these scopes cover everything, including send)
- [x] Refresh tokens encrypted at rest (key from env), with automatic access-token refresh
- [x] List, rename/recolor and disconnect accounts (disconnecting deletes that account's mirrored data)
- [x] Account switcher in the sidebar (All accounts or one account), wired to real accounts
- [x] `docs/SETUP.md`: create the Google Cloud project, enable the Gmail API, create the OAuth
      client, set redirect URIs, and **publish the app to "In production"** so refresh tokens
      don't expire after 7 days. It also covers Pub/Sub: create a topic, grant
      `gmail-api-push@system.gserviceaccount.com` the Publisher role on it, create a **pull**
      subscription, and create a service account with the Subscriber role whose key goes in `.env`.
      Finally `.env`, the password hash, and running behind HTTPS

**Done when:** after a fresh `docker compose up`, following `SETUP.md` alone is enough to log in and
connect two Gmail accounts, both survive a restart, and a logged-out browser gets 401 from the API.

## Phase 2 — Sync engine (read-only mirror)

**Goal:** a complete local mirror of every connected mailbox, **all history**, kept up to date.

- [x] Final schema and migrations for accounts, labels, threads, messages, message labels and attachments
- [x] Label sync (system and user labels)
- [x] **Initial backfill**: page through *all* message ids and fetch each message (batched,
      respecting Gmail quota with backoff on 429/5xx). Parse MIME into text and sanitized HTML,
      store attachment metadata. It must be resumable after a restart and must not block the UI.
      Record progress
- [x] **Incremental sync** with the History API. Handles messages added and deleted and labels
      added and removed. If the `historyId` has expired (404), fall back to a full resync
- [x] **Push trigger**: call `users.watch` for each account against the shared Pub/Sub topic and
      renew it daily (it expires after 7 days). The worker pulls the subscription; each
      notification (email address + historyId) triggers an incremental sync for that account,
      debounced and acknowledged only after the sync succeeds. A safety-net poll every ~15 min
      catches anything missed
- [x] Full-text search index on messages (subject, from, to, body), with a search API supporting
      plain text plus a small operator set: `from:`, `to:`, `label:`, `is:unread`, `is:starred`,
      `has:attachment`, `before:`, `after:`
- [x] Read API: thread list (filter by account, mailbox or label; cursor pagination), thread detail
      with messages, attachment download proxy
- [x] Worker publishes "threads changed" events to Redis, and an SSE endpoint streams them to the browser
- [x] A **demo seed** command that fills the DB with a fake mailbox (like the mockup data), so UI
      work and tests don't need Google credentials
- [x] Tests: MIME parsing fixtures, plus sync logic against a fake Gmail (recorded responses)

**Done when:** connecting a real account backfills its entire history; message and thread counts
for INBOX and a few labels match Gmail's web UI; a new mail or a label change made in Gmail appears
in the API **within a few seconds** through Pub/Sub; sync still catches up when Pub/Sub is
unavailable (safety-net poll); and a restart mid-backfill resumes instead of starting over.

## Phase 3 — Reading UI (split view)

**Goal:** the real app for reading mail, matching the design system.

- [x] Sidebar: account switcher, Compose button (opens a placeholder until Phase 5), mailboxes
      (Inbox, Done, Sent, Drafts) and labels with color dots and counts
- [x] Thread list: search box; compact single-line rows (unread dot, sender, subject — snippet, star,
      time); virtualized; infinite scroll. Grouping is plain date order for now; label sections arrive in Phase 6
- [x] Email pane: subject, messages in the thread (latest expanded, older ones collapsed), sanitized
      HTML in a sandboxed iframe, attachments list with download
- [x] **Remote images blocked by default.** They are enforced by the iframe's CSP (no remote
      `img-src`, including CSS `url()` backgrounds), not just by stripping `<img>` tags. A quiet "Show
      images" bar loads them for that email only after an explicit click. No per-sender allowlist
      and no auto-loading. Inline `cid:` images, which are attachments rather than remote images,
      always show through the attachment proxy
- [x] Toolbar and hover-action icons rendered in place, but disabled until Phase 4
- [x] Keyboard: `j`/`k` move, `Enter`/`o` open, `/` search, `Esc` back, `?` shortcut help
- [x] Live updates from SSE (new mail shows up without a refresh)
- [x] An empty-inbox state that is calm and quiet, not a celebration screen
- [x] Assistant panel area present but collapsed or hidden (the AI-off look)

**Done when:** on a real account you can browse every mailbox and label, open and read any thread
from any year, search all mail, and drive the main flows from the keyboard. Side by side with the
mockup, it matches.

## Phase 4 — Triage actions and write-back

**Goal:** inbox zero works, and every change shows up in Gmail too.

- [x] Actions API plus `outbox` table: Done (archive), Delete (trash), Star, Important, read/unread,
      add/remove label, move to inbox. Work on single threads and in bulk
- [x] Optimistic UI: apply locally at once, roll back if the action fails for good
- [x] Undo for Done and Delete (toast, `z`), cancelling the outbox entry if it hasn't been sent yet
- [x] Outbox processor in the worker: sends to Gmail with retries and backoff, and stops the
      incremental sync from bouncing the change back
- [x] Toolbar icons and hover quick actions enabled, with shortcuts: `e` done, `#` delete, `s` star,
      `=` important, `l` label picker, `u` mark unread, `x` select
- [x] Opening a thread marks it read (and writes that back)
- [x] Label management: create, rename, recolor, delete (synced to Gmail)

**Done when:** archiving, deleting, starring and labeling in kanso are reflected in Gmail's web UI
within seconds, undo works, nothing is lost or duplicated if the worker restarts with a non-empty
outbox, and the inbox can be cleared to zero with the keyboard alone.

## Phase 5 — Compose, reply, forward

**Goal:** kanso is a complete email client.

- [x] Compose panel (docked; it doesn't cover the thread list) with to/cc/bcc autocomplete from
      mirrored contacts, subject, and a body editor (plain rich text: bold, italic, links, lists; nothing more)
- [x] Reply, reply all and forward, with correct `In-Reply-To`/`References` headers so Gmail threads them
- [x] Choose the sending account (defaults to the account that received the thread)
- [x] Attachments: upload, show, remove
- [x] Send through Gmail (the sent mail appears in Sent via sync). Drafts autosave as Gmail drafts
- [x] Shortcuts: `c` compose, `r` reply, `a` reply all, `f` forward, `Cmd/Ctrl+Enter` send

**Done when:** you can send new mail, replies and forwards with attachments from either account;
recipients get correctly threaded mail; and drafts survive a reload and appear in Gmail's Drafts.

## Phase 6 — AI labeling and bundles

**Goal:** incoming mail sorts itself, and the inbox shows the label-grouped view from the mockup.

- [x] AI is optional: with no `ANTHROPIC_API_KEY` every AI feature is hidden and the app works as in Phases 0–5
- [x] Label settings: each label can have a natural-language description ("bills, statements and
      payment reminders") and an "AI-managed" toggle. Any label can be marked as a **bundle**
- [x] Classifier in the worker: new inbox messages are classified against the AI-managed labels in
      one cheap model call (structured output), and the result is applied as a real Gmail label
      through the outbox. Low-confidence results stay unlabeled
- [x] Never re-classify a thread the user has labeled by hand; a manual label change is final
- [x] An optional, explicit "Label existing inbox" action. **No automatic AI backfill over all
      history**, to control cost
- [x] Inbox view grouped into **sections by label**, each with its count and "Done all"; bundles
      shown as collapsed rows at the bottom, as in the mockup
- [x] Track token usage per feature in a small table, shown in settings (for now one line in the
      account menu; Phase 9's settings page shows it properly)

**Done when:** with labels described, new mail arrives already labeled (also visible in Gmail);
the grouped inbox and bundles match the mockup; "Done all" clears a section with undo; and removing
the API key turns all of this off cleanly.

## Phase 7 — Thread summaries

**Goal:** a short summary of a thread when it's worth having one.

- [x] Summary generated on open for threads above a length threshold (or on demand from a
      button), stored and keyed by the thread's latest message id so it updates when new mail arrives
- [x] Shown as the "Summary" card at the top of the assistant panel for the open thread (as in the mockup)
- [x] Sends only the content needed (trimmed quoted text and signatures) to keep cost and data
      exposure low

**Done when:** opening a long thread shows a correct summary within a couple of seconds, reopening
it costs no new AI call, and a new reply produces a fresh summary.

## Phase 8 — Assistant

**Goal:** the docked assistant can answer questions about your mail and act on it safely.

- [x] Assistant panel docked at the bottom of the email pane: "this thread" context chip, input,
      streamed replies (SSE), minimize/restore, and a shortcut to focus it
- [x] Tool use against the **local mirror**: `search_threads`, `get_thread`, `list_labels`, and the
      mutating `archive`, `apply_label`, `star`, `mark_read`
- [x] **Mutating tools need explicit confirmation**: the assistant proposes and the UI shows a
      Confirm/Cancel card. Confirmed actions go through the same actions API and outbox as Phase 4
- [x] Thread references in answers are clickable and open the thread
- [x] Conversations stored, with "New conversation" and a short history
- [x] The assistant never sends email (AI-drafted replies are out of scope)

**Done when:** questions like "what needs a reply before Friday?" or "find my Lisbon booking from
last year" get correct, linked answers from the whole mailbox; proposed actions only happen after
Confirm; and the panel never covers the list or the email.

## Phase 9 — PWA, mobile layout, release

**Goal:** installable on a phone, robust to self-host, ready for v1.

- [x] PWA: manifest, icons, service worker caching the app shell only (no offline mail in v1)
- [x] Mobile layout: at phone width the split view becomes list → thread navigation; the sidebar
      becomes a drawer; the assistant becomes a bottom sheet on the thread screen
- [x] Touch: hover actions become swipe (done / delete), with tap targets of at least 44px
- [x] Settings page: accounts, labels and AI descriptions, sync status, AI usage, change password
- [x] Hardening: security headers and CSP, CSRF protection on state-changing routes, a review of the
      auth and session code, a dependency audit
- [x] Ops: Postgres backup/restore documented, upgrade notes, `docker compose pull && up` works
      with migrations running on start
- [x] README with a screenshot, quick start, and links to `SETUP.md`

**Done when:** a new self-hoster can go from clone to a working install on their phone with only
the README and `SETUP.md`, and a production-like deploy behind HTTPS passes a basic security review.

---

## Open questions (resolve when the phase arrives)

- ~~**Dark theme**: not designed yet. Decide after v1.~~ Added 2026-11-02 at the user's request (see the Decision log).

## Decision log

Record deviations and decisions here: date, decision, reason.

- 2026-11-01: Plan created. Stack and scope as in `CLAUDE.md`.
- 2026-11-01: The user confirmed the baseline: no job-queue library (worker loops + Postgres
  outbox), Vite run with Bun, one image running as two containers (`app`, `worker`), and
  attachments stored as metadata only, fetched from Gmail on demand.
- 2026-11-01: Remote images in emails are blocked by default and load only when the user clicks
  "Show images" on that email.
- 2026-11-01: Sync is triggered by Gmail push through Google Cloud Pub/Sub (the user's choice),
  using a **pull** subscription so kanso needs no public endpoint, plus a ~15 min safety-net poll.
- 2026-11-01 (Phase 0): Type checker is **pyrefly** (with ruff and pytest).
- 2026-11-01 (Phase 0): Fonts are self-hosted with `@fontsource` packages instead of the Google
  Fonts CDN the mockup used, so the browser talks to no extra external service. At the user's
  request (Phase 1) the **full subsets** are bundled, Japanese included, so Japanese text renders in
  the brand fonts. Browsers download only the `unicode-range` chunks a page uses, but the cost is
  real: the `@font-face` rules make the main CSS much bigger (see the CJK entry below for totals)
  and the font files add tens of MB to the image.
- 2026-11-01 (Phase 0): create-vite now scaffolds oxlint and TypeScript 7; replaced oxlint with
  ESLint as the baseline says. typescript-eslint can't use TS 7 yet, so `package.json` follows
  Microsoft's side-by-side setup: `typescript` is aliased to `@typescript/typescript6` (for ESLint)
  and `@typescript/native` (TS 7) provides `tsc`. Revisit when typescript-eslint supports TS 7.
- 2026-11-01 (Phase 0): Starlette deprecates `httpx` for its `TestClient` in favor of `httpx2`, so
  the backend dev dependency is `httpx2`. Phase 1 should check whether the Gmail client (baseline:
  httpx) should also use `httpx2`.
- 2026-11-01 (Phase 0): Migrations run in the `app` container before uvicorn starts; the `worker`
  waits for `app` to be healthy. The worker touches a heartbeat file for its compose healthcheck.
- 2026-11-01: The user wants CJK text in brand-matched fonts. Japanese uses the brand fonts (full
  subsets, above); Chinese uses **Noto Sans SC + TC** (400, 700) behind Zen Maru Gothic and **Noto
  Serif SC + TC** (700) behind Shippori Mincho B1, all full-subset `@fontsource` imports. The stacks
  are the `--font-ui` / `--font-heading` tokens; `:lang(zh…)` overrides put the Chinese font first
  (SC for `zh`, `zh-Hans`, `zh-CN`, `zh-SG`; TC for `zh-Hant`, `zh-TW`, `zh-HK`), since the
  Japanese fonts would otherwise draw shared Han characters with Japanese glyph shapes. Cost: main
  CSS 1.19 MB (521 KB gzipped, mostly `@font-face` rules), `dist/` 60 MB (woff2 27 MB plus woff
  fallbacks 32 MB the browser never fetches), image 420 MB. **For Phase 3**: set `lang` on message
  subjects and bodies when it can be detected (`Content-Language` header, charsets like
  GB2312/GBK/Big5, or simple script detection), and make the fonts available inside the sandboxed
  email iframe too.
- 2026-11-01 (Phase 1): The Gmail/Google HTTP client is **`httpx2`**, not `httpx`. It is the
  maintained continuation of httpx (same API, by Pydantic; `httpx` itself has had no stable release
  since 0.28.1), and Starlette's TestClient already needs it, so there is one HTTP stack.
- 2026-11-01 (Phase 1): The OAuth web flow is **hand-rolled** with httpx2 (auth URL, code exchange,
  refresh, revoke: ~100 lines) instead of authlib. The email comes from the ID token returned by
  the token endpoint (no signature check needed over a direct TLS call, per OpenID Connect).
  `prompt=consent` + `access_type=offline` so every connect returns a refresh token.
- 2026-11-01 (Phase 1): Scopes verified against Google's docs: `gmail.modify` is accepted by
  `messages.send`, drafts, labels create/update/delete, `history.list`, `users.watch`, and
  message/thread modify and trash. It lacks only immediate permanent deletion (bypassing Trash),
  which kanso doesn't do. `openid email` gives the account's address. The callback rejects a
  grant where the user unticked the Gmail permission.
- 2026-11-01 (Phase 1): One **`SECRET_KEY`** both signs the session and encrypts refresh tokens
  (two keys derived with HKDF), to keep `.env` short. Changing it signs out and needs reconnecting.
  Tokens are encrypted with Fernet (`cryptography`); passwords are hashed with `argon2-cffi`.
- 2026-11-01 (Phase 1): The session is a **stateless signed cookie** (`<expiry>.<HMAC>`, 30 days,
  httpOnly, SameSite=Lax), not a Redis session, so it survives Redis restarts. Lax (not Strict) so
  the cookie comes along on Google's redirect back. The signature covers the password hash, so a
  new password signs out every session. `Secure` follows `PUBLIC_URL` (https → Secure) instead of a
  separate setting; `PUBLIC_URL` also builds the redirect URI.
- 2026-11-01 (Phase 1): Login rate limit: 5 failed attempts per client IP per 15 minutes (Redis
  counter), then 429 with `Retry-After`. Behind a reverse proxy the real client IP needs uvicorn's
  `FORWARDED_ALLOW_IPS` (documented in SETUP.md); without it, all clients share the proxy's limit.
- 2026-11-01 (Phase 1): Access tokens are cached **in process memory** (app and worker each
  refresh their own; at most a couple of refreshes per account per hour). OAuth `state` is kept in
  Redis for 10 minutes and is single-use. Disconnect also revokes the Google grant (best effort).
  New accounts are named after the email's local part and take the next color from the
  accent + label palette.
- 2026-11-01 (Phase 1): `accounts` has only what Phase 1 needs (email, display name, color,
  encrypted refresh token, created_at); `historyId` and sync status come with Phase 2's schema.
  The Pub/Sub settings are named now for SETUP.md (`GOOGLE_PUBSUB_TOPIC`,
  `GOOGLE_PUBSUB_SUBSCRIPTION`, `GOOGLE_PUBSUB_CREDENTIALS` = base64 of the service account JSON key)
  but nothing reads them until Phase 2.
- 2026-11-01 (Phase 1): Database tests run against a **real, disposable Postgres** given by
  `TEST_DATABASE_URL` (tables are dropped and recreated); without it they are skipped with a
  reason. CI runs a Postgres service. Redis is faked with `fakeredis` (dev dependency); Google with
  an httpx2 `MockTransport`.
- 2026-11-02 (user-approved): **Font loading.** The critical CSS now holds only the Latin and
  Latin Extended `@font-face` rules of Zen Maru Gothic (400, 700) and Shippori Mincho B1 (700),
  in `styles/fonts.css`; every other subset (Japanese, Chinese, Greek, Cyrillic) is in
  `src/fonts-deferred.ts`, imported after the first frame paints (script-inserted, so never
  render-blocking). A small Vite plugin removes the legacy `.woff` fallbacks from the fontsource
  CSS, so only woff2 ships. The API uses Starlette's `GZipMiddleware` (it skips
  `text/event-stream` and `font/woff2`; `.woff2` is registered as `font/woff2` because the slim
  image has no `/etc/mime.types`). Results: render-blocking CSS 1.19 MB / 505 KB gz → 21.7 KB /
  9.4 KB gz; the deferred font CSS is 983 KB / 401 KB gz; `dist/` 60 MB → 28 MB; image 420 MB →
  366 MB. In Chromium: first paint 28 ms and first contentful paint 80 ms, while the deferred CSS
  loads from 42 to 197 ms marked non-blocking; Japanese, Simplified and Traditional Chinese text
  then render in Zen Maru Gothic / Shippori Mincho B1, Noto Sans/Serif SC and Noto Sans TC. Until
  the deferred CSS arrives (a fraction of a second), CJK text shows in a system font.
- 2026-11-02 (Phase 2): New dependencies: **nh3** (baseline, HTML sanitizing) and **google-auth**
  (baseline, Pub/Sub; it brings pyasn1 and pyasn1-modules). Pub/Sub calls use a **self-signed
  service-account JWT** (audience `https://pubsub.googleapis.com/`, Google's "authorization
  without OAuth"), signed locally by google-auth, so there is no token exchange and no
  `requests` transport. New settings: none beyond the `GOOGLE_PUBSUB_*` trio named in Phase 1.
- 2026-11-02 (Phase 2): Messages are fetched with **`format=raw`** and parsed with the stdlib
  `email` package (compat32 policy) plus kanso's own charset handling: declared charsets map to
  their supersets (GB2312/GBK → GB18030, Big5 → Big5-HKSCS, Shift_JIS → CP932, ISO-2022-JP via
  JIS X 0213 for the NEC extras), unencoded 8-bit headers decode in the body's charset, and UTF-8
  is the fallback. The trade-off: a backfill downloads attachment bytes once (they are parsed
  for metadata and thrown away). With raw there are no Gmail attachment ids, so the attachment
  proxy refetches the raw message and extracts the part by its index. **For Phase 3**: inline
  `cid:` images each cost one raw fetch; add a small cache if that feels slow.
- 2026-11-02 (Phase 2): Gmail's batch endpoint isn't used. "Batched" fetching is **8 concurrent
  requests per account** over one pooled httpx2 client, with exponential backoff and jitter on
  429, 5xx and rate-limit 403s (honoring `Retry-After`). The backfill lists messages with
  `includeSpamTrash=true`, newest first, and commits after each page of 500; a restart resumes
  from the saved page token. A full resync (after a 404 on history) reuses the backfill: known
  messages only get their labels refreshed (`format=minimal`) and messages it didn't see are
  removed.
- 2026-11-02 (Phase 2): History replay: new messages are fetched in full; label changes apply as
  ordered deltas to the mirror (no API call); a change to a message the mirror lacks fetches it.
  Thread columns (flags, labels, senders, subject from the first message, snippet and `lang`
  from the latest) are recomputed by one SQL statement (`store.refresh_threads`) after every
  write. `in_trash` / `in_spam` mean *every* message is there; lists leave such threads out
  unless Trash or Spam is asked for.
- 2026-11-02 (Phase 2): Polling: every **15 min** behind Pub/Sub, every **2 min** when Pub/Sub
  isn't configured (logged clearly at worker start), so polling-only installs still feel
  current. Push notifications are debounced (1 s) per account and acknowledged after the sync
  succeeds; mid-backfill notifications are acknowledged at once, since the backfill replays
  history when it finishes. `users.watch` is renewed when less than 6 days are left (daily).
  The worker looks for connected/disconnected accounts every 15 s.
- 2026-11-02 (Phase 2): Search: Postgres's parsers don't split Chinese or Japanese into words,
  so CJK runs are indexed as **overlapping bigrams** (plus each run's last character) in a
  `simple`-config `tsvector`; a CJK query is the phrase of its bigrams, a single character a
  prefix match. Other words match as prefixes ("renew" finds "renewal"). `from:` uses a
  `pg_trgm` index on name + address; `before:`/`after:` are UTC days; `label:`, `is:unread`,
  `is:starred` are thread-level. Unknown operators are searched as text.
- 2026-11-02 (Phase 2): Language hint per message: kana → `ja`; Han → refined by charset or
  `Content-Language` (`zh-Hans`, `zh-Hant`, `ja`), else `zh`; Hangul → `ko`; otherwise the
  `Content-Language` value. Threads carry their latest message's hint.
- 2026-11-02 (Phase 2): Sync status is its own endpoint (`GET /api/sync`) rather than extra
  fields on `/api/accounts`. Demo data: accounts have a `demo` flag; `python -m kanso.demo_seed`
  replaces any previous demo account (idempotent), the worker never syncs it, and disconnecting
  it doesn't call Google. Database tests now build the schema from the Alembic migrations once
  per run and truncate between tests.
- 2026-11-02 (Phase 3): New frontend dependencies, both baseline: **react-router** 8.5.1 (only
  `BrowserRouter`, `Link`, `Navigate` and the location hooks; kanso parses its few URLs itself) and
  **@tanstack/react-virtual** 3.14.25. Main JS 274 KB / 85 KB gz → 356 KB / 112 KB gz (react-router
  ~35 KB and the virtualizer ~33 KB minified of that). URLs: `/inbox`, `/done`, `/sent`, `/drafts`
  (`/trash` and `/spam` work but aren't in the sidebar, which lists the design's four), `/label/<name>`,
  `/search?q=…`, plus `/<thread id>` for an open thread and `?account=<id>` for one account.
  Labels go by **name**, so the unified view merges same-named labels of every account; the
  thread list API now takes `label_id` more than once (threads in any of them).
- 2026-11-02 (Phase 3): The email iframe is `sandbox="allow-same-origin allow-popups
  allow-popups-to-escape-sandbox"`: **no `allow-scripts`, but same origin**, instead of an
  opaque origin. Reason: with scripts off inside the frame, only the parent can measure the
  content for auto-height, which needs same origin; it also lets the frame reuse the app's cached
  fonts (no CORS on font responses needed) and lets `cid:` images reach the attachment proxy with
  the session cookie. Same origin is only dangerous together with scripts, which the sandbox and
  the frame's CSP (`default-src 'none'`) both forbid, on top of the nh3 sanitizing. The CSP in the
  srcdoc allows images only from `/api/attachments/` and `data:` (that also covers CSS `url()`
  backgrounds), fonts and styles only from kanso; "Show images" re-renders that one email with
  `https: http:` added to `img-src`. Links open in a new tab (`<base target="_blank">`, nh3 adds
  `rel="noopener noreferrer"`). **For Phase 9**: a srcdoc frame inherits the app's own CSP, so
  the app CSP must allow `img-src https: http: data:` or "Show images" stops working.
- 2026-11-02 (Phase 3): Fonts: `src/fonts-deferred.ts` became `src/styles/fonts-full.css`,
  imported with Vite's `?url` so it is one file with a known URL: `main.tsx` links it after first
  paint (as before) and every email frame links the same file plus a small `email.css` (tokens,
  `:lang()` rules, body defaults), so Chinese and Japanese mail renders in Noto Sans SC/TC and Zen
  Maru Gothic inside the frame too. The woff-dropping Vite plugin became a PostCSS plugin, since
  `@import`ed CSS skips Vite's transform hook. The server marks Vite's hashed `assets/` as
  `public, max-age=31536000, immutable`, so each frame's font CSS is a cache hit.
- 2026-11-02 (Phase 3): Attachment responses are `private, max-age=31536000, immutable`
  (attachment ids are never reused), so inline images and repeat downloads cost no Gmail fetch.
  Demo attachments are now served from `demo_seed.DEMO_FILES` (a real PNG and a tiny PDF), the
  demo newsletters carry remote images (an `<img>` and a CSS background; since 2026-11-02 real
  photos on `images.unsplash.com`, the user's choice, so "Show images" shows something; the
  browser fetches them only after that click), and the Big5 and ISO-2022-JP demo mails have an HTML part in their own charset.
- 2026-11-02 (Phase 3): UI details. Sidebar: user labels only, alphabetical, with their unread
  thread count; a Gmail label color outside the design's palette is drawn in the nearest palette
  color (uncolored labels are neutral), so Gmail's bright colors don't break the calm palette.
  The sync note at the bottom of the sidebar shows backfill progress or a sync error, nothing
  otherwise. Keyboard: `j`/`k` move a cursor; while a thread is open it follows the cursor
  (replacing the history entry), `Enter`/`o` open, `Esc` closes the thread, then leaves search;
  keys also work after clicking into an email (the frame forwards them). Search covers all mail
  of the selected account(s) and runs on Enter. Disabled toolbar and hover actions use
  `aria-disabled` so their shortcut tooltips still show. Compose shows a placeholder in the email
  pane. The assistant is hidden (the AI-off look) and its static Phase 0 placeholder was
  removed; Phase 8 builds it from the mockup. Live updates batch SSE events for 300 ms and
  refetch everything after a reconnect.
- 2026-11-02 (Phase 4): **Outbox.** One `outbox` row per action and account: a label delta
  (`add_labels`, `remove_labels`, Gmail ids) on a fixed list of Gmail message ids, so every
  entry is idempotent. Adding/removing `TRASH` goes out as `messages.trash`/`untrash` (8 in
  parallel); everything else as `messages.batchModify` (≤1000 ids). Message ids rather than
  `threads.modify`, so a reply that arrives before the write-back stays in the inbox. The API
  applies the delta to the mirror and inserts the row in one transaction, then wakes the worker
  over Redis pub/sub (`kanso:outbox`; the worker also checks every 30 s). Each account's
  entries go out oldest first, one at a time (an entry waiting for a retry holds back that
  account's later ones, so Done and its undo never swap). An entry is claimed with
  `FOR UPDATE SKIP LOCKED` and marked sent in the same transaction as the Gmail call: a crash
  in between leaves it pending, and resending it changes nothing. Retries: 5 s, 15 s, 45 s…
  up to 6 attempts; 400 and 404 fail at once. A failed entry is **reconciled**: the messages'
  real Gmail labels are fetched back into the mirror, and the browser shows a quiet notice.
  Finished rows are deleted after 7 days. Demo accounts go through the outbox too but make no
  Gmail call.
- 2026-11-02 (Phase 4): **No bounce-back.** After every sync commit, the account's pending
  entries are re-applied over the mirror in a short transaction under a per-account advisory
  lock, which actions also take. So an action racing a sync's writes is seen either way, and a
  sync never undoes a change Gmail hasn't received (history replay, full resync, refetched
  messages). Tested with and without the re-apply step.
- 2026-11-02 (Phase 4): **Actions** (`POST /api/threads/actions`): done, inbox, delete, untrash,
  star/unstar, important/unimportant, read/unread, label/unlabel (user label **by name**, resolved
  per account, as the unified view merges labels by name), on up to 1000 threads across
  accounts. Stars and "mark unread" go on the thread's latest non-draft message only, as in
  Gmail; other adds go on every non-draft message. **Undo** (`POST /api/threads/undo`, by
  outbox entry ids) cancels an entry that is still pending and not being sent, and otherwise
  queues the inverse; it never waits on an entry in flight. The UI offers Undo (and `z`) for
  6 s after Done and Delete, from the moment the key is pressed.
- 2026-11-02 (Phase 4): **Label management goes to Gmail directly**, while the request waits,
  rather than through the outbox: it is rare, and Gmail's answer (a refused name, a clash) shows
  at once; no placeholder label ids are needed. In the unified view a new label is created in
  every account, and rename/recolor/delete apply to every same-named label. **Colors:** Gmail
  accepts only its own palette, so the backend writes a fixed Gmail color per kanso color and
  mirrors Gmail's color, as a sync would; the UI already draws the nearest kanso color. Each
  pair round-trips (tested): Ochre `#aa8831`, Slate `#285bac`, Stone `#666666`, Moss `#1a764d`,
  Plum `#8e63ce`, Rust `#cc3a21`, Sand `#c2c2c2` (text white, black on Sand). A label created
  from the `l` picker has no color (drawn neutral).
- 2026-11-02 (Phase 4): **Done excludes draft-only threads** (every message a draft): new
  derived column `threads.drafts_only`, kept by `refresh_threads`.
- 2026-11-02 (Phase 4): UI. `e` is Done, or "Move to inbox" when every target is already out of
  the inbox; `#` is Delete, or Restore in Trash; `s`, `=` toggle; `u` toggles read (marking the
  open thread unread returns to the list, or opening would mark it read again); `l` opens the
  label picker under the toolbar's Label button (type to filter, Enter toggles, or creates);
  `x` checks threads, and the toolbar and keys then act on all of them; `Esc` clears the
  selection. Rows show user labels as small chips and an important marker. The undo/failure
  notice sits at the foot of the sidebar, so it never covers mail. "More" holds Mark as
  read/unread. Live-update refetches wait while an action is in flight. Main JS 356 KB /
  112 KB gz → 369 KB / 116 KB gz.
- 2026-11-02 (Phase 5): **Drafts.** A `drafts` row holds what is being written (recipients,
  subject, the body's sanitized HTML, the quoted original, attachment metadata, the threading
  headers) and is created when a composer opens (`c`, `r`, `a`, `f`); nothing goes to Gmail until
  the first edit. Every autosave (1.5 s after typing pauses, plus on Esc, unmount and `pagehide`
  with `keepalive`) writes a real Gmail draft (`drafts.create`, then `drafts.update`; a draft
  deleted in Gmail meanwhile is created again) and mirrors its message at once, so it shows in
  Drafts here and in Gmail. Draft create/update use Gmail's **multipart upload** endpoint
  (`/upload/...?uploadType=multipart`, up to 35 MB), not JSON `raw`, so large attachments fit.
  A draft written in Gmail opens in the composer too (`POST /api/drafts/open` looks up its draft
  id with `drafts.list`). Demo drafts live in the mirror only. Drafts opened but never edited
  are dropped on Esc, or swept after a day.
- 2026-11-02 (Phase 5): **Attachments are never stored in Postgres.** Uploads (the file is the
  request body, so no `python-multipart`) go to a small file cache (`UPLOAD_DIR`, default the
  app container's temp dir) and into the Gmail draft with every save; after a save each
  attachment points at the Gmail draft message that holds it, so a lost cache (a restart) is
  refilled from Gmail, and cached files are deleted after sending/discarding or a day unused.
  **Forwarded attachments** are fetched from the original message at the first save (not only
  at send time), so the Gmail draft is complete; the user can remove them like uploads. Limit:
  25 MB in total, as in Gmail. Cost: every autosave re-uploads the attachments to Gmail
  (Gmail has no partial draft update). Demo attachments live only in the cache (a restart loses
  them, and the composer says so).
- 2026-11-02 (Phase 5): **Sending is synchronous** (the API waits for Gmail, so errors show in
  the composer), **through `drafts.send`, crash-safe, never twice.** The final message is saved to
  the Gmail draft, then the row is marked `sending_since` (committed), then `drafts.send` is
  called. Gmail deletes a draft when it sends it, so a repeated `drafts.send` either sends now or
  gets 404 = already sent. A marked draft is never saved or re-created again (409). A 4xx from
  Gmail means nothing went out: the mark is cleared and the draft stays, with Gmail's reason
  shown. An unknown outcome (5xx after retries, network) answers `pending`; the user's retry or
  the worker (every minute, for marks older than a minute) finishes it, and a send the worker
  finds refused is reported over SSE (`send_failed`). Saving and sending one draft is serialized
  with a Postgres advisory lock. The sent message is fetched back from Gmail into the mirror at
  once (Gmail may rewrite headers such as Message-ID, which later replies need). From: Gmail's
  default send-as display name (`settings.sendAs`, covered by `gmail.modify`); without one,
  From is left out and Gmail fills it in. Changing From moves the Gmail draft to the other
  mailbox (reply headers stay; the Gmail thread id, which is per mailbox, doesn't).
- 2026-11-02 (Phase 5): **MIME**: stdlib `EmailMessage` (`policy.default`): UTF-8 text and HTML
  alternatives, base64 when not ASCII (compact for CJK), RFC 2047 headers and RFC 2231
  filenames; inline images of a forwarded original are `multipart/related` to the HTML. Bcc is
  in the draft (Gmail delivers to it and strips it). Replies set `In-Reply-To`, `References`
  (the original's chain plus its Message-ID, last 20) and the Gmail `threadId`; forwards too, so
  they stay in the thread for the sender. `Re:`/`Fwd:` aren't doubled (also `RE`, `AW`, `SV`,
  `FW`, `回复`, `转发`…). Reply all leaves out the addresses of every connected account; replying
  to your own message writes to its recipients. The quote uses Gmail's `gmail_quote` markup (so
  Gmail collapses it), drops `<style>` and, in replies, `cid:` images; the plain-text part
  quotes with `> `. The quote's date is in the browser's time zone.
- 2026-11-02 (Phase 5): **Editor**: a `contenteditable` with the browser's own commands (bold,
  italic, link via an inline field and `Ctrl/Cmd+K`, bulleted and numbered lists), a ~50-line
  DOM sanitizer for pastes and stored drafts, and nh3 on the server (`b i a ul ol li p div br
  blockquote`, `href` with http/https/mailto only). No editor library. Main JS 369 KB / 116 KB gz
  → 388 KB / 122 KB gz.
- 2026-11-02 (Phase 5): **Contacts** for autocomplete are a materialized view (`contacts`):
  everyone you wrote to (weight 1) or who wrote to you (0.2), each message counting 1 plus up to
  9 more the more recent it is (60-day decay); drafts don't count. A trigram index serves the
  `ILIKE` match; prefix matches rank first. The worker refreshes it (`CONCURRENTLY`) at most every
  5 minutes and only when new mail arrived; the demo seed refreshes it too.
- 2026-11-02 (Phase 5): **UI.** `c` opens the composer in the email pane (it never covers the
  thread list); `r`, `a`, `f` (and the toolbar's Reply, Forward and More → Reply all) dock it under
  the open thread, or open the thread's existing draft. A thread's draft opens expanded under it;
  Esc closes it to a "Draft" row that reopens it; a thread of drafts only (from Drafts) is the
  composer itself. Subject is hidden in an inline reply. The quote is collapsed behind "···"
  ("Forwarded message" for forwards). `Cmd/Ctrl+Enter` sends, Esc closes and keeps the draft,
  shortcuts never fire while typing. Not built (out of scope): signatures, undo send, scheduled
  send, send-as aliases, AI drafts.
- 2026-11-02 (Phase 6): **AI settings and capability.** New dependency: the official **`anthropic`**
  SDK (2.3.0, built on httpx2 like the rest). Settings: `ANTHROPIC_API_KEY` (empty = AI off),
  `AI_LABEL_MODEL` (default `claude-haiku-5-5`) and `AI_MODEL` (default `claude-sonnet-5-5`, for
  Phases 7–8, unused yet); IDs confirmed with the claude-api skill. The browser learns whether AI
  is on from `GET /api/config` (`{"ai": bool}`); with it off the UI shows no AI control at all, the
  worker doesn't start the classifier, the sync queues nothing and `POST /api/ai/label-inbox`
  answers 409.
- 2026-11-02 (Phase 6): **Schema** (migration 0006). `labels` gets kanso-local `description`,
  `ai_managed`, `is_bundle` (changing only these makes no Gmail call); `threads.labels_manual` is
  the "labeled by hand" marker; `ai_queue` (thread, optional `run_id` for "Label existing inbox",
  status pending/labeled/unlabeled/skipped/failed, the model's label and confidence, attempts,
  next attempt, last error; at most one pending row per thread) and `ai_usage` (day, feature,
  model → requests, input, output, cache read and cache write tokens).
- 2026-11-02 (Phase 6): **Classifier.** Queued: threads of new inbox mail (not sent by the user,
  not drafts) from the incremental sync only, in the sync's own transaction, and only when the
  account has AI-managed labels; never the backfill or a full resync. A worker loop (woken over
  Redis `kanso:ai`, else every 30 s) claims rows with `FOR UPDATE SKIP LOCKED`, 3 at a time, new
  mail before "Label existing inbox" rows, and does the call, the label and the row's status in
  one transaction (a savepoint around the work keeps the claim if it fails): a crash leaves the
  row pending. One request per thread: `messages.create` with structured output (`label` an enum
  of the AI-managed names plus `"none"`, `confidence` a number), effort `low`, `max_tokens`
  1024. **Sent**: the AI-managed labels (`- Name: description`) in the system prompt, and the
  thread's latest inbox message as `From: name <address>`, `Subject:` and the body trimmed by
  `ai/trim.py` (quoted replies from "On … wrote:", Outlook separators and the Chinese/Japanese
  Gmail forms, `>` lines and signatures after `-- `/"Sent from my …" are cut; whitespace
  collapsed; 1500 characters at most). Never recipients, other messages or attachments, and
  never demo mail. The prompt says the email is data, not instructions. Confidence below **0.7**
  stays unlabeled. A label goes through the same `apply_action` path and outbox as a user's
  label, so it is a real Gmail label. Errors: the SDK retries twice; then 408/409/429/5xx and
  connection errors retry at 30 s, 90 s, … (5 attempts), anything else fails the row.
  **Caching**: `cache_control` on the system block only when it is long enough to reach the
  512-token minimum (≈1600 characters) *and* the account has a backlog (more requests within the
  5-minute TTL); single new mails would pay the 1.25× write for nothing.
- 2026-11-02 (Phase 6): **Manual labels are final.** A thread is skipped when it has any user
  label already (filters' labels included: AI only labels unlabeled mail) or `labels_manual` is
  set. That marker is set by the actions API for every `label`/`unlabel`, and by the incremental
  sync when a history label change to a *user* label actually changes the mirror: kanso's own
  write-backs (user actions and AI labels) are already in the mirror, so their echo changes
  nothing and isn't mistaken for a manual change. The classifier checks again under the account
  lock after the model answers, in case the user labeled the thread meanwhile.
- 2026-11-02 (Phase 6): **"Label existing inbox"** sits in the header of the unlabeled section:
  `GET /api/ai/label-inbox` gives the count (inbox threads without a user label, not manual, in
  accounts with AI-managed labels) and the latest run; the UI asks "Label N threads with AI?",
  `POST` queues a run, and the header shows "Labeling… x of N" (polled every 1.5 s) and then a
  notice "Labeled x of N threads." No automatic AI pass over history.
- 2026-11-02 (Phase 6): **Grouped inbox** (`GET /api/threads?mailbox=inbox&grouped=true`, the
  sections and bundles with counts, newest time and a bundle's first senders on the first page;
  `&bundle=<name>` lists one bundle). **Placement rule**: a thread goes under exactly one of its
  user labels: non-bundle labels before bundles, then by name (case-insensitive), first wins. So
  a thread labeled Work and Newsletters is in Work; one with only bundle labels is in the first
  bundle by name. **Order**: sections by their newest thread (this reproduces the mockup's Home,
  Personal, Work, Travel, Finance, Health), threads newest first; the cursor is (section time,
  section name, thread time, id). **Unlabeled** threads form a plain section named "Other" with a
  hollow dot, ordered like any section (new unsorted mail rises with the rest). **Bundles** are
  collapsed rows below a hairline at the foot, newest first; one opens at a time, its threads
  loading below it. Sections need AI: **without a key** the inbox is today's date-ordered list
  (with label chips), but bundles still collapse, since they aren't AI. Per-row label chips are
  hidden in the grouped inbox only. **Done all** sends the section (`{name, bundle, account_id,
  until}`) instead of thread ids, so threads not loaded yet are included, and `until` (the
  section's newest time as shown) leaves mail that arrived afterwards alone; the loaded threads
  leave at once, the counts follow, and Undo/`z` works as for Done. `j`/`k` go through every
  section and an open bundle; headers are not cursor stops. Main JS 388 KB / 122 KB gz → 396 KB /
  125 KB gz.
- 2026-11-02 (Phase 6): **Usage**: every response's tokens go into `ai_usage`;
  `GET /api/ai/usage` sums the last 30 days per feature and model with an estimated cost from list
  prices in `ai/usage.py` (Haiku 5.5 $0.10/$0.50, cache read $0.01, write $0.125 per MTok; Sonnet
  5.5 $2/$10/$0.20/$2.50; Opus 5.5 $4/$20/$0.20/$5). Shown as one line in the account menu.
  **Demo**: the seed marks every demo label AI-managed with a description, makes Newsletters,
  Receipts and Notifications bundles, leaves two inbox threads unlabeled and records simulated
  usage (DEMO DATA, no request made); demo threads are never sent to Claude (the classifier skips
  them), like the demo account makes no Gmail calls.
- 2026-11-02 (Phase 7): **Summaries** (`ai/summaries.py`, migration 0007). Table `summaries`:
  one row per thread (`thread_id` PK, cascades with the thread) holding `message_id`, the Gmail id
  of the thread's latest message **with drafts left out** (autosave replaces drafts all the
  time), the text, the model and `created_at`. A row is current while that id is still the latest
  message; a new reply makes it stale and the next open replaces it. `GET
  /api/threads/{id}/summary` gives `{messages, long, lang, summary}` and never calls Claude;
  `POST` streams Server-Sent Events (`delta` text, then `done` with the stored summary, or
  `error`) and answers a current summary at once without a call. Both answer 409 with AI off.
- 2026-11-02 (Phase 7): **Threshold**: summarized on open when the thread has **4 or more
  messages** or **4000 characters of trimmed text** (about two minutes of reading), a Chinese,
  Japanese or Korean character counting as three; shorter threads get a "Summarize" button.
  Trimmed text, so a long quote under a short reply doesn't count. No background summarizing.
- 2026-11-02 (Phase 7): **Sent**: the thread subject, and per message `From:` (name and address,
  or `me` for the account's own address, so the summary can say "you"), `Date:` (UTC, with the
  weekday, so "by Friday" resolves) and the body trimmed by `ai/trim.py`, at most 2000 characters
  each and 24000 in all, filled newest first (older ones are left out with a note). Never
  recipients, attachments, drafts or demo mail. The system prompt asks for one to three plain
  sentences (about 60 words, 120 CJK characters): who wants what, by when, what was decided and
  what is open. **Language rule**: the language most of the thread is written in; for a mixed
  thread, the latest message's language. The card carries the thread's `lang`, so Chinese and
  Japanese summaries get the right fonts.
- 2026-11-02 (Phase 7): **Model and latency.** New optional setting **`AI_SUMMARY_MODEL`** (empty
  = `AI_MODEL`, `claude-sonnet-5-5`), so summaries can move to `claude-haiku-5-5` if they're too
  slow without also downgrading the Phase 8 assistant. Request: adaptive thinking (the model's
  default) at effort **`low`**, `max_tokens` 2048, streamed; client timeout 30 s with one retry. No
  prompt caching (one request per thread version). Not using the API's server-side refusal
  `fallbacks`: the model is configurable (Haiku has none) and a refusal just shows a quiet
  message. Each summary logs its timing in the app log (`summary of thread N (k messages, model):
  first text after X s, done after Y s`). With the mocked API, kanso's own overhead is about
  0.01–0.03 s; in the browser the demo stream showed first text 0.42 s after the click (0.4 s of
  it the deliberate delay below).
- 2026-11-02 (Phase 7): **Made once.** A Redis lock per thread and latest message (`SET NX`,
  120 s) guards the call; a second request meanwhile polls for the stored row (every 0.25 s)
  instead of calling again, and takes over if the first one fails. The call runs in its own task,
  so a reader who moves on before it ends still gets the stored summary (no wasted tokens). Usage
  goes into `ai_usage` under feature `summary`, also for a refusal (it is billed); a failed call
  stores nothing and the lock goes, so "Try again" calls again.
- 2026-11-02 (Phase 7): **UI** (`features/assistant/`). With AI on and a thread open, the
  assistant docks at the foot of the email pane (`--assistant` background, hairline top border):
  a header (Assistant, the "this thread" chip, minimize/restore) and the Summary card ("Summary"
  in the accent color) as in the mockup. Until Phase 8 adds the chat, the panel is only as tall as
  its content (at most 45% of the pane), so a short thread costs one header line with a
  "Summarize" button; the email above keeps its own scroll. A long thread is summarized 0.4 s
  after it opens (j/k stepping through threads doesn't start one each) and **not while the panel
  is minimized** (nobody would see it; restoring starts it). Minimized is remembered per browser
  (`localStorage`, wrapped in try/catch). Failures show inline with "Try again", never blocking the
  email. Hidden with AI off, while composing and for draft-only threads. **Demo**: two long demo
  threads (an English and a Chinese one) with canned summaries in `demo_seed.SUMMARIES`, a plain
  stand-in for the others, streamed in small chunks with no request and no usage. Main JS 396 KB /
  125 KB gz → 400 KB / 126 KB gz.
- 2026-11-02 (Phase 8): **Assistant loop** (`ai/assistant.py`). A **manual agentic loop**, not the
  SDK's Tool Runner (beta): a proposed change pauses the turn across HTTP requests (the user's
  Confirm can come after a reload), which the runner doesn't model. Each question streams
  `messages.stream` with the seven tools, then runs the read tools and sends the results back, at
  most **8 requests** per question (then a quiet "Stopped after 8 steps", and the last calls are
  answered "not run" so the history stays valid). Model `AI_MODEL` (`claude-sonnet-5-5`), adaptive
  thinking (the default) at effort **`medium`** (multistep tool use; `low` is the fallback if it
  feels slow), `max_tokens` 8192, client timeout 60 s with 2 retries. **History is append-only and
  stored exactly as sent/received**, thinking blocks included (they must go back unchanged): the
  date, the user's time zone and the open thread go in a `<context>` block at the start of each
  user message, never in the system prompt. **Caching**: a breakpoint on the system prompt (it
  also covers the tools, which render first: about 1.3K tokens, above Sonnet 5.5’s 512 minimum) plus
  the top-level automatic breakpoint, which caches the conversation so far for the next step and
  turn. Not used: server-side refusal `fallbacks` (as in Phase 7: the model is configurable and a
  refusal just shows "Claude declined to answer that."), `eager_input_streaming` and `strict`
  tools (inputs are a few ids and words; the server validates every input with pydantic and
  returns an error result the model can fix). Usage goes under feature `assistant`, refusals
  included. The loop runs in its own task, so an answer whose reader left is still stored; a
  Redis lock (300 s) allows one run per conversation (409 otherwise).
- 2026-11-02 (Phase 8): **Tools and what is sent** (`ai/tools.py`), all on the local mirror.
  `search_threads(query, inbox_only, limit ≤ 20, default 10)` reuses `db/search.py` (operators,
  CJK) over every account and the whole history (Trash/Spam only with `label:`), newest first,
  and returns per thread: id, subject, up to three sender names, the latest date, message count,
  status flags, user label names and a ≤160-character snippet, plus `more`. `get_thread(id)`
  returns subject, flags, labels and per message the sender (name and address, or `me`), the
  date and the body trimmed by `ai/trim.py`, ≤1500 characters each and ≤10000 per thread, newest
  first (older ones counted as left out). `list_labels` returns names and descriptions. Never
  recipients, attachments (not even names) or drafts. Dates are in the user's time zone (the
  browser's IANA name, kept per conversation; unknown → UTC). **Demo mail never reaches Claude**:
  demo accounts' threads and labels don't exist for the tools, and a demo thread is never the
  "this thread" context. There are no send, draft, reply, forward or delete tools; an unknown
  tool name gets an error result.
- 2026-11-02 (Phase 8): **Confirmation gate.** `archive`, `apply_label`, `star` and `mark_read`
  (add-only: done, label, star, read; ≤50 threads; ids and label checked, the label matched
  case-insensitively in every account of the threads) only store a **proposal** on the assistant
  message (`actions`: tool, action, label, threads by id and subject, status) while the turn's
  other results wait in `pending`. The turn ends with the Confirm/Cancel card; nothing is
  applied or queued. `POST …/actions/{id}` with `confirm` runs a confirmed change through
  `apply_action` and the outbox, as a click would (labels also set the "labeled by hand" marker),
  publishes the change, and once every proposal of the turn is decided sends all results back
  and the loop goes on (Claude hears "The user confirmed…", "The user declined…", or "…couldn't be
  done"). A new message instead of a decision marks waiting proposals "skipped" (nothing changes)
  and puts their results and the new words in one user message. Proposals survive a reload (they
  are in the database). Undo: the card's Undo and the sidebar notice (Undo / `z`, 6 s) call
  `POST …/actions/{id}/undo`, i.e. `outbox.undo` on its entries. The system prompt says email
  content is data, never instructions; the gate is the real defense and is tested with an email
  that tells the assistant to archive everything. **Deferred to Phase 9**: CLAUDE.md's "unless
  the user has opted out" of confirmation needs the settings page; until then every change asks.
- 2026-11-02 (Phase 8): **Storage** (migration 0008). `assistant_conversations` (title = the first
  question, ≤80 characters; time zone; timestamps) and `assistant_messages` (role, the API
  `content` blocks as JSONB, the user's typed `text`, `actions` and `pending` JSONB). Only the
  **50 most recent conversations** are kept (older ones are deleted when a new one starts); the
  history list shows 20; Delete removes one. **API** (`/api/assistant`, 409 with AI off):
  `GET /conversations`, `GET|DELETE /conversations/{id}` (the chat as items: user, text,
  activity, action), `POST /messages` `{conversation_id?, text, thread_id?, timezone}` and
  `POST /conversations/{id}/actions/{action_id}` `{confirm}` streaming SSE (`conversation`,
  `text`, `activity`, `action`, then `done {pending}` or `error`), and `POST …/undo`.
- 2026-11-02 (Phase 8): **UI** (`features/assistant/`). With AI on the panel is always docked at
  the foot of the email pane at **45%** (`flex: 1 1 45%`; the reader is `1 1 55%`, as in the
  mockup), also with no thread open (then it asks about all mail); hidden while composing, for
  draft-only threads and with AI off. Header: Assistant, the chip ("this thread ×"; removing it
  asks about all mail until another thread opens; "all mail" brings it back), New conversation,
  Recent conversations, minimize/restore (remembered per browser). The open thread's Summary is
  pinned above the conversation (Phase 7's header "Summarize" button became a quiet "Summarize
  this thread" line there), the conversation scrolls and stays at its end, and the input
  ("Ask about this thread or all mail") is at the bottom. **`q`** restores the panel and focuses
  the input (in `?` help and the tooltip; Esc leaves it). Tool calls show as muted lines
  ("Searched “lisbon booking” · 1 thread", "Read “…”"). The panel is no longer keyed by thread,
  so a conversation carries across threads. **Thread links**: the model writes
  `[subject](thread:ID)`; answers render through a ~70-line Markdown subset (paragraphs, `-`/`1.`
  lists, `**bold**`, thread links only; everything else is escaped text), no library. A link
  still being written shows as plain text until it is complete. Main JS 400 KB / 126 KB gz →
  411 KB / 129 KB gz.
- 2026-11-02 (Phase 9): **PWA.** New frontend dependencies: **vite-plugin-pwa** 2.0.0 (baseline;
  it supports Vite 8) with its peers **workbox-build** 7.4.1 (dev) and **workbox-window** 7.4.1
  (bundled as a 5.7 KB chunk). `generateSW`; the **precache is the app shell only**: `index.html`,
  JS, CSS, the email frame's CSS, icons, manifest and the three Latin woff2 files the first paint
  uses (18 entries, ~574 KiB). Not precached: the deferred font CSS (~1 MB) and the CJK/other
  font subsets; they go into a **bounded runtime cache** (`kanso-fonts`, CacheFirst, at most 200
  entries, 60 days; the files are content-hashed). **Nothing under `/api` is cached**: there is no
  runtime route for it, and the navigation fallback (`index.html` for SPA URLs) skips `/api/`,
  so "Connect account" and the OAuth callback always reach the server. Checked in Chromium: the
  caches hold only those files, the shell starts offline, and the API failing shows "Can’t reach
  kanso… it tries again when the connection is back" (it retries on the `online` event or "Try
  again"). **Updates**: `registerType: 'prompt'`; a new service worker waits, the sidebar (or on a
  phone a bar at the foot) offers "Reload", and the app checks for a new version hourly while open.
  The server now sends `Cache-Control: no-cache` for everything outside the hashed `assets/`
  (`index.html`, `sw.js`, the manifest, icons), and `.webmanifest` is registered as
  `application/manifest+json`. **Icons**: an ink ensō on paper closed by an indigo dot
  (`frontend/icons/*.svg`), rasterized by `frontend/icons/build.sh` with headless Chromium
  (ImageMagick's own SVG renderer drops the stroke): 192/512, maskable 512, apple-touch 180,
  `favicon.svg`. Main JS 411 KB / 129 KB gz → 427 KB / 134 KB gz.
- 2026-11-02 (Phase 9): **Phone layout** below 768px (`@media (max-width: 767px)` in the CSS
  modules, `usePhone()` in `app/media.ts`); desktop is unchanged (a pixel diff of the Phase 8
  inbox screenshot differs only in two timestamps). One screen at a time: the list; a thread (or a
  new message) covers it while the list stays mounted underneath, keeping its scroll; Settings.
  The sidebar is a drawer from a menu button in the list header (closes on any navigation,
  including Back); Compose is a button at the foot of the list. Rows are two lines and 64px
  (sender and time, then subject and snippet); section headers 44px, bundles 48px; every tap
  target is at least 44px (toolbar, drawer rows, composer, settings, label editor). The thread
  toolbar gets Back; Forward moves into More, Reply is icon-only. A new message is full screen
  with its own Close. The assistant is a **bottom sheet** under the thread: closed (one bar) each
  time a thread opens, 72% of the screen when open; it shrinks the email rather than covering it.
  Notices (Undo) float at the foot, clear of Compose and the sheet. Inputs are 16px on phones
  (a deviation from the 13px UI token), or mobile browsers zoom in on focus.
- 2026-11-02 (Phase 9): **Swipe** (`features/threads/swipe.ts`, pointer events, no library): a
  touch that moves sideways 10px before it moves vertically drags the row (`touch-action: pan-y`
  leaves scrolling to the browser); released past 35% of the width (at most 120px) it slides out
  and runs the row's quick action: right = Done (or Move to inbox in Done), left = Delete (or
  Restore in Trash), with the usual Undo. The click that ends a swipe is swallowed. Mice are
  unaffected; hover quick actions now apply only under `(hover: hover)`, so they no longer stick
  on touch screens (the sidebar label ⋯ too: on touch, labels are edited in Settings).
- 2026-11-02 (Phase 9): **Settings page** (`/settings`, from the account menu, in place of the
  list and email pane; the mail shortcuts are off there): Accounts (name, address and sync state
  per account; Edit renames, recolors or disconnects; Connect), Labels (color, AI-managed and
  bundle flags, descriptions; Edit/New reuse the sidebar's label editor), Assistant (the
  confirmation opt-out, AI on only), AI usage (30 days per feature and model, AI on only),
  Password. **Account management moved out of the switcher**, which now lists the accounts, then
  Settings and Log out; with no account connected the empty inbox links to Settings.
- 2026-11-02 (Phase 9): **Changing the password** (`POST /api/auth/password {current, new}`).
  `PASSWORD_HASH` in `.env` stays the starting value; a changed password's argon2 hash goes in a
  new key/value **`settings` table** (migration 0009, `kanso.db.prefs`). The app keeps the current
  hash in memory (one process), read from the database once. The current password is required and
  counts against the login rate limit; a wrong one answers 400, not 401, so the session isn't
  treated as gone. Every session is signed out (the hash is part of the cookie signature), and
  this browser gets a new cookie. **Reset**: `docker compose exec app python -m
  kanso.reset_password` deletes the stored hash; `docker compose restart app` makes the app read
  `.env` again (documented in SETUP.md, "Your password").
- 2026-11-02 (Phase 9): **Assistant confirmation opt-out** (Settings → Assistant, stored as
  `assistant_confirm` in `settings`, default on; `GET/PATCH /api/settings`). Off: a change tool
  still goes through `tools.propose` (the same checks), is applied at once through the actions
  path and outbox (labels marked manual, browsers told), stored on the turn with `auto: true` and
  status `confirmed`, and Claude hears "Done without asking (confirmation is off)…"; the chat
  shows the card ("Done without asking." with Undo) and the sidebar notice offers Undo. Each user
  message's `<context>` says confirmation is off, so the system prompt and tools stay the same
  (and cached). On: every change pauses as before.
- 2026-11-02 (Phase 9): **Hardening** (`kanso/core/hardening.py`, a pure ASGI middleware, so SSE
  isn't buffered). Every response: `Content-Security-Policy` (`default-src 'self'; script-src
  'self'`, no inline scripts, `object-src 'none'`, `frame-ancestors 'none'`, `base-uri 'self'`,
  `form-action 'self'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, a
  `Permissions-Policy`, and HSTS when `PUBLIC_URL` is https. The email frame inherits the CSP, so
  `img-src` allows `https: http: data:` ("Show images" checked in Chromium: the remote image loads,
  no app-CSP violation) and `style-src` allows `'unsafe-inline'` (email HTML needs it; no script
  can run). **CSRF**: every non-GET/HEAD/OPTIONS `/api` request needs `X-Requested-With: kanso`
  (403 otherwise), which the frontend sends on every request (the SSE POST streams and the
  attachment upload included). A custom header can't be sent cross-site without a CORS preflight,
  which kanso never grants; simpler than tokens, on top of SameSite=Lax. **API docs off**:
  `/docs`, `/redoc` and `/openapi.json` are gone (they fall through to the SPA).
- 2026-11-02 (Phase 9): **Security review** of auth, sessions, OAuth, token encryption and uploads.
  Fixed: the login rate limit now counts an attempt *before* the slow argon2 check (parallel
  guesses could pass the 5-attempt limit); a session cookie with non-ASCII characters raised a
  500 in `hmac.compare_digest` instead of a 401; removing a draft attachment unlinked a path built
  from the URL's id even when the draft had no such attachment (no slash is possible, so no
  escape, but now nothing is touched). Accepted, documented: the session is a stateless signed
  cookie (30 days), so logging out clears it in that browser only; changing the password signs
  out everywhere. No Host-header allowlist (the session cookie isn't sent to other hosts, and
  login is rate-limited). Postgres and Redis have no published ports; Redis has no password on the
  compose network. **Dependency audit**: `pip-audit` on the exported lock (runtime and dev): no
  known vulnerabilities. `bun audit`: 3 in build-time tools only (glob and minimatch via
  workbox-build, ESLint and typescript-eslint; deepmerge via workbox-build), moderate/low, fixed
  only by major upgrades upstream; nothing that ships to the browser or the image's runtime.
- 2026-11-02 (Phase 9): **Ops.** The compose file keeps **building the image locally** (`image:
  kanso` + `build: .`): kanso has no registry. Upgrade path: `git pull`, `docker compose pull
  --ignore-buildable` (Postgres/Redis patch releases; plain `pull` errors on the local-only
  `kanso` image), `docker compose up -d --build`; migrations run as `app` starts. Verified: a
  Phase 8 install with the demo seed, rebuilt to Phase 9, ran 0008 → 0009 on the existing volume
  with its data intact. Backup/restore (`pg_dump -Fc` / `pg_restore --clean --if-exists` through
  `docker compose exec -T postgres`, app and worker stopped for the restore) verified the same way.

- 2026-11-02 (user-approved follow-ups): **Trash** is in the sidebar (desktop and the phone
  drawer) as a quiet, muted entry below Drafts. Spam stays reachable by URL only (`/spam`).
- 2026-11-02 (user-approved follow-ups): **"me" and recipients in rows**, as in Gmail. The thread
  list API adds `me` (the names in `senders` sent from any connected account's address, which the
  row shows as "me", e.g. "Daniel Reyes, me") and, in Sent and Drafts only, `recipients` (the To
  and Cc names of the user's own messages in the thread, leaving the user's addresses out), which
  the row shows as "To: Daniel Reyes" ("(no recipients)" for an empty draft). Both are worked out
  when the list is read (one extra query per page, by thread id), not stored on `threads`, so
  connecting another account changes them at once. Everywhere else (the unified view, one
  account, search, the grouped inbox and bundles) rows show senders with "me".
- 2026-11-02 (user-approved follow-ups): **`from:` / `to:` match the start of a word**, as in
  Gmail: of a word in the name, or of the address or any part of it (`from:reyes` finds
  `daniel.reyes@example.com`, `from:example.cn` the domain). Postgres `~*` with `\m` (start of a
  word) on the same text as before, served by the `pg_trgm` index; a value that doesn't start
  with a letter or digit (`from:@example.jp`) isn't anchored. A value with CJK characters keeps
  substring matching (no word breaks in CJK names). `to:` now searches the To and Cc **names and
  addresses only** (`jsonb_path_query_array(to_addrs || cc_addrs, '$[*].*')::text`; before, the
  JSON text, so `to:name` matched every message) and gets its own trigram index on that
  expression (migration 0010); `EXPLAIN` shows both indexes used.
- 2026-11-02 (user-approved follow-ups): **"Other" is pinned first** in the grouped inbox; the
  label sections follow by their newest thread and bundles stay at the foot. The grouped cursor
  gained a leading "unlabeled" flag (older cursors answer 400; they only live in an open page).
- 2026-11-02 (user-approved follow-ups): **Rejected API key.** When Anthropic answers 401
  (authentication) or 403 (permission) to labeling, a summary or the assistant, a
  `ai_key_rejected` row goes in the `settings` table (status, time and a 12-character SHA-256
  prefix of the key, so a notice about an old key doesn't outlive it once `.env` has a new one).
  Every response that comes back clears it (`usage.record`, which every feature calls). Shown as
  one muted line in the account menu, above Settings (the menu has no AI usage line since
  Phase 9), and in Settings → AI usage: "Anthropic API key was rejected; check
  ANTHROPIC_API_KEY" (`key_rejected` on `GET /api/ai/usage`); the summary card and the assistant
  say the same instead of "Claude didn't answer". **Labeling backs off**: a rejected key fails no
  row and counts no attempt; the queue pauses for 10 minutes in the worker's memory (one try per
  pause), and a restart, which a new key needs anyway, tries again at once. Verified in the
  browser with a dummy key against a throwaway endpoint answering 401 (`ANTHROPIC_BASE_URL`).
- 2026-11-02 (user-approved follow-ups): **Log out everywhere** (Settings → Sessions,
  `POST /api/auth/logout-everywhere`, CSRF header required like every change). A session
  generation (`session_epoch` in `settings`, kept in memory like the password hash) is part of
  the signed cookie message, next to the password hash, so adding one makes every cookie invalid.
  Generation 0 signs exactly what kanso signed before, so upgrading signs nobody out. **This
  browser is signed out too** (not re-issued), since the button says "everywhere": the login
  screen follows. Changing the password still re-issues this browser's cookie as before.
- 2026-11-02 (first real-account run): Gmail's quota is **6,000 units per user per minute**, and
  `messages.get` costs **60 units** with `format=raw` (20 with `minimal`), not the 5 units the
  Phase 2 code assumed. The unpaced backfill hit `rateLimitExceeded` constantly and, because a
  page was saved only when every fetch in it succeeded, saved nothing. Fix: the Gmail client paces
  each account's requests by their documented cost at 90 units a second (`gmail/client.py`,
  `COSTS`), a rate limit pauses all of that account's requests, and the backfill commits every 100
  messages. A full backfill therefore runs at ~90 messages a minute per account (~3 h for 16k
  messages); `format=full` costs the same 60, so switching formats wouldn't help.
- 2026-11-02: The user picked logo direction **A · Brush ensō** (design canvas, artboard "6 · Logo
  directions"): one ink brush stroke that swells and trails off, on paper, no accent color. It
  replaces the Phase 9 placeholder in `frontend/icons/{icon,maskable}.svg`; `sh icons/build.sh`
  regenerates `public/` (favicon, PWA and Apple touch icons).
- 2026-11-02: **Demo instance** (user request): `./demo.sh [anthropic-api-key]` or
  `docker compose -f docker-compose.demo.yml up`, a separate Compose project (`kanso-demo`, image
  `kanso-demo`, port `DEMO_PORT`, default 8001) with the same four services. `DEMO_MODE=true`:
  the secret key is public and the password is `demo` (the user's choice: shown and filled in on
  the login page through the public `GET /api/auth/demo`; its argon2 hash has a fixed salt, so a
  restart doesn't sign visitors out); "Connect account" (403) and changing the password (403) are
  off, and Settings hides both. AI only with a key given to the demo itself
  (`DEMO_ANTHROPIC_API_KEY`, never a production `.env` or an exported `ANTHROPIC_API_KEY`); then
  demo mail **is** sent to Claude (labeling, summaries and the assistant's tools treat it as real
  mail) and the seed records no simulated usage. **Reset**: `demo_seed.reset()` truncates every
  table (ids restart, so open browsers find the same threads under the same ids) and seeds; the
  app runs it before starting and the worker every day at midnight UTC. It refuses to run on a
  database holding a non-demo account. Triage, labels, drafts and send already worked locally for
  demo accounts (outbox, drafts).
- 2026-11-02: **UI feedback round** (user request): subtle motion across the app (tokens and rules
  in `CLAUDE.md` → Motion; CSS transitions and `@starting-style`, no animation library); the
  email pane is hidden until something is open, so the list takes the full width (the assistant
  opens alone with `q` or a list-header button); a label's "⋯" is a real "more actions" menu
  (`components/Menu.tsx`), with editing and deleting in a modal dialog (`components/Dialog.tsx`,
  a native `<dialog>`) instead of inline in the sidebar; line icons for the mailboxes and in the
  account menu; and a **dark theme** that follows the system or a per-browser pick in the account
  menu, applied before the first paint by `public/theme.js` (a file, since the CSP allows no
  inline script). HTML emails keep the light tokens on a light sheet in the dark theme.
- 2026-11-02: **Second UI feedback round** (user request): the list/email divider is draggable
  (`components/Resizer.tsx`; width in localStorage `kanso.listWidth`); a new message opens in a
  floating window (minimize, close) instead of the email pane, so mail stays readable while
  writing; this replaces Phase 5's "docked compose panel" for new messages (replies and forwards
  stay docked in the thread, and a thread of drafts only still opens in the pane). Clicking the
  open thread's row, or the toolbar's new ×, closes it. The label edit dialog has no Delete (the
  label's menu has it; Settings keeps its own).
