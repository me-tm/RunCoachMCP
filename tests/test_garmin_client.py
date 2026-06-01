"""
Tests for garmin/session_store.py and garmin/client.py.

garminconnect is mocked entirely — no real HTTP calls.
"""

import json
import stat
from unittest.mock import MagicMock, patch

import pytest

from claude2strava.crypto import Crypto
from claude2strava.garmin.client import GarminClient, extract_session, restore_session
from claude2strava.garmin.session_store import (
    GarminNotConnectedError,
    GarminSessionData,
    GarminSessionExpiredError,
    GarminSessionStore,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

SAMPLE_TOKEN_JSON = json.dumps({
    "di_token": "tok_abc",
    "di_refresh_token": "ref_xyz",
    "di_client_id": "client_123",
})


@pytest.fixture
def store(tmp_path, fernet_key):
    return GarminSessionStore(tmp_path, Crypto(fernet_key))


@pytest.fixture
def saved_session(store):
    data = GarminSessionData(username="athlete@example.com", token_json=SAMPLE_TOKEN_JSON)
    store.save(data)
    return data


@pytest.fixture
def client(store, saved_session):
    return GarminClient(store)


# ── GarminSessionStore ─────────────────────────────────────────────────────────

def test_session_save_and_load(store, saved_session):
    loaded = store.load()
    assert loaded.username == "athlete@example.com"
    assert loaded.token_json == SAMPLE_TOKEN_JSON


def test_session_exists_false_when_missing(store):
    assert not store.exists()


def test_session_exists_true_after_save(store, saved_session):
    assert store.exists()


def test_session_load_missing_raises(store):
    with pytest.raises(GarminNotConnectedError):
        store.load()


def test_session_delete(store, saved_session):
    store.delete()
    assert not store.exists()


def test_session_delete_noop_when_missing(store):
    store.delete()  # must not raise


def test_session_file_permissions(store, saved_session):
    mode = store._path.stat().st_mode
    assert not (mode & stat.S_IRGRP)
    assert not (mode & stat.S_IROTH)


def test_session_encrypted_on_disk(store, saved_session):
    raw = store._path.read_bytes()
    assert b"athlete@example.com" not in raw
    assert b"di_token" not in raw


# ── extract_session / restore_session ─────────────────────────────────────────

def test_extract_session_calls_dumps():
    mock_api = MagicMock()
    mock_api.client.dumps.return_value = SAMPLE_TOKEN_JSON
    result = extract_session(mock_api)
    assert result == SAMPLE_TOKEN_JSON
    mock_api.client.dumps.assert_called_once()


def test_restore_session_calls_loads():
    mock_api = MagicMock()
    restore_session(mock_api, SAMPLE_TOKEN_JSON)
    mock_api.client.loads.assert_called_once_with(SAMPLE_TOKEN_JSON)



# ── GarminClient ──────────────────────────────────────────────────────────────

@patch("claude2strava.garmin.client.Garmin")
def test_check_connection_success(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_user_profile.return_value = {"displayName": "Tobias", "userId": 42}
    MockGarmin.return_value = mock_api

    result = client.check_connection()
    assert result["displayName"] == "Tobias"
    mock_api.client.loads.assert_called_once_with(SAMPLE_TOKEN_JSON)


@patch("claude2strava.garmin.client.Garmin")
def test_check_connection_no_session(MockGarmin, store):
    empty_client = GarminClient(store)  # store has no session saved
    with pytest.raises(GarminNotConnectedError):
        empty_client.check_connection()


@patch("claude2strava.garmin.client.Garmin")
def test_restore_failure_raises_session_expired(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.client.loads.side_effect = Exception("invalid token")
    MockGarmin.return_value = mock_api

    with pytest.raises(GarminSessionExpiredError):
        client.check_connection()


@patch("claude2strava.garmin.client.Garmin")
def test_get_sleep_returns_dict(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 25200,
            "deepSleepSeconds": 5400,
            "avgOvernightHrv": 55.3,
        }
    }
    MockGarmin.return_value = mock_api

    result = client.get_sleep("2024-01-15")
    assert result["dailySleepDTO"]["sleepTimeSeconds"] == 25200
    mock_api.get_sleep_data.assert_called_once_with("2024-01-15")


@patch("claude2strava.garmin.client.Garmin")
def test_get_hrv_returns_dict(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_hrv_data.return_value = {
        "hrvSummary": {"lastNight": 55, "status": "BALANCED"},
        "hrvReadings": [{"hrvValue": 56}],
    }
    MockGarmin.return_value = mock_api

    result = client.get_hrv("2024-01-15")
    assert result["hrvSummary"]["status"] == "BALANCED"


@patch("claude2strava.garmin.client.Garmin")
def test_get_daily_stats_returns_dict(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_stats.return_value = {
        "totalSteps": 8500,
        "restingHeartRate": 52,
        "bodyBatteryMostRecentValue": 78,
    }
    MockGarmin.return_value = mock_api

    result = client.get_daily_stats("2024-01-15")
    assert result["totalSteps"] == 8500


@patch("claude2strava.garmin.client.Garmin")
def test_get_body_battery_returns_list(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_body_battery.return_value = [
        {"calendarDate": "2024-01-14", "charged": 45, "drained": 20, "endOfDayValue": 65},
        {"calendarDate": "2024-01-15", "charged": 38, "drained": 25, "endOfDayValue": 78},
    ]
    MockGarmin.return_value = mock_api

    result = client.get_body_battery("2024-01-14", "2024-01-15")
    assert len(result) == 2
    assert result[0]["charged"] == 45


@patch("claude2strava.garmin.client.Garmin")
def test_session_expired_raises(MockGarmin, client):
    from garminconnect import GarminConnectAuthenticationError
    mock_api = MagicMock()
    mock_api.get_user_profile.side_effect = GarminConnectAuthenticationError("expired")
    MockGarmin.return_value = mock_api

    with pytest.raises(GarminSessionExpiredError):
        client.check_connection()


@patch("claude2strava.garmin.client.Garmin")
def test_get_sleep_range_multi_day(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 25200,
            "avgOvernightHrv": 55.0,
        }
    }
    MockGarmin.return_value = mock_api

    result = client.get_sleep_range("2024-01-13", "2024-01-15")
    assert len(result) == 3
    assert result[0]["date"] == "2024-01-13"
    assert result[2]["date"] == "2024-01-15"
    assert result[0]["avg_hrv"] == 55.0


@patch("claude2strava.garmin.client.Garmin")
def test_get_weight_single_day(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_body_composition.return_value = {
        "dateWeightList": [
            {
                "calendarDate": "2024-01-15",
                "weight": 80500,      # grams
                "bmi": 24.1,
                "bodyFat": 18.5,
                "muscleMass": 62000,  # grams
                "boneMass": 3200,     # grams
                "bodyWater": 58.2,
            }
        ]
    }
    MockGarmin.return_value = mock_api

    result = client.get_weight("2024-01-15")

    mock_api.get_body_composition.assert_called_once_with("2024-01-15", "2024-01-15")
    assert result["date"] == "2024-01-15"
    assert result["weight_kg"] == pytest.approx(80.5)
    assert result["bmi"] == pytest.approx(24.1)
    assert result["body_fat_pct"] == pytest.approx(18.5)
    assert result["muscle_mass_kg"] == pytest.approx(62.0)
    assert result["bone_mass_kg"] == pytest.approx(3.2)
    assert result["body_water_pct"] == pytest.approx(58.2)


@patch("claude2strava.garmin.client.Garmin")
def test_get_weight_no_data(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_body_composition.return_value = {"dateWeightList": []}
    MockGarmin.return_value = mock_api

    result = client.get_weight("2024-01-15")
    assert result == {"date": "2024-01-15", "weight_kg": None}


@patch("claude2strava.garmin.client.Garmin")
def test_get_weight_range(MockGarmin, client):
    mock_api = MagicMock()
    mock_api.get_body_composition.return_value = {
        "dateWeightList": [
            {"calendarDate": "2024-01-13", "weight": 81000, "bmi": 24.3,
             "bodyFat": 19.0, "muscleMass": 62000, "boneMass": 3200, "bodyWater": 57.5},
            {"calendarDate": "2024-01-14", "weight": 80800, "bmi": 24.2,
             "bodyFat": 18.8, "muscleMass": 62100, "boneMass": 3200, "bodyWater": 57.8},
        ]
    }
    MockGarmin.return_value = mock_api

    result = client.get_weight_range("2024-01-13", "2024-01-14")

    mock_api.get_body_composition.assert_called_once_with("2024-01-13", "2024-01-14")
    assert len(result) == 2
    assert result[0]["date"] == "2024-01-13"
    assert result[0]["weight_kg"] == pytest.approx(81.0)
    assert result[1]["date"] == "2024-01-14"
    assert result[1]["weight_kg"] == pytest.approx(80.8)
