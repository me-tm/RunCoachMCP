import base64
import hashlib
import os
import urllib.parse
from dataclasses import dataclass

import httpx


STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
REDIRECT_URI = "http://localhost:{port}/auth/strava/callback"
SCOPES = "read,activity:read_all,profile:read_all"


@dataclass
class PKCEPair:
    verifier: str
    challenge: str


class StravaAuthError(Exception):
    pass


def generate_pkce_pair() -> PKCEPair:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PKCEPair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()


def build_auth_url(client_id: str, state: str, code_challenge: str, port: int = 8080) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI.format(port=port),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{STRAVA_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
    port: int = 8080,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI.format(port=port),
        "code_verifier": code_verifier,
    }
    client = http_client or httpx.AsyncClient()
    async with (httpx.AsyncClient() if http_client is None else _nullctx(client)) as c:
        actual = http_client if http_client else c
        resp = await actual.post(STRAVA_TOKEN_URL, data=payload)
    if resp.status_code != 200:
        raise StravaAuthError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    client = http_client or httpx.AsyncClient()
    async with (httpx.AsyncClient() if http_client is None else _nullctx(client)) as c:
        actual = http_client if http_client else c
        resp = await actual.post(STRAVA_TOKEN_URL, data=payload)
    if resp.status_code != 200:
        raise StravaAuthError(f"Token refresh failed ({resp.status_code}): {resp.text}")
    return resp.json()


class _nullctx:
    """Async context manager that wraps an already-open client without closing it."""
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_: object) -> None:
        pass
