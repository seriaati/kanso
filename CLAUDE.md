# kanso (簡素)

簡素 means *simplicity*. It is the product's name and its main design rule.

kanso is a self-hosted, open source (AGPL-3.0) email client for reaching **inbox zero**. It is
inspired by Shortwave but drops the parts we don't need, and keeps **your full email history with
no 90-day limit and no paid tier**.

It is a personal project for personal use, not a SaaS product. Optimize for one person running it
on their own machine or server with `docker compose up`.

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
| Sync model | **Two-way sync.** Gmail is the source of truth. kanso mirrors *all* of it in Postgres and writes archive, label and read-state changes back to Gmail, so other Gmail clients stay consistent. |
| Accounts | **One user, multiple Gmail accounts**, each independent: one account is in view at a time (its mail, labels and assistant conversations), with no combined view. |
| AI provider | **Anthropic (Claude)** with the user's own API key. The model is configurable via env. |
| Search | **Postgres full-text search.** No vector search in v1. |
| Clients | **Web app plus an installable PWA**: desktop-first, also usable on a phone. No native apps. |

## v1 scope

**Inbox and organization**
- Read, compose, reply and forward (basic email-client functionality).
- **Done/archive flow**: mark a thread done to archive it, with undo. Inbox zero is the goal state
  and the UI should celebrate it quietly.
- **Bundles**: group the inbox by label (e.g. Newsletters, Receipts) so a whole group can be
  cleared at once.
- **Keyboard-first**: full keyboard navigation (j/k, e, etc.) and a command palette.

**AI**
- **Auto-labeling**: Claude classifies incoming mail into the user's labels, each defined by a
  natural-language description. Labels are real Gmail labels and sync both ways.
- **Assistant chat**: a side panel where Claude can search, read, summarize and act on email
  through tools that run against the local mirror.
- **Thread summaries**: short summaries of long threads.

## Explicitly out of scope (until the user decides otherwise)

Snooze and reminders · AI-drafted replies · semantic/vector search · IMAP or non-Gmail providers ·
multi-user or teams (shared inboxes, comments, delegation) · calendar integration · todos ·
scheduled send · read receipts or tracking pixels · native desktop or mobile apps · billing, plans
or usage limits.

Don't add these "while you're at it". Propose them to the user first.

## UI principles

kanso is a tool for working through email, not a dashboard. **Function comes before looks.**

- **Email comes first.** The message list is the main surface. Show many threads at once so they
  can be skimmed, and keep rows compact and scannable: sender, subject, snippet, labels, time.
- **No decorative real estate.** No hero numbers, greeting banners, oversized stats or tiles that
  waste space. Every pixel should help you read or triage mail.
- **Key controls always in view, always in the same place.** These are the account switcher,
  Compose, search and the label/bundle navigation.
- **Clear information hierarchy.** Labeled and bundled mail is organized so you can tell where
  things are at a glance.
- **The assistant docks beside the mail and never covers it.** It is a panel, not a modal or
  overlay.
- **The look is zen, paper and calm**: warm paper tones, ink-like text, restrained accents and
  quiet typography. The look serves the layout, never the other way around.

## Design system (chosen 2026-11-01)

