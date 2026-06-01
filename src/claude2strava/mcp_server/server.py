"""
RunCoach MCP server — exposes Strava and Garmin Connect data to Claude.

Add to claude_desktop_config.json:
  {
    "mcpServers": {
      "runcoach": {
        "command": "uv",
        "args": ["run", "python", "-m", "claude2strava.mcp_server"],
        "cwd": "/path/to/Claude2Strava"
      }
    }
  }
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..config import get_settings
from ..crypto import Crypto
from ..garmin.client import GarminClient
from ..garmin.session_store import GarminNotConnectedError, GarminSessionExpiredError, GarminSessionStore
from ..strava.client import StravaClient
from ..token_store import TokenNotFoundError, TokenStore

mcp = FastMCP("RunCoach MCP", instructions=(
    "Access the user's Strava workout history and Garmin Connect wellness data. "
    "Strava: use list_activities, get_activity, get_activity_streams, get_athlete_stats. "
    "Garmin: use get_garmin_sleep, get_garmin_hrv, get_garmin_daily_stats, "
    "get_garmin_body_battery, get_garmin_sleep_range for HRV, sleep and recovery data. "
    "Use get_garmin_weight / get_garmin_weight_range for body weight and composition data. "
    "Use get_garmin_user_profile for age, height, and baseline user info. "
    "Combine both sources for complete training + recovery analysis."
))

# Module-level singletons — built on first call
_strava_client: StravaClient | None = None
_garmin_client: GarminClient | None = None


def _get_strava() -> StravaClient:
    global _strava_client
    if _strava_client is None:
        settings = get_settings()
        crypto = Crypto(settings.get_fernet_key())
        store = TokenStore(settings.token_dir, crypto)
        _strava_client = StravaClient(store, settings.strava_client_id, settings.strava_client_secret)
    return _strava_client


def _get_garmin() -> GarminClient:
    global _garmin_client
    if _garmin_client is None:
        settings = get_settings()
        crypto = Crypto(settings.get_fernet_key())
        garmin_store = GarminSessionStore(settings.token_dir, crypto)
        _garmin_client = GarminClient(garmin_store)
    return _garmin_client


def _get_client() -> StravaClient:  # legacy alias used inside the Strava tools below
    return _get_strava()


def _ts(dt_str: str | None) -> int | None:
    """Parse an ISO date string like '2024-01-15' to a Unix timestamp."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def check_connection() -> dict:
    """
    Verify that Strava is connected and return the authenticated athlete's profile.
    Call this first to confirm the MCP server can reach Strava.
    """
    try:
        client = _get_client()
        athlete = await client.get_athlete()
        return {
            "connected": True,
            "athlete_id": athlete["id"],
            "name": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
            "username": athlete.get("username"),
            "city": athlete.get("city"),
            "country": athlete.get("country"),
            "follower_count": athlete.get("follower_count"),
            "following_count": athlete.get("friend_count"),
        }
    except TokenNotFoundError as exc:
        return {"connected": False, "error": str(exc)}


@mcp.tool()
async def list_activities(
    after: Optional[str] = None,
    before: Optional[str] = None,
    activity_type: Optional[str] = None,
    limit: int = 30,
    page: int = 1,
) -> list[dict]:
    """
    List the athlete's activities with optional filters.

    Parameters
    ----------
    after : ISO date string (e.g. "2024-01-01") — only return activities after this date
    before : ISO date string — only return activities before this date
    activity_type : filter by type, e.g. "Run", "Ride", "Swim", "VirtualRide", "Walk"
    limit : max activities to return (1-200, default 30)
    page : page number for pagination (default 1)
    """
    client = _get_client()
    activities = await client.list_activities(
        after=_ts(after),
        before=_ts(before),
        activity_type=activity_type,
        limit=limit,
        page=page,
    )
    return [
        {
            "id": a["id"],
            "name": a.get("name"),
            "type": a.get("type"),
            "start_date": a.get("start_date_local"),
            "distance_m": a.get("distance"),
            "distance_km": round(a.get("distance", 0) / 1000, 2),
            "moving_time_s": a.get("moving_time"),
            "elapsed_time_s": a.get("elapsed_time"),
            "total_elevation_gain_m": a.get("total_elevation_gain"),
            "average_speed_ms": a.get("average_speed"),
            "max_speed_ms": a.get("max_speed"),
            "average_heartrate": a.get("average_heartrate"),
            "max_heartrate": a.get("max_heartrate"),
            "average_watts": a.get("average_watts"),
            "kudos_count": a.get("kudos_count"),
            "trainer": a.get("trainer"),
            "commute": a.get("commute"),
        }
        for a in activities
    ]


