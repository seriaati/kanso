# Setting up kanso

This guide takes you from the two files in the README's quick start to kanso running with your
Gmail accounts connected. It takes about 20 minutes, most of it in the Google Cloud console.

kanso talks to Gmail with **your own** Google Cloud OAuth client, so nothing goes through a third
party. Google treats Gmail access as a "restricted" scope, which is why you set up the client
yourself instead of using a shared one.

You need:

- A machine with Docker and Docker Compose.
- A Google account to own the Google Cloud project. It can be one of the Gmail accounts you want to
  connect, or another one.
- `openssl` (or any other way to make a random string).

## Quick setup with the gcloud CLI

If you have the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and have run
`gcloud auth login`, one script does steps 1, 2, 5 and 6 for you and writes `.env`. It lives in
the repository, so clone it and run kanso from the clone:

```sh
git clone https://github.com/seriaati/kanso.git && cd kanso
scripts/setup.sh            # or: scripts/setup.sh <existing-project-id>
```

Google has no command-line way to set up the consent screen or create the OAuth client (steps 3
and 4), so the script pauses there, prints direct links, and asks you to paste the client ID and
secret. It's safe to re-run: it reuses what already exists and keeps the secrets in `.env`, after
saving a backup next to it. When it finishes, continue with step 7.

If enabling Pub/Sub fails because the project has no billing account, the script tells you how to
link one, or lets you skip Pub/Sub. kanso then checks Gmail every 2 minutes instead.

The rest of this guide is the same setup done by hand in the Google Cloud console.

## 1. Create a Google Cloud project