The reference mockup is the "3 · Chosen — Koke + indigo, split view" artboard on the design canvas
(https://claude.ai/artifact/V1JQgMWnzg11fKx9W61eUb). New screens and components follow it.

**Layout: split view**
- **Sidebar** (~200px): the account switcher at the top (one account at a time; the last used per browser), then
  Compose (the primary button), mailboxes with line icons (Inbox, Starred, Done, Sent, Drafts,
  All mail, then Spam and Trash in muted text), labels with color dots, and "+ New label". A
  label's "⋯" opens a small "more actions" menu (Edit, Delete); editing, creating and deleting
  labels happen in a small centered dialog, never inline.
- **Thread list** (left; half the space beside the sidebar by default, **the full width while nothing is open**): search at the top. Threads are grouped into sections by AI label
  (Home, Personal, Work…), each with a count and "Done all". Bundles (Newsletters, Receipts,
  Notifications) are collapsed rows at the bottom. Rows are compact single lines (40px by default;
  ~31px with the account menu's Density set to Compact, per browser): unread dot, sender,
  subject — snippet, star, time. **On hover, the time is replaced by quick-action
  icons**: Done, Delete, Star, Important. A right-click on a row opens its actions menu (Done,
  Delete, Star, Important, Mark unread, Label…, Select, each with its shortcut); a click
  outside only closes it. While the email pane is open, an empty search box folds to its icon
  and unfolds when clicked or on `/`.
  Ctrl/⌘-click checks a row (with the open thread, if any), Shift-click checks a range, and `x`
  checks the cursor's row; a click on a checked row's checkmark unchecks it. While rows are
  checked, a bar floats at the foot of the list with the count and the batch actions (Done,
  Delete, Mark read/unread, Star, Important, Label, clear); the email pane is unaffected.
- **Email pane** (right): shown only with something in it (a thread or the assistant); it slides
  in as it opens and the list narrows to make room. The divider between list and pane can be
  dragged (arrow keys too; double-click resets); the list's width is remembered per browser. A
  thread closes with Esc, the toolbar's × at the far right, or a click on its row again. An
  **icon toolbar** across the top with Done (archive), Delete | Label,
  Important, Star, and then Reply, Forward and More on the right. Toggle icons show their state
  (filled in the accent color when on). Each tooltip names its keyboard shortcut.
- **Assistant**: docked at the bottom of the email pane (~45% of its height). It is aware of the
  open thread ("this thread" chip), can also search all mail, and can be minimized. With no
  thread open, `q` (or the list header's assistant button) opens it alone in the email pane. It
  never overlays the list or the email.
- **New message** (c): a window of its own at the bottom right, over the corner of the email
  pane, so mail can still be read and triaged while writing. It minimizes to its title bar and
  closes (keeping the draft). One at a time; c brings it back. On a phone it is the whole screen.
  Replies and forwards stay docked in their thread.

**Tokens**

| Token | Value | Use |
|---|---|---|
| paper | `#F1EFE7` | app background, thread list |
| sidebar | `#EAE7DC` | sidebar |
| surface | `#F7F5EE` | email pane, inputs, cards |
| assistant | `#ECE9DF` | assistant panel |
| line | `#DAD6C8` / `#E3E0D4` | dividers (strong / soft) |
| hover / selected | `#E8E5DA` / `#DEDACB` | row and nav states |
| ink | `#242520` | primary text, icons |
| muted | `#63655C` | secondary text (passes 4.5:1 on paper) |
| accent (indigo) | `#2F4B6E` | Compose, unread dots, active toggles, summary label, send |
| label colors | Home `#A07338`, Personal `#4F6E8C`, Work `#6B6B66`, Travel `#556B3F`, Finance `#87607E`, Health `#A35A3A`, bundles `#B8B3A2` | label dots only |

**Dark theme** (sumi ink on warm charcoal; same structure, the label colors are unchanged): paper
`#1D1E1A`, sidebar `#181915`, surface `#242520`, assistant `#20211C`, line `#3A3B34` / `#2E2F29`,
hover / selected `#2B2C26` / `#35362F`, ink `#E8E5DA`, muted `#A3A497`, accent `#93ABCD`. It
follows the system by default; the account menu's Theme row picks System, Light or Dark (stored
per browser). HTML emails that set their own colors or backgrounds keep the light tokens and sit
on a light sheet in the dark pane; HTML without its own colors follows the dark theme.

**Accent**: the account menu's Accent row picks the accent (stored per browser): Indigo (the
default), Teal `#2B5E5B`/`#86B4AF`, Moss `#4A5D2C`/`#A0B381`, Ochre `#704C20`/`#CCA979`, Rust
`#8F4127`/`#DF9C80`, Plum `#6A3F62`/`#C7A2C0` (light/dark). Every shade is at least 5:1 against
every background in its theme, including surface-colored text on the accent; a new palette must be
too.

- **Type**: Zen Maru Gothic for the UI and body text (13px UI, 14px message body, line-height
  1.6–1.75); the open email's subject is Zen Maru Gothic too, bold at 21px, like the subjects in
  the list. Shippori Mincho B1 for pane and dialog titles (18px).
- **Shape**: 8px radius on rows and buttons, 10px on inputs and cards. Hairline borders, no
  shadows, except one soft shadow under what floats over the mail: the new-message window and the
  selection bar.
- **Icons**: line icons, 1.8px stroke, 15–17px, in `currentColor`.
- **Motion**: subtle and purposeful, from the `--fast` (120ms) / `--medium` (200ms) / `--slow`
  (360ms) tokens with one soft ease. Menus, dialogs and notices fade and rise in; the email pane
  slides in; a message expanded in a thread grows open; rows glide into place when threads
  leave; toggles pop; inbox zero draws an ensō. Nothing loops or delays work, and all of it is
  off under `prefers-reduced-motion`. Only compact buttons shrink when pressed; wide, row-like
  ones (message headers, menu rows) don't.

## Architecture

| Component | Tech | Role |
|---|---|---|
| Database | PostgreSQL | Full mail mirror (accounts, threads, messages, labels), sync state, full-text index |
| Cache | Redis | Caching, and the job queue for background work |
| Backend | Python + FastAPI | REST API, Gmail OAuth, sync, AI orchestration |
| Worker | Same Python codebase, separate container | Gmail sync, write-back to Gmail, AI labeling |
| Frontend | React, built with Bun | SPA plus PWA |
| Deploy | Docker Compose | All of the above, plus a `.env` |

Guidelines:
- **Sync**: do the initial full backfill (all history) in the background, then sync incrementally
  with the Gmail History API. Incremental syncs are triggered **instantly by Gmail push
  notifications through a Google Cloud Pub/Sub *pull* subscription**, so kanso needs no public
  endpoint, with a slow safety-net poll behind it. Write-backs are queued and retried. The UI must
  work while the backfill is still running.
- **AI calls** run in the backend or worker, never in the browser. The API key stays server-side.
- **Assistant actions** that change mail (archive, label, send) need explicit user confirmation
  unless the user has opted out.
- Keep the module layout boring and flat. Don't add abstraction layers for providers we don't
  support.

## Known constraints

- Each self-hoster needs their **own Google Cloud OAuth client**. Gmail scopes are "restricted". An
  OAuth app left in *Testing* status issues refresh tokens that expire after about 7 days, so the
  setup docs must cover this (e.g. publish the app to *In production* unverified for personal use).
- Mail content sent to Claude leaves the server. Keep that limited to what each AI feature needs.

- **Auth**: a simple single-user password login. The PWA is reached from outside localhost over HTTPS.
- **AI is optional**: with no Anthropic API key, kanso works as a plain email client and the AI
  features are hidden rather than shown broken.
