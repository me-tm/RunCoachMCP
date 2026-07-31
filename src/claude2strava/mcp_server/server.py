"""
RunCoach MCP server — exposes Garmin Connect data to Claude.

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

from mcp.server.fastmcp import FastMCP

from ..config import get_settings
from ..crypto import Crypto
from ..garmin.client import GarminClient
from ..garmin.session_store import GarminNotConnectedError, GarminSessionExpiredError, GarminSessionStore

mcp = FastMCP("RunCoach MCP", instructions=(
    "Access the user's Garmin Connect wellness data. "
    "Use get_garmin_sleep, get_garmin_hrv, get_garmin_daily_stats, "
    "get_garmin_body_battery, get_garmin_sleep_range for HRV, sleep and recovery data. "
    "Use get_garmin_weight / get_garmin_weight_range for body weight and composition data. "
    "Use get_garmin_user_profile for age, height, and baseline user info."
))

# Module-level singleton — built on first call
_garmin_client: GarminClient | None = None


def _get_garmin() -> GarminClient:
    global _garmin_client
    if _garmin_client is None:
        settings = get_settings()
        crypto = Crypto(settings.get_fernet_key())
        garmin_store = GarminSessionStore(settings.token_dir, crypto)
        _garmin_client = GarminClient(garmin_store)
    return _garmin_client


# ── Garmin Connect Tools ───────────────────────────────────────────────────────

@mcp.tool()
async def check_garmin_connection() -> dict:
    """
    Verify that Garmin Connect is linked and the session is still valid.
    Returns the athlete's name on success (display_name is Garmin's internal
    profile ID, not a readable name).
    Call this first before using other Garmin tools.
    """
    try:
        client = _get_garmin()
        profile = await asyncio.get_event_loop().run_in_executor(
            None, client.check_connection
        )
        return {
            "connected": True,
            "name": profile.get("full_name") or profile.get("display_name", ""),
            "display_name": profile.get("display_name"),   # Garmin-internal profile ID
            "user_id": profile.get("user_id"),
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

    Returns full_name, age, height_cm, weight_kg (last recorded), gender,
    and birth_date (display_name is Garmin's internal profile ID).
    Useful as baseline context for training load calculations,
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