1. Open the [Google Cloud console](https://console.cloud.google.com/) and sign in.
2. Open the project picker at the top and click **New project**. Name it `kanso` and click
   **Create**. Select the new project once it exists.
3. Note the **project ID** (shown on the dashboard, e.g. `kanso-123456`). You need it in step 5.

## 2. Enable the APIs

Go to **APIs & Services → Library** and enable these (search, open, click **Enable**):

- **Gmail API**
- **Cloud Pub/Sub API**
- **People API** (sender photos from your Google Contacts; without it rows just show no photo)

## 3. Set up the consent screen and publish it

This is the screen Google shows when you connect an account.

1. Go to **Google Auth platform → Branding**. If you see "Google Auth platform not configured yet",
   click **Get started**.
2. **App information**: app name `kanso`, support email: your address. **Next**.
3. **Audience**: choose **External** (for @gmail.com accounts). If all your accounts belong to
   one Google Workspace organization, you can pick **Internal** instead and skip the publishing
   step below. **Next**.
4. **Contact information**: your address. **Next**, agree to the User Data Policy, **Create**.
5. Go to **Data Access → Add or remove scopes**. Tick (or paste under "Manually add scopes"):
   - `openid`
   - `.../auth/userinfo.email`
   - `.../auth/userinfo.profile` (your profile photo, shown in the account switcher)
   - `.../auth/contacts.readonly` and `.../auth/contacts.other.readonly` (photos of the people
     who email you, from your contacts; kanso reads only their addresses and photos)
   - `https://www.googleapis.com/auth/gmail.modify`

   Click **Update**, then **Save**.
6. Go to **Audience**. Under **Publishing status**, click **Publish app** and confirm. The status
   changes from *Testing* to **In production**.

**Why publish?** While an app is in *Testing*, Google expires its refresh tokens 7 days after you
connect, and kanso would lose access to your mail every week. Publishing does not make kanso
public and does not need Google's review: the app stays *unverified*, it isn't listed anywhere,
only your kanso (which holds the client secret) can complete a sign-in with it, and Google shows a
warning on the consent screen (step 7). Unverified apps are limited
to 100 users over the project's lifetime, which is plenty for one person.

## 4. Create the OAuth client

1. Go to **Google Auth platform → Clients** and click **Create client**.
2. **Application type**: **Web application**. Name: `kanso`.
3. Under **Authorized redirect URIs**, add the address you open kanso at, followed by
   `/api/accounts/callback`. For the default local setup that is:

   ```
   http://localhost:8000/api/accounts/callback
   ```

   If you will reach kanso over HTTPS (step 8), add that address too, for example
   `https://kanso.example.com/api/accounts/callback`. Google only accepts `https://` addresses,
   plus `http://localhost`. It doesn't accept bare IP addresses or local names like `kanso.local`.
4. Click **Create**. Copy the **Client ID** and **Client secret** (open the client again later if
   you need them). They go in `.env` in step 6.

## 5. Set up Pub/Sub for instant new-mail notifications

Gmail tells kanso about new mail through Google Cloud Pub/Sub. kanso *pulls* these notifications,
so your server doesn't need to be reachable from the internet. Go to **Pub/Sub** in the console.

This step is optional. Without it, kanso checks Gmail for changes every 2 minutes instead of
within seconds.

1. **Create the topic.** **Topics → Create topic**. Topic ID: `kanso-gmail`. Untick **Add a
   default subscription**. Click **Create**.
2. **Let Gmail publish to it.** Open the topic, go to the **Permissions** tab (show the info panel
   if needed) and click **Add principal**:
   - New principal: `gmail-api-push@system.gserviceaccount.com`
   - Role: **Pub/Sub Publisher**

   Click **Save**. If this fails with a "domain restricted sharing" error, your organization's
   policy blocks it; an organization admin has to allow an exception for that account.
3. **Create the pull subscription.** **Subscriptions → Create subscription**. Subscription ID:
   `kanso-gmail`. Topic: `kanso-gmail`. **Delivery type: Pull**. Set **Acknowledgement
   deadline** to `60` seconds (kanso acknowledges a notification only after the sync it
   triggered has finished). Leave the rest as is and click **Create**.
4. **Create a service account for kanso.** Go to **IAM & Admin → Service accounts → Create service
   account**. Name: `kanso-pubsub`. Click **Create and continue**, skip the optional role and user
   steps, and click **Done**.
5. **Let it read the subscription.** Back in **Pub/Sub → Subscriptions**, open `kanso-gmail`, go to
   its **Permissions** and **Add principal**: the service account's email
   (`kanso-pubsub@<project-id>.iam.gserviceaccount.com`) with the role **Pub/Sub Subscriber**.
   **Save**.
6. **Create its key.** Open the service account, go to **Keys → Add key → Create new key → JSON**.
   A `.json` file downloads. If key creation is blocked by an organization policy
   (`iam.disableServiceAccountKeyCreation`), an organization admin has to lift it for this project.

The key goes in `.env` in the next step. Keep the file private, and delete it once it is in `.env`.

## 6. Configure kanso

From the kanso folder:

```sh
cp .env.example .env
```

Open `.env` and fill it in:

| Setting | Value |
|---|---|
| `POSTGRES_PASSWORD` | A long random string: `openssl rand -hex 24`. Set it before the first start. |
| `SECRET_KEY` | Another random string: `openssl rand -hex 32`. Changing it later signs you out and means reconnecting your accounts. |
| `PUBLIC_URL` | The address you open kanso at, e.g. `http://localhost:8000` (the default) or `https://kanso.example.com`. It must match a redirect URI from step 4. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | From step 4. |
| `GOOGLE_PUBSUB_TOPIC` | `projects/<project-id>/topics/kanso-gmail` |
| `GOOGLE_PUBSUB_SUBSCRIPTION` | `projects/<project-id>/subscriptions/kanso-gmail` |
| `GOOGLE_PUBSUB_CREDENTIALS` | The service account key from step 5 on one line: `base64 -w0 key.json` (macOS: `base64 -i key.json`). |
| `ANTHROPIC_API_KEY` | Optional. A Claude API key from [console.anthropic.com](https://console.anthropic.com), which turns on AI labeling and thread summaries. Without it kanso is a plain email client and shows no AI controls. The model for each AI feature is chosen in Settings → AI models. |

Then set your login password. This downloads the kanso image the first time:

```sh
docker compose run --rm --no-deps app python -m kanso.hash_password
```

It asks for the password twice and prints a line like
`PASSWORD_HASH='$argon2id$v=19$...'`. To script it instead, pass the password on stdin (note the
`-T`, which turns off the terminal so the command reads stdin):

```sh
docker compose run --rm -T --no-deps app python -m kanso.hash_password < password.txt
```

Paste the printed line into `.env`, replacing the empty
`PASSWORD_HASH=`, and keep the single quotes: without them Docker Compose mangles the `$` signs.

## 7. Start kanso and connect your accounts

```sh
docker compose up -d
```

Open `PUBLIC_URL` (e.g. http://localhost:8000) and log in with your password. After 5 wrong
passwords from one address, kanso refuses logins from it for 15 minutes.

To connect a Gmail account, click the account switcher at the top left, then **Settings**, then
**+ Connect a Gmail account**:

1. Choose the Google account.
2. Google warns **"Google hasn't verified this app"**. That's expected for your own unverified
   client (step 3). Click **Advanced → Go to kanso (unsafe)**.
3. On the permissions screen, make sure **Read, compose, send, and permanently delete all your
   email from Gmail** is ticked (Google's wording is broader than what kanso does: it never
   deletes mail permanently), and click **Continue**.

You land back in kanso with the account in the switcher, and kanso starts copying the account's
whole mail history in the background, newest mail first. Gmail limits how fast one account's
mail can be read (6,000 quota units a minute, and each full message costs 60), so kanso copies
about 90 messages a minute per account: roughly 3 hours for 16,000 messages. This happens only
once; afterwards new mail arrives within seconds. kanso is usable meanwhile, and a restart
continues where it stopped.
Repeat for each account. In **Settings**, an account's
**Edit** renames or recolors it, or disconnects it. Disconnecting removes the account and its
synced mail from kanso and revokes kanso's access in Google. Your Gmail itself is not changed.
Settings also shows each account's sync state, your labels (with their AI descriptions when AI is
on), Claude's token use, whether the assistant asks before it changes mail, and your password.

Connect accounts with kanso opened at `PUBLIC_URL` exactly: Google sends you back there, and you
have to be logged in at that address.

## 8. Reaching kanso over HTTPS

kanso doesn't handle HTTPS itself. To use it from your phone or another machine, put it behind a
reverse proxy that does, such as [Caddy](https://caddyserver.com/) or
[Tailscale](https://tailscale.com/kb/1312/serve).

- **Caddy**, on the same machine, with a domain pointing at it (`Caddyfile`):

  ```
  kanso.example.com {
      reverse_proxy localhost:8000
  }
  ```

- **Tailscale**, private to your tailnet: `tailscale serve --bg 8000` serves kanso at
  `https://<machine>.<tailnet>.ts.net`.

Then, in `.env`:

1. Set `PUBLIC_URL` to the HTTPS address. kanso then marks its login cookie `Secure` and tells
   browsers to use HTTPS only for it (HSTS).
2. Set `KANSO_PORT=127.0.0.1:8000`, so kanso only listens on the machine itself and is reached
   only through the proxy.
3. Set `FORWARDED_ALLOW_IPS=*`, so kanso trusts the proxy's `X-Forwarded-For` header and the login
   rate limit applies per client instead of to the proxy as a whole. Only do this together with
   2: otherwise anyone reaching the port directly could fake their address.

Add `<PUBLIC_URL>/api/accounts/callback` to the OAuth client's redirect URIs (step 4) and run
`docker compose up -d` again.

## 9. Install kanso on your phone

kanso is a web app you can install like an app. This needs the HTTPS address from step 8: phones
only install web apps served over HTTPS.

- **Android (Chrome)**: open `PUBLIC_URL`, log in, then **⋮ → Add to Home screen → Install**.
- **iPhone and iPad (Safari)**: open `PUBLIC_URL`, log in, then **Share → Add to Home Screen**.

On a phone, kanso shows one screen at a time: the list, then the thread you tap (the arrow at the
top left goes back). The menu button at the top left opens the mailboxes and labels. Swipe a
thread right to mark it done, or left to delete it; **Undo** shows at the bottom for a few
seconds. The assistant is the bar at the bottom of a thread: tap it to open it.

The app keeps only kanso itself on the phone, so it starts quickly. Your mail is not stored on the
phone: without a connection kanso says it can't reach the server and tries again when you are back
online. When a new version of kanso is running on the server, the app offers **Reload**.

**Notifications of new mail**: in the installed app, open **Settings → Notifications** and turn on
**New inbox mail on this device**, then allow notifications when asked. Each new unread message
in the inbox then shows its sender, subject and first line; tap it to open the thread, or use its
**Done** and **Delete** buttons to deal with it from there. Turn it on
in each phone, browser or installed app where you want them (a desktop browser works too, over
HTTPS or at `http://localhost`). On an iPhone or iPad this works only in the app added to the Home
Screen (iOS 16.4 or later), not in a Safari tab. The notification goes through the push service of
your phone's or browser's maker (Google, Apple or Mozilla), encrypted so that only your device can
read it. Changing `SECRET_KEY` turns notifications off: turn them on again on each device.

## Your password

Change it in **Settings → Password**. It signs out every other browser and phone. The new password
is stored in kanso's database; `PASSWORD_HASH` in `.env` is only the starting one.

If you forget it, go back to the `.env` password:

```sh
docker compose exec app python -m kanso.reset_password
docker compose restart app
```

(If you have forgotten that one too, make a new `PASSWORD_HASH` as in step 6, run
`docker compose up -d`, then reset as above.)

## Backups

Everything kanso keeps (your connected accounts, the mail copy, labels, settings, assistant
conversations) is in its Postgres database. The mail itself stays in Gmail too, so a lost database
can always be synced again; a backup saves the time and keeps kanso's own settings.

Back up to a file:

```sh
docker compose exec -T postgres pg_dump -U kanso -d kanso -Fc > kanso-$(date +%F).dump
```

Restore a backup into a running kanso (this replaces what is in the database now):

```sh
docker compose stop app worker
docker compose exec -T postgres pg_restore -U kanso -d kanso --clean --if-exists < kanso-2026-11-02.dump
docker compose start app worker
```

Keep `.env` with your backups: the database is no use without its `SECRET_KEY`, which encrypts the
Gmail tokens in it. Treat both as secrets.

## Upgrading

kanso's image is published to `ghcr.io/seriaati/kanso`, and `latest` follows the main branch. To
upgrade:

```sh
# The latest compose file and .env.example (in a clone: git pull)
curl -fsSLO https://raw.githubusercontent.com/seriaati/kanso/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/seriaati/kanso/main/.env.example
docker compose pull     # the new kanso image, plus Postgres 18 and Redis 8 patch releases
docker compose up -d
```

To run your own changes instead, build the image from the source in a clone with
`docker compose up -d --build`.

Database changes apply by themselves when the new version starts (the `app` container runs the
migrations first, and the `worker` waits for it). Take a backup first if you want to be able to go
back: an older kanso can't use a database a newer one has changed. After an upgrade, an open
kanso offers **Reload** to switch to the new version.

If an upgrade ever needs something done by hand, such as a new setting in `.env`, this section
and `.env.example` say so.

- **Account and sender photos** (November 2026): enable the **People API** (step 2), add the
  `userinfo.profile`, `contacts.readonly` and `contacts.other.readonly` scopes (step 3), then
  connect each account again (**Settings → Connect a Gmail account**, same Google account; it
  keeps its mail and settings). Until then kanso works as before, without photos.

## Troubleshooting

- **`redirect_uri_mismatch` from Google**: the redirect URI in the OAuth client doesn't match
  `PUBLIC_URL` + `/api/accounts/callback` exactly (scheme, host, port, no trailing slash).
- **"Gmail access was not granted"**: the Gmail permission was unticked on Google's consent
  screen. Connect again and tick it.
- **"Not logged in" after Google sends you back**: you started from a different address than
  `PUBLIC_URL`. Open kanso at `PUBLIC_URL`, log in, and connect again.
- **"Google OAuth is not configured"**: `GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_SECRET` is empty.
  Set them and run `docker compose up -d`.
- **kanso won't start and the log mentions `secret_key` or `password_hash`**: `SECRET_KEY` must be
  at least 32 characters, and `PASSWORD_HASH` must be the line from `kanso.hash_password` (with
  its quotes). See the log with `docker compose logs app`.
- **An account stops syncing after a week**: the consent screen is still in *Testing*. Publish it
  (step 3) and connect the account again.
- **The phone offers no "Install"**: kanso must be opened over HTTPS (step 8), at `PUBLIC_URL`.
- **No notifications**: check that Settings → Notifications is on *on that device* and that the
  phone allows notifications for kanso (or for the browser). New mail must reach kanso first: with
  Gmail push (step 5) that takes seconds, without it up to 2 minutes.
- **"Missing the X-Requested-With header"**: something between the browser and kanso drops request
  headers. kanso refuses changes without that header, so other websites can't make them in your
  name. Caddy and Tailscale pass it through as is.
