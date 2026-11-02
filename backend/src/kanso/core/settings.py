import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # The single .env lives at the repo root; ../.env finds it when running from backend/.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://kanso:kanso@localhost:5432/kanso"
    redis_url: str = "redis://localhost:6379/0"
    # Built frontend served by the API. The Docker image sets this to its own copy.
    frontend_dist: Path = Path("../frontend/dist")
    # A short-lived cache of draft attachments; the bytes themselves live in the Gmail draft.
    upload_dir: Path = Path(tempfile.gettempdir()) / "kanso-drafts"

    # The URL you open kanso at. Builds the Google redirect URI; https:// makes cookies Secure.
    public_url: str = "http://localhost:8000"
    # Signs the session cookie and encrypts Gmail refresh tokens. Changing it signs you out and
    # means reconnecting every account.
    secret_key: str = Field(min_length=32)
    # argon2 hash of the login password, from `python -m kanso.hash_password`.
    password_hash: str = Field(pattern=r"^\$argon2")
    # Your Google Cloud OAuth client (docs/SETUP.md). Empty disables "Connect account".
    google_client_id: str = ""
    google_client_secret: str = ""
    # Gmail push through a Pub/Sub pull subscription (docs/SETUP.md, step 5). Without them the
    # worker polls Gmail instead.
    google_pubsub_topic: str = ""
    google_pubsub_subscription: str = ""
    # The service account's JSON key, base64-encoded.
    google_pubsub_credentials: str = ""
    # Claude, for AI labeling (and later summaries and the assistant). Without a key every AI
    # feature is off and kanso is a plain email client.
    anthropic_api_key: str = ""
    # The small, fast model that sorts incoming mail into labels.
    ai_label_model: str = "claude-haiku-5-5"
    # The stronger model for the assistant (Phase 8) and, unless set below, thread summaries.
    ai_model: str = "claude-sonnet-5-5"
    # Thread summaries only, e.g. claude-haiku-5-5 if they arrive too slowly. Empty: AI_MODEL.
    ai_summary_model: str = ""

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/api/accounts/callback"

    @property
    def pubsub_configured(self) -> bool:
        return bool(
            self.google_pubsub_topic
            and self.google_pubsub_subscription
            and self.google_pubsub_credentials
        )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def summary_model(self) -> str:
        return self.ai_summary_model or self.ai_model

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


settings = Settings()
