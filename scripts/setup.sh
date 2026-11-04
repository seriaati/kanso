#!/usr/bin/env bash
# Sets up Google Cloud for kanso with the gcloud CLI and writes .env. Safe to re-run.
# Usage: scripts/setup.sh [project-id]
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
TOPIC=kanso-gmail
SUBSCRIPTION=kanso-gmail
SA_NAME=kanso-pubsub

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
ask() { local reply; read -r -p "$1 [$2]: " reply; echo "${reply:-$2}"; }
env_get() { [[ -f $ENV_FILE ]] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 || true; }

# Replace KEY=... (or a commented "# KEY=..." line) in .env with KEY=VALUE, or append it.
env_set() {
  KEY="$1" VALUE="$2" awk '
    !done && ($0 ~ "^" ENVIRON["KEY"] "=" || $0 ~ "^# ?" ENVIRON["KEY"] "=") {
      print ENVIRON["KEY"] "=" ENVIRON["VALUE"]; done = 1; next
    }
    { print }
    END { if (!done) print ENVIRON["KEY"] "=" ENVIRON["VALUE"] }
  ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
}

# New service accounts take a moment to become usable.
retry() {
  local i
  for i in 1 2 3 4 5; do "$@" 2>/dev/null && return 0; sleep 5; done
  "$@"
}

for cmd in gcloud docker openssl base64 awk; do
  command -v "$cmd" >/dev/null || die "$cmd is not installed."
done
[[ -n $(gcloud auth list --filter=status:ACTIVE --format='value(account)') ]] \
  || die "gcloud is not logged in. Run: gcloud auth login"

umask 077
if [[ -f $ENV_FILE ]]; then
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
  say "Updating the existing .env (backup saved next to it)."
else
  cp .env.example "$ENV_FILE"
fi

say "1. Google Cloud project"
PROJECT="${1:-$(ask 'Project ID (created if it does not exist)' "kanso-$(openssl rand -hex 3)")}"
if gcloud projects describe "$PROJECT" >/dev/null 2>&1; then
  echo "Using existing project $PROJECT."
else
  gcloud projects create "$PROJECT" --name=kanso
fi

say "2. Enabling the Gmail and People APIs"
gcloud services enable gmail.googleapis.com people.googleapis.com --project "$PROJECT"

say "3. Pub/Sub for instant new-mail notifications (optional)"
PUBSUB=yes
if ! gcloud services enable pubsub.googleapis.com --project "$PROJECT"; then
  echo "Enabling Pub/Sub failed. If the error mentions billing, link a billing account:"
  echo "  gcloud billing accounts list"
  echo "  gcloud billing projects link $PROJECT --billing-account=<ACCOUNT_ID>"
  echo "Pub/Sub's free tier covers kanso's traffic. Without Pub/Sub, kanso checks Gmail every 2 minutes."
  [[ $(ask 'Skip Pub/Sub for now? (y/n)' y) == y ]] || die "Fix the error above and re-run."
  PUBSUB=no
fi

if [[ $PUBSUB == yes ]]; then
  SA_EMAIL="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
  # A new project's location policy takes minutes to initialize; topic creation fails until then.
  for i in $(seq 10); do
    gcloud pubsub topics describe "$TOPIC" --project "$PROJECT" >/dev/null 2>&1 && break
    gcloud pubsub topics create "$TOPIC" --project "$PROJECT" 2>/dev/null && break
    (( i < 10 )) || die "Could not create the Pub/Sub topic. Re-run: scripts/setup.sh $PROJECT"
    echo "The new project isn't ready for Pub/Sub yet; retrying in 30 s ($i/10)..."
    sleep 30
  done
  gcloud pubsub topics add-iam-policy-binding "$TOPIC" --project "$PROJECT" \
    --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
    --role=roles/pubsub.publisher >/dev/null
  gcloud pubsub subscriptions describe "$SUBSCRIPTION" --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud pubsub subscriptions create "$SUBSCRIPTION" --project "$PROJECT" \
      --topic "$TOPIC" --ack-deadline=60
  gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name=kanso
  retry gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION" --project "$PROJECT" \
    --member="serviceAccount:$SA_EMAIL" --role=roles/pubsub.subscriber >/dev/null \
    || die "Could not grant the subscriber role to $SA_EMAIL."

  env_set GOOGLE_PUBSUB_TOPIC "projects/$PROJECT/topics/$TOPIC"
  env_set GOOGLE_PUBSUB_SUBSCRIPTION "projects/$PROJECT/subscriptions/$SUBSCRIPTION"
  if [[ -z $(env_get GOOGLE_PUBSUB_CREDENTIALS) ]]; then
    KEY_FILE=$(mktemp)
    trap 'rm -f "$KEY_FILE"' EXIT
    retry gcloud iam service-accounts keys create "$KEY_FILE" --iam-account "$SA_EMAIL" \
      --project "$PROJECT" || die "Could not create a key for $SA_EMAIL (an organization policy may block it)."
    env_set GOOGLE_PUBSUB_CREDENTIALS "$(base64 < "$KEY_FILE" | tr -d '\n')"
    rm -f "$KEY_FILE"
  else
    echo "Keeping the service account key already in .env."
  fi
