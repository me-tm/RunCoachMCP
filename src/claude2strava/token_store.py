import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .crypto import Crypto


class TokenNotFoundError(Exception):
    pass


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: int  # Unix timestamp
    athlete_id: int
    athlete_name: str = ""

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - buffer_seconds)


class TokenStore:
    def __init__(self, token_dir: Path, crypto: Crypto, filename: str = "tokens.enc") -> None:
        self._path = token_dir / filename
        self._crypto = crypto

    def save(self, data: TokenData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(data))
        encrypted = self._crypto.encrypt(payload)
        self._path.write_bytes(encrypted)
        # Restrict file permissions to owner-only
        self._path.chmod(0o600)

    def load(self) -> TokenData:
        if not self._path.exists():
            raise TokenNotFoundError(
                "No Strava tokens found. Open http://localhost:8080 to connect your account."
            )
        encrypted = self._path.read_bytes()
        payload = self._crypto.decrypt(encrypted)
        return TokenData(**json.loads(payload))

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def exists(self) -> bool:
        return self._path.exists()
