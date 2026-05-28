import time
import pytest
import httpx
import respx

from claude2strava.crypto import Crypto
from claude2strava.strava.auth import STRAVA_TOKEN_URL, StravaAuthError
from claude2strava.strava.client import StravaClient, STRAVA_API_BASE
from claude2strava.token_store import TokenData, TokenStore, TokenNotFoundError


@pytest.fixture
def store(tmp_path, fernet_key):
    return TokenStore(tmp_path, Crypto(fernet_key))


@pytest.fixture
def valid_token(store) -> TokenData:
    t = TokenData(
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=int(time.time()) + 3600,
        athlete_id=99,
        athlete_name="Jane Doe",
    )
    store.save(t)
    return t


@pytest.fixture
def expired_token(store) -> TokenData:
    t = TokenData(
        access_token="old_acc",
        refresh_token="good_ref",
        expires_at=int(time.time()) - 10,
        athlete_id=99,
        athlete_name="Jane Doe",
    )
    store.save(t)
    return t


def make_client(store: TokenStore) -> StravaClient:
    return StravaClient(store, client_id="cid", client_secret="csec")


# ── get_athlete ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_athlete(store, valid_token):
    respx.get(f"{STRAVA_API_BASE}/athlete").mock(return_value=httpx.Response(200, json={
        "id": 99, "firstname": "Jane", "lastname": "Doe", "username": "janedoe",
    }))
    client = make_client(store)
    result = await client.get_athlete()
    assert result["id"] == 99
    assert result["firstname"] == "Jane"


# ── list_activities ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_activities_no_filter(store, valid_token):
    activities = [{"id": 1, "type": "Run"}, {"id": 2, "type": "Ride"}]
    respx.get(f"{STRAVA_API_BASE}/athlete/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )
    client = make_client(store)
    result = await client.list_activities()
    assert len(result) == 2


@pytest.mark.asyncio
@respx.mock
async def test_list_activities_type_filter(store, valid_token):
    activities = [{"id": 1, "type": "Run"}, {"id": 2, "type": "Ride"}]
    respx.get(f"{STRAVA_API_BASE}/athlete/activities").mock(
        return_value=httpx.Response(200, json=activities)
    )
    client = make_client(store)
    result = await client.list_activities(activity_type="Run")
    assert len(result) == 1
    assert result[0]["type"] == "Run"


# ── get_activity ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_activity(store, valid_token):
    respx.get(f"{STRAVA_API_BASE}/activities/123").mock(return_value=httpx.Response(200, json={
        "id": 123, "name": "Morning Run", "type": "Run", "distance": 10000.0,
        "moving_time": 3600, "splits_metric": [], "best_efforts": [],
    }))
    client = make_client(store)
    result = await client.get_activity(123)
    assert result["id"] == 123
    assert result["name"] == "Morning Run"


# ── get_activity_streams ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_activity_streams(store, valid_token):
    respx.get(f"{STRAVA_API_BASE}/activities/123/streams").mock(return_value=httpx.Response(200, json={
        "heartrate": {"data": [120, 130, 140], "series_type": "distance", "resolution": "medium"},
        "time": {"data": [0, 1, 2], "series_type": "distance", "resolution": "medium"},
    }))
    client = make_client(store)
    result = await client.get_activity_streams(123)
    assert "heartrate" in result
    assert result["heartrate"]["data"] == [120, 130, 140]


# ── get_athlete_stats ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_athlete_stats(store, valid_token):
    respx.get(f"{STRAVA_API_BASE}/athletes/99/stats").mock(return_value=httpx.Response(200, json={
        "ytd_run_totals": {"count": 50, "distance": 500000, "moving_time": 180000, "elevation_gain": 5000},
        "all_run_totals": {"count": 200, "distance": 2000000, "moving_time": 720000, "elevation_gain": 20000},
    }))
    client = make_client(store)
    result = await client.get_athlete_stats(99)
    assert result["ytd_run_totals"]["count"] == 50


# ── get_athlete_zones ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_athlete_zones(store, valid_token):
    zones = {"heart_rate": {"custom_zones": False, "zones": [{"min": 0, "max": 115}]}}
    respx.get(f"{STRAVA_API_BASE}/athlete/zones").mock(return_value=httpx.Response(200, json=zones))
    client = make_client(store)
    result = await client.get_athlete_zones()
    assert "heart_rate" in result


# ── get_starred_segments ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_starred_segments(store, valid_token):
    segs = [{"id": 1, "name": "Box Hill", "distance": 7000.0}]
    respx.get(f"{STRAVA_API_BASE}/segments/starred").mock(return_value=httpx.Response(200, json=segs))
    client = make_client(store)
    result = await client.get_starred_segments()
    assert result[0]["id"] == 1


# ── Token auto-refresh ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_auto_refresh_on_expired_token(store, expired_token):
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "refreshed_acc",
        "refresh_token": "refreshed_ref",
        "expires_at": int(time.time()) + 3600,
    }))
    respx.get(f"{STRAVA_API_BASE}/athlete").mock(return_value=httpx.Response(200, json={"id": 99}))
    client = make_client(store)
    await client.get_athlete()
    # Token in store should now be refreshed
    updated = store.load()
    assert updated.access_token == "refreshed_acc"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_failure_raises_auth_error(store, expired_token):
    respx.post(STRAVA_TOKEN_URL).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    client = make_client(store)
    with pytest.raises(StravaAuthError):
        await client.get_athlete()


# ── No token ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_token_raises(store):
    client = make_client(store)
    with pytest.raises(TokenNotFoundError):
        await client.get_athlete()
