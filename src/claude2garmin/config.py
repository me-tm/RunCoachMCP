import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to .env — works regardless of which directory the process starts from.
# config.py lives at src/claude2garmin/config.py → three parents up = project root.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"

# Pre-rename token directory (project was called claude2strava). Migrated
# automatically so an existing Garmin session survives the rename instead of
# forcing a fresh email/password login.
_LEGACY_TOKEN_DIR = Path.home() / ".claude2strava"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # AES-256 key for encrypting tokens on disk. Auto-generated if blank.
    token_encryption_key: str = ""

    web_port: int = 8080
    web_secret_key: str = "change-me-to-a-long-random-string"

    # Directory where the encrypted token file lives (outside the repo)
    token_dir: Path = Path.home() / ".claude2garmin"

    def get_fernet_key(self) -> bytes:
        if self.token_encryption_key:
            return self.token_encryption_key.encode()
        # Auto-generate and print a key so the user can save it
        key = Fernet.generate_key()
        print(
            "\n[claude2garmin] No TOKEN_ENCRYPTION_KEY set — generated a temporary key.\n"
            "Add this to your .env to make it permanent:\n"
            f"TOKEN_ENCRYPTION_KEY={key.decode()}\n"
        )
        return key


def _migrate_legacy_token_dir(token_dir: Path) -> None:
    if token_dir.exists() or not _LEGACY_TOKEN_DIR.exists():
        return
    token_dir.parent.mkdir(parents=True, exist_ok=True)
    _LEGACY_TOKEN_DIR.rename(token_dir)
    print(f"[claude2garmin] Migrated tokens from {_LEGACY_TOKEN_DIR} to {token_dir}")


def get_settings() -> Settings:
    settings = Settings()
    _migrate_legacy_token_dir(settings.token_dir)
    return settings