@mcp.tool()
async def get_activity(activity_id: int) -> dict:
    """
    Get full detail for a single activity including splits, best efforts, and gear.

    Parameters
    ----------
    activity_id : the Strava activity ID (from list_activities)
    """
    client = _get_client()
    a = await client.get_activity(activity_id)
    return {
        "id": a["id"],
        "name": a.get("name"),
        "description": a.get("description"),
        "type": a.get("type"),
        "sport_type": a.get("sport_type"),
        "start_date": a.get("start_date_local"),
        "timezone": a.get("timezone"),
        "distance_m": a.get("distance"),
        "distance_km": round(a.get("distance", 0) / 1000, 2),
        "moving_time_s": a.get("moving_time"),
        "elapsed_time_s": a.get("elapsed_time"),
        "total_elevation_gain_m": a.get("total_elevation_gain"),
        "elev_high_m": a.get("elev_high"),
        "elev_low_m": a.get("elev_low"),
        "average_speed_ms": a.get("average_speed"),
        "max_speed_ms": a.get("max_speed"),
        "average_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "average_cadence": a.get("average_cadence"),
        "average_watts": a.get("average_watts"),
        "weighted_average_watts": a.get("weighted_average_watts"),
        "max_watts": a.get("max_watts"),
        "kilojoules": a.get("kilojoules"),
        "suffer_score": a.get("suffer_score"),
        "calories": a.get("calories"),
        "perceived_exertion": a.get("perceived_exertion"),
        "splits_metric": a.get("splits_metric", []),
        "best_efforts": [
            {
                "name": e.get("name"),
                "distance_m": e.get("distance"),
                "elapsed_time_s": e.get("elapsed_time"),
                "moving_time_s": e.get("moving_time"),
            }
            for e in (a.get("best_efforts") or [])
        ],
        "gear": a.get("gear"),
        "device_name": a.get("device_name"),
        "has_heartrate": a.get("has_heartrate"),
        "has_power": a.get("device_watts", False),
        "map_summary_polyline": a.get("map", {}).get("summary_polyline"),
    }


@mcp.tool()
async def get_activity_streams(
    activity_id: int,
    keys: Optional[list[str]] = None,
) -> dict:
    """
    Get time-series data streams for an activity (heart rate, pace, altitude, power, etc.).

    Parameters
    ----------
    activity_id : Strava activity ID
    keys : list of stream types to fetch. Defaults to all available:
           ["time", "distance", "heartrate", "cadence", "altitude", "watts", "velocity_smooth"]
    """
    client = _get_client()
    streams = await client.get_activity_streams(activity_id, keys)
    result = {}
    for key, stream in streams.items():
        if isinstance(stream, dict):
            result[key] = {
                "data": stream.get("data", []),
                "series_type": stream.get("series_type"),
                "resolution": stream.get("resolution"),
            }
    return result


@mcp.tool()
async def get_athlete_stats() -> dict:
    """
    Get the authenticated athlete's cumulative stats: YTD, all-time, and recent totals
    for runs, rides, and swims.
    """
    client = _get_client()
    token_data = client._store.load()
    stats = await client.get_athlete_stats(token_data.athlete_id)

    def fmt(s: dict) -> dict:
        return {
            "count": s.get("count"),
            "distance_km": round(s.get("distance", 0) / 1000, 2),
            "moving_time_hours": round(s.get("moving_time", 0) / 3600, 2),
            "elevation_gain_m": s.get("elevation_gain"),
            "achievement_count": s.get("achievement_count"),
        }

    return {
        "ytd_run": fmt(stats.get("ytd_run_totals", {})),
        "ytd_ride": fmt(stats.get("ytd_ride_totals", {})),
        "ytd_swim": fmt(stats.get("ytd_swim_totals", {})),
        "all_run": fmt(stats.get("all_run_totals", {})),
        "all_ride": fmt(stats.get("all_ride_totals", {})),
        "all_swim": fmt(stats.get("all_swim_totals", {})),
        "recent_run": fmt(stats.get("recent_run_totals", {})),
        "recent_ride": fmt(stats.get("recent_ride_totals", {})),
        "recent_swim": fmt(stats.get("recent_swim_totals", {})),
        "biggest_ride_distance_km": round(stats.get("biggest_ride_distance", 0) / 1000, 2),
        "biggest_climb_elevation_gain_m": stats.get("biggest_climb_elevation_gain"),
    }


@mcp.tool()
async def get_athlete_zones() -> dict:
    """
    Get the athlete's configured heart rate and power training zones.
    """
    client = _get_client()
    return await client.get_athlete_zones()


