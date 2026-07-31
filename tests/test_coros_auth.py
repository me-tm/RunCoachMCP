"""Tests for COROS OAuth2 auth helpers."""
import urllib.parse
import time
import pytest
import httpx
import respx

from claude2strava.coros.auth import (
    generate_state,
    build_auth_url,
    exchange_code,
    refresh_access_token,
    CorosAuthError,
    COROS_TOKEN_URL,
    COROS_REFRESH_URL,
    COROS_AUTH_URL,
)


# ── generate_state ────────────────────────────────────────────────────────────

def test_generate_state_is_unique():
    assert generate_state() != generate_state()


def test_generate_state_is_str():
    assert isinstance(generate_state(), str)


def test_generate_state_no_padding():
    assert "=" not in generate_state()


# ── build_auth_url ────────────────────────────────────────────────────────────

def test_build_auth_url_base():
    url = build_auth_url("my_cid", "my_state", port=8080)
    assert url.startswith(COROS_AUTH_URL)


def test_build_auth_url_required_params():
    url = build_auth_url("my_cid", "my_state", port=8080)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert params["client_id"] == "my_cid"
    assert params["state"] == "my_state"
    assert params["response_type"] == "code"
    assert "localhost:8080" in params["redirect_uri"]


def test_build_auth_url_custom_port():
    url = build_auth_url("cid", "st", port=9090)
    assert "9090" in url


# ── exchange_code ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_success():
    now = int(time.time())
    respx.post(COROS_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "result": "0000",
        "message": "OK",
        "data": {
            "openId": "user_open_id",
            "accessToken": "acc_tok",
            "refreshToken": "ref_tok",
            "tokenExpiresIn": 86400,
            "refreshTokenExpiresIn": 7776000,
        },
    }))
    data = await exchange_code("cid", "csec", "auth_code", port=8080)
    assert data["accessToken"] == "acc_tok"
    assert data["refreshToken"] == "ref_tok"
    assert data["openId"] == "user_open_id"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_http_error_raises():
    respx.post(COROS_TOKEN_URL).mock(return_value=httpx.Response(400, text="Bad Request"))
    with pytest.raises(CorosAuthError, match="400"):
        await exchange_code("cid", "csec", "bad_code", port=8080)


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_api_error_raises():
    respx.post(COROS_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "result": "1001",
        "message": "Invalid authorization code",
        "data": {},
    }))
    with pytest.raises(CorosAuthError, match="Invalid authorization code"):
        await exchange_code("cid", "csec", "bad_code", port=8080)


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_uses_provided_http_client():
    """exchange_code must reuse the passed-in client (no extra connections)."""
    respx.post(COROS_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "result": "0000",
        "message": "OK",
        "data": {
            "openId": "oid", "accessToken": "at", "refreshToken": "rt",
            "tokenExpiresIn": 3600, "refreshTokenExpiresIn": 7776000,
        },
    }))
    async with httpx.AsyncClient() as client:
        data = await exchange_code("cid", "csec", "code", port=8080, http_client=client)
    assert data["accessToken"] == "at"


# ── refresh_access_token ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_success():
    respx.post(COROS_REFRESH_URL).mock(return_value=httpx.Response(200, json={
        "result": "0000",
        "message": "OK",
        "data": {
            "accessToken": "new_acc",
            "refreshToken": "new_ref",
            "tokenExpiresIn": 86400,
            "refreshTokenExpiresIn": 7776000,
        },
    }))
    result = await refresh_access_token("cid", "csec", "old_ref")
    assert result["access_token"] == "new_acc"
    assert result["refresh_token"] == "new_ref"
    assert result["expires_at"] > int(time.time())
    assert result["refresh_expires_at"] > result["expires_at"]


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_http_error_raises():
    respx.post(COROS_REFRESH_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(CorosAuthError, match="401"):
        await refresh_access_token("cid", "csec", "bad_ref")


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_api_error_raises():
    respx.post(COROS_REFRESH_URL).mock(return_value=httpx.Response(200, json={
        "result": "2001",
        "message": "Refresh token expired",
        "data": {},
    }))
    with pytest.raises(CorosAuthError, match="Refresh token expired"):
        await refresh_access_token("cid", "csec", "expired_ref")


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_falls_back_to_old_refresh_when_missing():
    """If the response omits refreshToken, the old token should be kept."""
    respx.post(COROS_REFRESH_URL).mock(return_value=httpx.Response(200, json={
        "result": "0000",
        "message": "OK",
        "data": {
            "accessToken": "new_acc",
            "tokenExpiresIn": 3600,
            "refreshTokenExpiresIn": 7776000,
            # refreshToken intentionally omitted
        },
    }))
    result = await refresh_access_token("cid", "csec", "keep_this_ref")
    assert result["refresh_token"] == "keep_this_ref"
