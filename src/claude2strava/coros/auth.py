"""
COROS Open API — OAuth2 authorization code flow.

Docs: https://open.coros.com/
Auth endpoint: https://open.coros.com/oauth2/authorize
Token endpoint: https://open.coros.com/oauth2/accesstoken
Refresh endpoint: https://open.coros.com/oauth2/refreshtoken
"""

import base64
import os
import time
import urllib.parse

import httpx


COROS_AUTH_URL = "https://open.coros.com/oauth2/authorize"
COROS_TOKEN_URL = "https://open.coros.com/oauth2/accesstoken"
COROS_REFRESH_URL = "https://open.coros.com/oauth2/refreshtoken"
REDIRECT_URI = "http://localhost:{port}/auth/coros/callback"


class CorosAuthError(Exception):
    pass


def generate_state() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()


def build_auth_url(client_id: str, state: str, port: int = 8080) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI.format(port=port),
        "response_type": "code",
        "state": state,
    }
    return f"{COROS_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
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
    }
    actual = http_client or httpx.AsyncClient()
    if http_client is None:
        async with httpx.AsyncClient() as c:
            resp = await c.post(COROS_TOKEN_URL, data=payload)
    else:
        resp = await actual.post(COROS_TOKEN_URL, data=payload)

    if resp.status_code != 200:
        raise CorosAuthError(f"Token exchange failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    if body.get("result") != "0000":
        raise CorosAuthError(f"COROS error: {body.get('message', body)}")

    return body["data"]


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
    actual = http_client or httpx.AsyncClient()
    if http_client is None:
        async with httpx.AsyncClient() as c:
            resp = await c.post(COROS_REFRESH_URL, data=payload)
    else:
        resp = await actual.post(COROS_REFRESH_URL, data=payload)

    if resp.status_code != 200:
        raise CorosAuthError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    if body.get("result") != "0000":
        raise CorosAuthError(f"COROS error: {body.get('message', body)}")

    data = body["data"]
    now = int(time.time())
    return {
        "access_token": data["accessToken"],
        "refresh_token": data.get("refreshToken", refresh_token),
        "expires_at": now + int(data.get("tokenExpiresIn", 86400)),
        "refresh_expires_at": now + int(data.get("refreshTokenExpiresIn", 7776000)),
    }
