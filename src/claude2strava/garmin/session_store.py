"""
Encrypted session storage for Garmin Connect.

garminconnect 0.3.x stores auth as three Bearer tokens (di_token,
di_refresh_token, di_client_id) retrievable via client.dumps() as JSON.
We encrypt that JSON blob and write it to
~/.claude2strava/garmin_session.enc  (owner-read-only, mode 0600).

Only tokens are persisted — the password is never stored.
When the session expires (~30 days), the user re-authenticates through
the web dashboard at http://localhost:8080.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..crypto import Crypto


class GarminNotConnectedError(Exception):
    """Raised when no stored session exists."""


class GarminSessionExpiredError(Exception):
    """Raised when stored tokens are rejected by Garmin."""


@dataclass
class GarminSessionData:
    username: str
    token_json: str   # JSON string from garminconnect client.dumps()


class GarminSessionStore:
    def __init__(
        self,
        token_dir: Path,
        crypto: Crypto,
        filename: str = "garmin_session.enc",
    ) -> None:
        self._path = token_dir / filename
        self._crypto = crypto

    def save(self, data: GarminSessionData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(data))
        self._path.write_bytes(self._crypto.encrypt(payload))
        self._path.chmod(0o600)

    def load(self) -> GarminSessionData:
        if not self._path.exists():
            raise GarminNotConnectedError(
                "No Garmin session found. Open http://localhost:8080 to connect."
            )
        payload = self._crypto.decrypt(self._path.read_bytes())
        d = json.loads(payload)
        return GarminSessionData(**d)

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def exists(self) -> bool:
        return self._path.exists()
