<img src="frontend/icons/icon.svg" alt="" width="72" height="72">

# Kanso

Kanso (簡素) stands for "simplicity" in Japanese. It is a open-source, self-hosted email client built for getting to inbox zero. Kanso is inspired by Shortwave, but with no paywall, and everything stays local.

You can try out Kanso at https://demo.kanso.seria.moe with the password `demo`. AI features are off, and the database resets daily.

![kanso: the inbox grouped by label, a thread open, and the assistant docked below it](docs/screenshot.png)

## Quick start

You need Docker Compose.

```sh
git clone <this repository's URL> kanso
cd kanso
scripts/setup.sh    # needs the gcloud CLI; or: cp .env.example .env and set it up by hand
```

Then follow [docs/SETUP.md](docs/SETUP.md). It walks you through creating your own Google OAuth
client and Pub/Sub subscription, filling in `.env`, setting your password, starting kanso with
`docker compose up -d`, connecting your accounts, and installing kanso on your phone.

To look around before connecting a real account, fill in `.env` (SETUP.md, step 6), start kanso
and load a made-up mailbox:

```sh
docker compose exec app python -m kanso.demo_seed
```