@mcp.tool()
async def get_starred_segments() -> list[dict]:
    """
    Get the athlete's starred Strava segments (favourite routes/climbs).
    """
    client = _get_client()
    segments = await client.get_starred_segments()
    return [
        {
            "id": s["id"],
            "name": s.get("name"),
            "activity_type": s.get("activity_type"),
            "distance_m": s.get("distance"),
            "distance_km": round(s.get("distance", 0) / 1000, 2),
            "average_grade": s.get("average_grade"),
            "maximum_grade": s.get("maximum_grade"),
            "elevation_high_m": s.get("elevation_high"),
            "elevation_low_m": s.get("elevation_low"),
            "total_elevation_gain_m": s.get("total_elevation_gain"),
            "city": s.get("city"),
            "country": s.get("country"),
            "climb_category": s.get("climb_category"),
            "athlete_count": s.get("athlete_count"),
            "effort_count": s.get("effort_count"),
        }
        for s in segments
    ]


# ── Garmin Connect Tools ───────────────────────────────────────────────────────

@mcp.tool()
async def check_garmin_connection() -> dict:
    """
    Verify that Garmin Connect is linked and the session is still valid.
    Returns the Garmin display name on success.
    Call this first before using other Garmin tools.
    """
    try:
        client = _get_garmin()
        profile = await asyncio.get_event_loop().run_in_executor(
            None, client.check_connection
        )
        return {
            "connected": True,
            "display_name": profile.get("displayName") or profile.get("userName", ""),
            "user_id": profile.get("userId"),
        }
    except GarminNotConnectedError as exc:
        return {"connected": False, "error": str(exc)}
    except GarminSessionExpiredError as exc:
        return {"connected": False, "error": str(exc)}


@mcp.tool()
async def get_garmin_sleep(date: str) -> dict:
    """
    Get detailed sleep data for a specific date from Garmin Connect.

    Parameters
    ----------
    date : calendar date in "YYYY-MM-DD" format

    Returns sleep stages (deep / light / REM / awake) in seconds,
    overnight HRV average, SpO2, breathing rate, and body battery change.
    """
    client = _get_garmin()
    raw = await asyncio.get_event_loop().run_in_executor(
        None, client.get_sleep, date
    )
    dto = raw.get("dailySleepDTO", {}) if isinstance(raw, dict) else {}
    return {
        "date": date,
        "total_sleep_seconds": dto.get("sleepTimeSeconds"),
        "total_sleep_hours": round(dto.get("sleepTimeSeconds", 0) / 3600, 2),
        "deep_seconds": dto.get("deepSleepSeconds"),
        "light_seconds": dto.get("lightSleepSeconds"),
        "rem_seconds": dto.get("remSleepSeconds"),
        "awake_seconds": dto.get("awakeSleepSeconds"),
        "avg_overnight_hrv": dto.get("avgOvernightHrv"),
        "avg_spo2": dto.get("averageSpO2Value"),
        "avg_respiration_bpm": dto.get("averageRespirationValue"),
        "body_battery_change": dto.get("bodyBatteryChange"),
        "sleep_start_utc": dto.get("sleepStartTimestampGMT"),
        "sleep_end_utc": dto.get("sleepEndTimestampGMT"),
    }


@mcp.tool()
async def get_garmin_hrv(date: str) -> dict:
    """
    Get overnight HRV (Heart Rate Variability) data for a specific date.

    Parameters
    ----------
    date : calendar date in "YYYY-MM-DD" format

    Returns last-night HRV average, weekly average, 5-minute sample readings,
    HRV status (BALANCED / UNBALANCED / POOR / NO_DATA), and baseline range.
    Higher HRV generally indicates better recovery readiness.
    """
    client = _get_garmin()
    raw = await asyncio.get_event_loop().run_in_executor(
        None, client.get_hrv, date
    )
    summary = raw.get("hrvSummary", {}) if isinstance(raw, dict) else {}
    readings = raw.get("hrvReadings", []) if isinstance(raw, dict) else []
    baseline = summary.get("baseline", {})
    return {
        "date": date,
        "last_night_avg": summary.get("lastNight"),
        "weekly_avg": summary.get("weeklyAvg"),
        "last_5_nights_avg": summary.get("lastFive"),
        "status": summary.get("status"),          # BALANCED / UNBALANCED / POOR / NO_DATA
        "baseline_low": baseline.get("lowUpper"),
        "baseline_balanced_low": baseline.get("balancedLow"),
        "baseline_balanced_high": baseline.get("balancedUpper"),
        "sample_count": len(readings),
        "readings_ms": [r.get("hrvValue") for r in readings if r.get("hrvValue")],
    }


