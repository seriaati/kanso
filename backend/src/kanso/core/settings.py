import tempfile
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.low_level import hash_secret
from pydantic import Field, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEMO_PASSWORD = "demo"


class Settings(BaseSettings):
    # ../.env finds the repo-root .env when running from backend/.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://kanso:kanso@localhost:5432/kanso"
    redis_url: str = "redis://localhost:6379/0"
    frontend_dist: Path = Path("../frontend/dist")
    # A short-lived cache; the attachment bytes themselves live in the Gmail draft.
    upload_dir: Path = Path(tempfile.gettempdir()) / "kanso-drafts"

    # https:// makes cookies Secure.
    public_url: str = "http://localhost:8000"
    # Changing it signs you out and means reconnecting every account (it encrypts their tokens).
    secret_key: str = Field(min_length=32)
    # From `python -m kanso.hash_password`.
    password_hash: str = Field(pattern=r"^\$argon2")
    # Empty disables "Connect account".
    google_client_id: str = ""
    google_client_secret: str = ""
    # Without Pub/Sub the worker polls Gmail instead.
    google_pubsub_topic: str = ""
    google_pubsub_subscription: str = ""
    # The service account's JSON key, base64-encoded.
    google_pubsub_credentials: str = ""
    # The Android app's google-services.json, base64-encoded. Empty: no notifications in the app.
    firebase_android_config: str = ""
    # A made-up mailbox, reset every day; no Gmail accounts can be connected.
    demo_mode: bool = False

    @model_validator(mode="before")
    @classmethod
    def _demo_secrets(cls, data: Any) -> Any:
        """A demo instance has nothing to keep secret, whatever the environment says."""
        if not TypeAdapter(bool).validate_python(data.get("demo_mode") or False):
            return data
        # A fixed salt keeps the hash stable, so a restart doesn't sign visitors out.
        p = PasswordHasher()
        demo_hash = hash_secret(
            DEMO_PASSWORD.encode(),
            b"kanso-demo",
            p.time_cost,
            p.memory_cost,
            p.parallelism,
            p.hash_len,
            p.type,
        ).decode()
        return data | {
            "secret_key": "kanso-demo-instance-public-key-not-a-secret",
            "password_hash": demo_hash,
        }

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
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


settings = Settings()
