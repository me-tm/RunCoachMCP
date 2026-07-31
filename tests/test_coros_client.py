"""Tests for CorosClient — API calls and auto-refresh logic."""
import time
import pytest
import httpx
import respx

from claude2strava.crypto import Crypto
from claude2strava.coros.client import CorosClient, COROS_API_BASE
from claude2strava.coros.token_store import (
    CorosNotConnectedError,
    CorosTokenData,
    CorosTokenStore,
    CorosTokenExpiredError,
)
from claude2strava.coros.auth import COROS_REFRESH_URL


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _store_with_token(tmp_path, fernet_key, *, access_offset=3600, refresh_offset=7776000) -> CorosTokenStore:
    now = int(time.time())
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(CorosTokenData(
        open_id="test_open_id",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=now + access_offset,
        refresh_expires_at=now + refresh_offset,
    ))
    return store


def _client(store: CorosTokenStore) -> CorosClient:
    return CorosClient(store, client_id="cid", client_secret="csec")


# ── No token ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_token_raises_not_connected(tmp_path, fernet_key):
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    client = _client(store)
    with pytest.raises(CorosNotConnectedError):
        await client.get_profile()


# ── get_profile ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_profile_success(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    respx.get(f"{COROS_API_BASE}/v2/coros/user/profile").mock(return_value=httpx.Response(200, json={
        "result": "0000",
        "message": "OK",
        "data": {"openId": "test_open_id", "nickName": "RunnerJoe"},
    }))
    profile = await client.get_profile()
    assert profile["nickName"] == "RunnerJoe"


@pytest.mark.asyncio
@respx.mock
async def test_get_profile_passes_open_id_param(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    captured = {}

    def handler(request):
        captured["openId"] = request.url.params.get("openId")
        return httpx.Response(200, json={"result": "0000", "message": "OK", "data": {}})

    respx.get(f"{COROS_API_BASE}/v2/coros/user/profile").mock(side_effect=handler)
    await client.get_profile()
    assert captured["openId"] == "test_open_id"


# ── list_activities ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_activities_returns_list(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    activities = [
        {"fitFileId": "fit_1", "name": "Morning Run", "sportType": 100,
         "distance": 10000, "totalTime": 3600, "avgHr": 148, "calorie": 600},
    ]
    respx.get(f"{COROS_API_BASE}/v2/coros/sport/list").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK",
        "data": {"dataList": activities},
    }))
    result = await client.list_activities("2024-01-01", "2024-01-31")
    assert len(result) == 1
    assert result[0]["fitFileId"] == "fit_1"
    assert result[0]["name"] == "Morning Run"


@pytest.mark.asyncio
@respx.mock
async def test_list_activities_empty_data(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    respx.get(f"{COROS_API_BASE}/v2/coros/sport/list").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": {"dataList": []},
    }))
    result = await client.list_activities("2024-01-01", "2024-01-31")
    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_list_activities_strips_hyphens_from_dates(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    captured = {}

    def handler(request):
        captured["startDay"] = request.url.params.get("startDay")
        captured["endDay"] = request.url.params.get("endDay")
        return httpx.Response(200, json={"result": "0000", "message": "OK", "data": {"dataList": []}})

    respx.get(f"{COROS_API_BASE}/v2/coros/sport/list").mock(side_effect=handler)
    await client.list_activities("2024-06-01", "2024-06-30")
    assert captured["startDay"] == "20240601"
    assert captured["endDay"] == "20240630"


# ── get_activity ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_activity_success(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    detail = {"fitFileId": "fit_42", "name": "Long Run", "distance": 21097, "totalTime": 7200}
    respx.get(f"{COROS_API_BASE}/v2/coros/sport/detail").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": detail,
    }))
    result = await client.get_activity("fit_42")
    assert result["fitFileId"] == "fit_42"
    assert result["totalTime"] == 7200


# ── list_daily_data ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_daily_data_returns_list(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    days = [
        {"date": "20240601", "steps": 8500, "calorie": 2200, "restingHr": 52},
        {"date": "20240602", "steps": 12000, "calorie": 2500, "restingHr": 50},
    ]
    respx.get(f"{COROS_API_BASE}/v2/coros/daily/list").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": {"dataList": days},
    }))
    result = await client.list_daily_data("2024-06-01", "2024-06-02")
    assert len(result) == 2
    assert result[0]["steps"] == 8500


# ── list_sleep ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_sleep_returns_list(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    nights = [{"date": "20240601", "totalSleepTime": 28800, "sleepScore": 82, "deepSleepTime": 5400}]
    respx.get(f"{COROS_API_BASE}/v2/coros/sleep/list").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": {"dataList": nights},
    }))
    result = await client.list_sleep("2024-06-01", "2024-06-01")
    assert result[0]["totalSleepTime"] == 28800
    assert result[0]["sleepScore"] == 82


# ── list_training_load ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_training_load_returns_list(tmp_path, fernet_key):
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    loads = [{"date": "20240601", "dailyLoad": 45, "aerobicLoad": 38, "anaerobicLoad": 7}]
    respx.get(f"{COROS_API_BASE}/v2/coros/trainingload/list").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": {"dataList": loads},
    }))
    result = await client.list_training_load("2024-06-01", "2024-06-01")
    assert result[0]["dailyLoad"] == 45


# ── auto-refresh ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_auto_refresh_when_access_token_expired(tmp_path, fernet_key):
    """An expired access token should trigger a refresh before the API call."""
    now = int(time.time())
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(CorosTokenData(
        open_id="oid",
        access_token="expired_acc",
        refresh_token="valid_ref",
        expires_at=now - 10,           # already expired
        refresh_expires_at=now + 7776000,
    ))
    client = _client(store)

    respx.post(COROS_REFRESH_URL).mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK",
        "data": {
            "accessToken": "fresh_acc",
            "refreshToken": "fresh_ref",
            "tokenExpiresIn": 86400,
            "refreshTokenExpiresIn": 7776000,
        },
    }))
    respx.get(f"{COROS_API_BASE}/v2/coros/user/profile").mock(return_value=httpx.Response(200, json={
        "result": "0000", "message": "OK", "data": {"openId": "oid"},
    }))

    await client.get_profile()

    # Persisted token must be the refreshed one
    saved = store.load()
    assert saved.access_token == "fresh_acc"
    assert saved.refresh_token == "fresh_ref"


@pytest.mark.asyncio
async def test_raises_when_refresh_token_also_expired(tmp_path, fernet_key):
    """Both tokens expired → CorosTokenExpiredError, no network call attempted."""
    now = int(time.time())
    store = CorosTokenStore(tmp_path, Crypto(fernet_key))
    store.save(CorosTokenData(
        open_id="oid",
        access_token="expired_acc",
        refresh_token="expired_ref",
        expires_at=now - 100,
        refresh_expires_at=now - 1,
    ))
    client = _client(store)
    with pytest.raises(CorosTokenExpiredError, match="Reconnect"):
        await client.get_profile()


# ── API error propagation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_api_error_result_raises(tmp_path, fernet_key):
    from claude2strava.coros.auth import CorosAuthError
    store = _store_with_token(tmp_path, fernet_key)
    client = _client(store)
    respx.get(f"{COROS_API_BASE}/v2/coros/user/profile").mock(return_value=httpx.Response(200, json={
        "result": "5001",
        "message": "Permission denied",
        "data": None,
    }))
    with pytest.raises(CorosAuthError, match="Permission denied"):
        await client.get_profile()