@mcp.tool()
async def get_garmin_daily_stats(date: str) -> dict:
    """
    Get the daily wellness summary for a specific date from Garmin Connect.

    Parameters
    ----------
    date : calendar date in "YYYY-MM-DD" format

    Returns steps, resting heart rate, body battery (start/end/max/min),
    average stress level, active calories, floors climbed, and intensity minutes.
    """
    client = _get_garmin()
    raw = await asyncio.get_event_loop().run_in_executor(
        None, client.get_daily_stats, date
    )
    if not isinstance(raw, dict):
        return {"date": date, "error": "No data available"}
    return {
        "date": date,
        "total_steps": raw.get("totalSteps"),
        "step_goal": raw.get("dailyStepGoal"),
        "resting_heart_rate": raw.get("restingHeartRate"),
        "min_heart_rate": raw.get("minHeartRate"),
        "max_heart_rate": raw.get("maxHeartRate"),
        "avg_stress_level": raw.get("averageStressLevel"),
        "max_stress_level": raw.get("maxStressLevel"),
        "body_battery_most_recent": raw.get("bodyBatteryMostRecentValue"),
        "body_battery_highest": raw.get("bodyBatteryHighestValue"),
        "body_battery_lowest": raw.get("bodyBatteryLowestValue"),
        "total_kilocalories": raw.get("totalKilocalories"),
        "active_kilocalories": raw.get("activeKilocalories"),
        "floors_climbed": raw.get("floorsAscended"),
        "moderate_intensity_minutes": raw.get("moderateIntensityMinutes"),
        "vigorous_intensity_minutes": raw.get("vigorousIntensityMinutes"),
    }


@mcp.tool()
async def get_garmin_body_battery(start_date: str, end_date: str) -> list[dict]:
    """
    Get Body Battery readings across a date range.

    Parameters
    ----------
    start_date / end_date : "YYYY-MM-DD"

    Body Battery (0–100) reflects energy reserves. Use this to track
    recovery trends over a training block or recovery week.
    """
    client = _get_garmin()
    raw = await asyncio.get_event_loop().run_in_executor(
        None, client.get_body_battery, start_date, end_date
    )
    if not isinstance(raw, list):
        return []
    results = []
    for entry in raw:
        if isinstance(entry, dict):
            results.append({
                "date": entry.get("calendarDate") or entry.get("date"),
                "charged": entry.get("charged"),
                "drained": entry.get("drained"),
                "end_of_day": entry.get("endOfDayValue"),
                "start_of_day": entry.get("startOfDayValue"),
            })
    return results


@mcp.tool()
async def get_garmin_weight(date: str) -> dict:
    """
    Get body composition / weight data from Garmin Connect for a single day.

    Parameters
    ----------
    date : "YYYY-MM-DD"

    Returns weight_kg, BMI, body_fat_pct, muscle_mass_kg, bone_mass_kg,
    body_water_pct. Fields are None when no measurement exists for that day.
    """
    client = _get_garmin()
    return await asyncio.get_event_loop().run_in_executor(
        None, client.get_weight, date
    )


@mcp.tool()
async def get_garmin_weight_range(start_date: str, end_date: str) -> list[dict]:
    """
    Get body composition / weight data from Garmin Connect for a date range.
    Useful for tracking weight trends over a training block or diet phase.

    Parameters
    ----------
    start_date / end_date : "YYYY-MM-DD"

    Returns one entry per day that has a recorded measurement, each with
    weight_kg, BMI, body_fat_pct, muscle_mass_kg, bone_mass_kg, body_water_pct.
    """
    client = _get_garmin()
    return await asyncio.get_event_loop().run_in_executor(
        None, client.get_weight_range, start_date, end_date
    )


@mcp.tool()
async def get_garmin_user_profile() -> dict:
    """
    Get basic user profile data from Garmin Connect.

    Returns display_name, age, height_cm, weight_kg (last recorded), gender,
    and birth_date. Useful as baseline context for training load calculations,
    VO2max estimates, and health metric interpretation.
    """
    client = _get_garmin()
    return await asyncio.get_event_loop().run_in_executor(
        None, client.get_user_profile
    )


@mcp.tool()
async def get_garmin_sleep_range(start_date: str, end_date: str) -> list[dict]:
    """
    Get a sleep summary for each night in a date range.
    Useful for analysing sleep quality trends over a training block.

    Parameters
    ----------
    start_date / end_date : "YYYY-MM-DD"

    Returns one entry per night with sleep duration, stage breakdown,
    overnight HRV, SpO2, respiration rate and body battery change.
    """
    client = _get_garmin()
    return await asyncio.get_event_loop().run_in_executor(
        None, client.get_sleep_range, start_date, end_date
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
