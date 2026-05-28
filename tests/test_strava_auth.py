import urllib.parse
import pytest
import httpx
import respx

from claude2strava.strava.auth import (
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    generate_state,
    refresh_access_token,
    StravaAuthError,
    STRAVA_TOKEN_URL,
)


def test_generate_pkce_pair_is_unique():
    a = generate_pkce_pair()
    b = generate_pkce_pair()
    assert a.verifier != b.verifier
    assert a.challenge != b.challenge


def test_pkce_verifier_and_challenge_are_strings():
    pair = generate_pkce_pair()
    assert isinstance(pair.verifier, str)
    assert isinstance(pair.challenge, str)


def test_pkce_no_padding():
    pair = generate_pkce_pair()
    assert "=" not in pair.verifier
    assert "=" not in pair.challenge


def test_generate_state_unique():
    assert generate_state() != generate_state()


def test_build_auth_url_contains_required_params():
    url = build_auth_url("my_id", "my_state", "my_challenge", port=8080)
    parsed = urllib.parse.urlparse(url)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    assert params["client_id"] == "my_id"
    assert params["state"] == "my_state"
    assert params["code_challenge"] == "my_challenge"
    assert params["code_challenge_method"] == "S256"
    assert params["response_type"] == "code"
    assert "activity:read_all" in params["scope"]
    assert "localhost:8080" in params["redirect_uri"]


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_success():
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "acc",
        "refresh_token": "ref",
        "expires_at": 9999999999,
        "athlete": {"id": 1, "firstname": "Test", "lastname": "User"},
    }))
    async with httpx.AsyncClient() as client:
        result = await exchange_code("cid", "csec", "code123", "verifier", http_client=client)
    assert result["access_token"] == "acc"
    assert result["athlete"]["id"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_failure_raises():
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(400, json={"message": "Bad Request"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(StravaAuthError, match="400"):
            await exchange_code("cid", "csec", "bad_code", "verifier", http_client=client)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_success():
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "new_acc",
        "refresh_token": "new_ref",
        "expires_at": 9999999999,
    }))
    async with httpx.AsyncClient() as client:
        result = await refresh_access_token("cid", "csec", "old_ref", http_client=client)
    assert result["access_token"] == "new_acc"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_failure_raises():
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(StravaAuthError, match="401"):
            await refresh_access_token("cid", "csec", "expired_ref", http_client=client)
