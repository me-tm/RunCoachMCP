"""
Tests for MCP server tools. We test the underlying tool functions directly
by calling the StravaClient methods via a patched client, without standing up
a full MCP transport.
"""
import time
import pytest
import httpx
import respx
from unittest.mock import AsyncMock, MagicMock, patch

from claude2strava.crypto import Crypto
from claude2strava.strava.client import StravaClient, STRAVA_API_BASE
from claude2strava.token_store import TokenData, TokenStore, TokenNotFoundError


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_store_with_token(tmp_path, fernet_key, expires_offset=3600) -> TokenStore:
    store = TokenStore(tmp_path, Crypto(fernet_key))
    store.save(TokenData(
        access_token="mcp_acc",
        refresh_token="mcp_ref",
        expires_at=int(time.time()) + expires_offset,
        athlete_id=7,
        athlete_name="MCP Athlete",
    ))
    return store


# ── check_connection ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_check_connection_connected(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    respx.get(f"{STRAVA_API_BASE}/athlete").mock(return_value=httpx.Response(200, json={
        "id": 7, "firstname": "MCP", "lastname": "Athlete", "username": "mcpuser",
        "city": "London", "country": "UK", "follower_count": 10, "friend_count": 5,
    }))
    result = await client.get_athlete()
    assert result["id"] == 7
    assert result["firstname"] == "MCP"


@pytest.mark.asyncio
async def test_check_connection_no_token(tmp_path, fernet_key):
    store = TokenStore(tmp_path, Crypto(fernet_key))  # empty
    client = StravaClient(store, "cid", "csec")
    with pytest.raises(TokenNotFoundError):
        await client.get_athlete()


# ── list_activities ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_list_activities_returns_list(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    acts = [
        {"id": 1, "name": "Easy Run", "type": "Run", "distance": 5000.0, "moving_time": 1800,
         "start_date_local": "2024-06-01T07:00:00Z", "total_elevation_gain": 50,
         "average_speed": 2.78, "max_speed": 3.5, "average_heartrate": 145, "max_heartrate": 165,
         "average_watts": None, "kudos_count": 3, "trainer": False, "commute": False,
         "elapsed_time": 1900},
    ]
    respx.get(f"{STRAVA_API_BASE}/athlete/activities").mock(return_value=httpx.Response(200, json=acts))
    result = await client.list_activities()
    assert isinstance(result, list)
    assert result[0]["id"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_list_activities_with_date_filter(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    respx.get(f"{STRAVA_API_BASE}/athlete/activities").mock(return_value=httpx.Response(200, json=[]))
    result = await client.list_activities(after=1700000000, before=1710000000)
    assert result == []


# ── get_activity ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_activity_returns_dict(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    respx.get(f"{STRAVA_API_BASE}/activities/42").mock(return_value=httpx.Response(200, json={
        "id": 42, "name": "Long Sunday Run", "type": "Run",
        "distance": 21097.5, "moving_time": 7200, "elapsed_time": 7400,
        "total_elevation_gain": 200, "splits_metric": [], "best_efforts": [],
        "start_date_local": "2024-06-09T09:00:00Z",
    }))
    result = await client.get_activity(42)
    assert result["id"] == 42
    assert result["name"] == "Long Sunday Run"


# ── get_activity_streams ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_streams_returns_keyed_dict(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    respx.get(f"{STRAVA_API_BASE}/activities/42/streams").mock(return_value=httpx.Response(200, json={
        "heartrate": {"data": [140, 150, 160], "series_type": "time", "resolution": "high"},
    }))
    result = await client.get_activity_streams(42, keys=["heartrate"])
    assert "heartrate" in result
    assert result["heartrate"]["data"] == [140, 150, 160]


# ── get_athlete_stats ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_athlete_stats_shape(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    respx.get(f"{STRAVA_API_BASE}/athletes/7/stats").mock(return_value=httpx.Response(200, json={
        "ytd_run_totals": {"count": 30, "distance": 300000.0, "moving_time": 108000, "elevation_gain": 3000},
        "all_run_totals": {"count": 150, "distance": 1500000.0, "moving_time": 540000, "elevation_gain": 15000},
        "ytd_ride_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "ytd_swim_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "all_ride_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "all_swim_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "recent_run_totals": {"count": 5, "distance": 50000.0, "moving_time": 18000, "elevation_gain": 500},
        "recent_ride_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "recent_swim_totals": {"count": 0, "distance": 0, "moving_time": 0, "elevation_gain": 0},
        "biggest_ride_distance": 0,
        "biggest_climb_elevation_gain": 500,
    }))
    result = await client.get_athlete_stats(7)
    assert "ytd_run_totals" in result
    assert result["ytd_run_totals"]["count"] == 30


# ── get_athlete_zones ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_athlete_zones(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    zones = {"heart_rate": {"custom_zones": False, "zones": [{"min": 0, "max": 115, "name": "Z1"}]}}
    respx.get(f"{STRAVA_API_BASE}/athlete/zones").mock(return_value=httpx.Response(200, json=zones))
    result = await client.get_athlete_zones()
    assert "heart_rate" in result


# ── get_starred_segments ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_starred_segments(tmp_path, fernet_key):
    store = make_store_with_token(tmp_path, fernet_key)
    client = StravaClient(store, "cid", "csec")
    segs = [{"id": 55, "name": "Ditchling Beacon", "distance": 1900.0, "activity_type": "Ride",
              "average_grade": 9.1, "maximum_grade": 18.0, "elevation_high": 248.0,
              "elevation_low": 75.0, "total_elevation_gain": 173.0,
              "city": "Brighton", "country": "UK", "climb_category": 3,
              "athlete_count": 5000, "effort_count": 25000}]
    respx.get(f"{STRAVA_API_BASE}/segments/starred").mock(return_value=httpx.Response(200, json=segs))
    result = await client.get_starred_segments()
    assert result[0]["id"] == 55
    assert result[0]["name"] == "Ditchling Beacon"