fi

PUBLIC_URL=$(ask 'Address you will open kanso at (no trailing slash)' "$(env_get PUBLIC_URL || true)")
PUBLIC_URL="${PUBLIC_URL:-http://localhost:8000}"
env_set PUBLIC_URL "$PUBLIC_URL"

if [[ -z $(env_get GOOGLE_CLIENT_ID) || -z $(env_get GOOGLE_CLIENT_SECRET) ]]; then
  C="https://console.cloud.google.com/auth"
  say "4. OAuth consent screen and client (in the browser; gcloud cannot do these)"
  cat <<EOF
  a) Consent screen: $C/branding?project=$PROJECT
     Get started -> app name "kanso", your email -> Audience: External -> your email -> Create.
  b) Scopes: $C/scopes?project=$PROJECT
     Add or remove scopes -> add openid, .../auth/userinfo.email, .../auth/userinfo.profile,
     .../auth/contacts.readonly, .../auth/contacts.other.readonly and
     https://www.googleapis.com/auth/gmail.modify -> Update -> Save.
  c) Publish: $C/audience?project=$PROJECT
     Publish app -> Confirm. (In "Testing", Google expires your login every 7 days.)
  d) Client: $C/clients/create?project=$PROJECT
     Web application, name "kanso", authorized redirect URI:
       $PUBLIC_URL/api/accounts/callback
     Create, then copy the client ID and secret.
EOF
  read -r -p "Client ID: " CLIENT_ID
  read -r -s -p "Client secret (hidden): " CLIENT_SECRET; echo
  [[ -n $CLIENT_ID && -n $CLIENT_SECRET ]] || die "Client ID and secret are required."
  env_set GOOGLE_CLIENT_ID "$CLIENT_ID"
  env_set GOOGLE_CLIENT_SECRET "$CLIENT_SECRET"
else
  echo "Keeping the OAuth client already in .env (redirect URI: $PUBLIC_URL/api/accounts/callback)."
fi

say "5. Secrets and your login password"
PG=$(env_get POSTGRES_PASSWORD)
[[ -z $PG || $PG == change-me ]] && env_set POSTGRES_PASSWORD "$(openssl rand -hex 24)"
[[ -z $(env_get SECRET_KEY) ]] && env_set SECRET_KEY "$(openssl rand -hex 32)"

if [[ -z $(env_get PASSWORD_HASH) ]]; then
  read -r -s -p "kanso login password (hidden): " PW; echo
  read -r -s -p "Again: " PW2; echo
  [[ $PW == "$PW2" ]] || die "The passwords don't match."
  (( ${#PW} >= 8 )) || die "Use at least 8 characters."
  echo "Pulling the kanso image to hash it (only the first time)..."
  HASH_LINE=$(printf '%s\n' "$PW" | docker compose run --rm -T --no-deps app python -m kanso.hash_password \
    | grep '^PASSWORD_HASH=') || die "Hashing the password failed (see the output above)."
  env_set PASSWORD_HASH "${HASH_LINE#PASSWORD_HASH=}"
else
  echo "Keeping the password already in .env."
fi

say "Done. Start kanso and connect your Gmail accounts:"
cat <<EOF
  docker compose up -d
  open $PUBLIC_URL, log in, then account menu -> Settings -> Connect a Gmail account.
  For the AI features, add your API keys under Settings -> AI keys.
  Google warns that the app is unverified: Advanced -> Go to kanso. Keep the Gmail box ticked.
EOF
