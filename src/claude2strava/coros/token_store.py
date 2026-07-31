"""
Encrypted OAuth token storage for COROS Open API.

Tokens are stored as JSON at ~/.claude2strava/coros_tokens.enc (mode 0600).
Only access + refresh tokens are persisted — never credentials.
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..crypto import Crypto


class CorosNotConnectedError(Exception):
    """Raised when no stored COROS token exists."""


class CorosTokenExpiredError(Exception):
    """Raised when the stored access token has expired and refresh fails."""


@dataclass
class CorosTokenData:
    open_id: str
    access_token: str
    refresh_token: str
    expires_at: int       # Unix timestamp for access token
    refresh_expires_at: int  # Unix timestamp for refresh token

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - buffer_seconds)

    def refresh_is_expired(self) -> bool:
        return time.time() >= self.refresh_expires_at


class CorosTokenStore:
    def __init__(
        self,
        token_dir: Path,
        crypto: Crypto,
        filename: str = "coros_tokens.enc",
    ) -> None:
        self._path = token_dir / filename
        self._crypto = crypto

    def save(self, data: CorosTokenData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(data))
        self._path.write_bytes(self._crypto.encrypt(payload))
        self._path.chmod(0o600)

    def load(self) -> CorosTokenData:
        if not self._path.exists():
            raise CorosNotConnectedError(
                "No COROS tokens found. Open http://localhost:8080 to connect your account."
            )
        payload = self._crypto.decrypt(self._path.read_bytes())
        return CorosTokenData(**json.loads(payload))

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def exists(self) -> bool:
        return self._path.exists()
