"""
Garmin Connect client — wraps the `garminconnect` library (v0.3.x).

garminconnect is synchronous (curl_cffi/requests internally).
In the async MCP server all calls are dispatched via asyncio.run_in_executor.

Token format (garminconnect 0.3.x)
------------------------------------
After login, api.client.dumps() returns a JSON string:
    {"di_token": "...", "di_refresh_token": "...", "di_client_id": "..."}

We encrypt this string and persist it via GarminSessionStore.
On restore, api.client.loads(token_json) re-hydrates the session.

MFA flow (when Garmin triggers 2FA)
------------------------------------
1. login() with return_on_mfa=True → returns (mfa_state_dict, _)
2. Store mfa_state_dict server-side (in-memory, per session_id cookie)
3. User submits 2FA code → resume_login(mfa_state_dict, code)
4. Persist tokens → done

Display name
------------------------------------
garminconnect sets api.display_name only inside login()/resume_login().
Restoring a session via client.loads() leaves it None, which breaks every
endpoint that interpolates it into the URL — get_stats() raises
"Display name is not set", get_sleep_data() requests .../None and gets a 403.
We therefore persist the display name with the session and re-attach it on
every restore (fetching it once from the social profile if it is missing).
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .session_store import (
    GarminNotConnectedError,
    GarminSessionData,
    GarminSessionExpiredError,
    GarminSessionStore,
)


# Garmin endpoint holding displayName / fullName of the logged-in user
SOCIAL_PROFILE_PATH = "/userprofile-service/socialProfile"


# ── Auth helpers (called by the web UI routes) ─────────────────────────────────

def login(email: str, password: str) -> tuple["Garmin", bool, Any]:
    """
    Attempt a Garmin Connect login.

    Returns
    -------
    (api, needs_mfa, mfa_state)
        needs_mfa  – True when Garmin triggered a 2FA challenge
        mfa_state  – opaque dict to pass to resume_login(); None if no MFA
    """
    api = Garmin(email=email, password=password, return_on_mfa=True)
    mfa_status, _ = api.login()   # always returns a 2-tuple in 0.3.x
    needs_mfa = mfa_status is not None
    return api, needs_mfa, mfa_status


def complete_mfa(api: "Garmin", mfa_state: Any, mfa_code: str) -> None:
    """Complete a pending 2FA challenge via resume_login."""
    api.resume_login(mfa_state, mfa_code)


def extract_session(api: "Garmin") -> str:
    """Return the token JSON string (di_token + refresh + client_id) for storage."""
    return api.client.dumps()


def restore_session(api: "Garmin", token_json: str) -> None:
    """Re-hydrate a Garmin API instance from stored token JSON."""
    api.client.loads(token_json)


def fetch_profile_names(api: "Garmin") -> tuple[str | None, str | None]:
    """Read (displayName, fullName) from the social profile endpoint."""
    profile = api.connectapi(SOCIAL_PROFILE_PATH)
    if not isinstance(profile, dict):
        return None, None
    return profile.get("displayName"), profile.get("fullName")


def profile_names(api: "Garmin") -> tuple[str | None, str | None]:
    """
    (displayName, fullName) of a freshly logged-in api instance.

    login()/resume_login() normally set both; fall back to the social profile
    endpoint so the caller can persist them with the session.
    """
    display_name = getattr(api, "display_name", None)
    full_name = getattr(api, "full_name", None)
    if display_name:
        return display_name, full_name
    try:
        return fetch_profile_names(api)
    except Exception:
        return None, None


# ── Client ─────────────────────────────────────────────────────────────────────

class GarminClient:
    def __init__(self, session_store: GarminSessionStore) -> None:
        self._store = session_store

    def _get_api(self) -> "Garmin":
        """
        Create a Garmin API instance restored from encrypted stored tokens.

        Raises GarminNotConnectedError  – no session on disk
        Raises GarminSessionExpiredError – stored tokens rejected by Garmin
        """
        session_data = self._store.load()   # raises GarminNotConnectedError
        api = Garmin()                      # no credentials needed on restore
        try:
            restore_session(api, session_data.token_json)
        except Exception as exc:
            raise GarminSessionExpiredError(
                "Garmin-Session ungültig — bitte über http://localhost:8080 neu verbinden."
            ) from exc
        self._attach_display_name(api, session_data)
        return api

    def _attach_display_name(self, api: "Garmin", session_data: GarminSessionData) -> None:
        """
        Restore api.display_name / api.full_name after a token-only restore.

        Without this, get_stats() and get_sleep_data() build URLs from a display
        name that is None. Sessions stored before this fix carry no display name,
        so we fetch it once from the social profile and write it back.
        """
        display_name = session_data.display_name
        full_name = session_data.full_name

        if not display_name:
            display_name, full_name = fetch_profile_names(api)
            if display_name:
                self._store.save(
                    replace(session_data, display_name=display_name, full_name=full_name)
                )

        if not display_name:
            raise GarminSessionExpiredError(
                "Garmin-Anzeigename konnte nicht ermittelt werden — bitte über "
                "http://localhost:8080 neu verbinden."
            )

        api.display_name = display_name
        api.full_name = full_name

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        return self._invoke(lambda api: getattr(api, method_name)(*args, **kwargs))

    def _invoke(self, fn: Callable[["Garmin"], Any]) -> Any:
        """Run fn against a restored api instance, translating Garmin errors."""
        try:
            api = self._get_api()
            return fn(api)
        except (GarminNotConnectedError, GarminSessionExpiredError):
            raise
        except GarminConnectAuthenticationError as exc:
            raise GarminSessionExpiredError(
                "Garmin-Session abgelaufen — bitte über http://localhost:8080 neu verbinden."
            ) from exc
        except GarminConnectTooManyRequestsError as exc:
            raise RuntimeError("Garmin rate limit erreicht — bitte kurz warten.") from exc
        except GarminConnectConnectionError as exc:
            raise RuntimeError(f"Garmin-Verbindungsfehler: {exc}") from exc

    # ── Public data methods (synchronous) ──────────────────────────────────────

    def check_connection(self) -> dict:
        """
        Verify the stored session is valid by fetching the social profile.
        Returns display_name, full_name and user_id.
        """
        def _fetch(api: "Garmin") -> dict:
            profile = api.connectapi(SOCIAL_PROFILE_PATH)
            profile = profile if isinstance(profile, dict) else {}
            return {
                "display_name": api.display_name,
                "full_name":    api.full_name or profile.get("fullName"),
                "user_id":      profile.get("userProfileId") or profile.get("profileId"),
            }

        return self._invoke(_fetch)

    def get_sleep(self, cdate: str) -> dict:
        """
        Sleep data for a calendar date (YYYY-MM-DD).
        Includes stages (deep/light/REM/awake), HRV status, SpO2, respiration.
        """
        return self._call("get_sleep_data", cdate)

    def get_hrv(self, cdate: str) -> dict:
        """
        Overnight HRV data for a calendar date.
        Returns last-night average, weekly avg, 5-min samples, status, baseline.
        """
        return self._call("get_hrv_data", cdate)

    def get_daily_stats(self, cdate: str) -> dict:
        """
        Daily wellness summary: steps, resting HR, body battery, stress, calories.
        """
        return self._call("get_stats", cdate)

    def get_body_battery(self, start_date: str, end_date: str) -> list:
        """Body battery readings across a date range (YYYY-MM-DD to YYYY-MM-DD)."""
        return self._call("get_body_battery", start_date, end_date)

    def get_weight(self, cdate: str) -> dict:
        """
        Body composition / weight data for a single calendar date (YYYY-MM-DD).
        Returns weight (kg), BMI, body fat %, muscle mass, bone mass, body water %.
        """
        raw = self._call("get_body_composition", cdate, cdate)
        entries = raw.get("dateWeightList", []) if isinstance(raw, dict) else []
        if not entries:
            return {"date": cdate, "weight_kg": None}
        entry = entries[0]
        return {
            "date":              cdate,
            "weight_kg":         entry.get("weight") / 1000 if entry.get("weight") is not None else None,
            "bmi":               entry.get("bmi"),
            "body_fat_pct":      entry.get("bodyFat"),
            "muscle_mass_kg":    entry.get("muscleMass") / 1000 if entry.get("muscleMass") is not None else None,
            "bone_mass_kg":      entry.get("boneMass") / 1000 if entry.get("boneMass") is not None else None,
            "body_water_pct":    entry.get("bodyWater"),
        }

    def get_weight_range(self, start_date: str, end_date: str) -> list[dict]:
        """
        Body composition / weight data for a date range (YYYY-MM-DD to YYYY-MM-DD).
        Returns one entry per day that has a measurement.
        """
        raw = self._call("get_body_composition", start_date, end_date)
        entries = raw.get("dateWeightList", []) if isinstance(raw, dict) else []
        results = []
        for entry in entries:
            cdate = entry.get("calendarDate") or entry.get("date")
            results.append({
                "date":           cdate,
                "weight_kg":      entry.get("weight") / 1000 if entry.get("weight") is not None else None,
                "bmi":            entry.get("bmi"),
                "body_fat_pct":   entry.get("bodyFat"),
                "muscle_mass_kg": entry.get("muscleMass") / 1000 if entry.get("muscleMass") is not None else None,
                "bone_mass_kg":   entry.get("boneMass") / 1000 if entry.get("boneMass") is not None else None,
                "body_water_pct": entry.get("bodyWater"),
            })
        return results

    def get_user_profile(self) -> dict:
        """
        Fetch basic user profile data: age, height, weight, gender, birthdate.
        Sourced from /userprofile-service/userprofile/user-settings → userData.
        """
        # user-settings has no displayName — that lives on the social profile,
        # which _get_api() has already attached to the api instance.
        def _fetch(api: "Garmin") -> tuple[str | None, str | None, Any]:
            return api.display_name, api.full_name, api.get_user_profile()

        display_name, full_name, raw = self._invoke(_fetch)
        user_data = raw.get("userData", {}) if isinstance(raw, dict) else {}
        return {
            "display_name":  display_name,   # Garmin-internal ID used in API URLs
            "full_name":     full_name,      # human-readable name
            "age":           user_data.get("age"),
            "height_cm":     user_data.get("height"),
            "weight_kg":     user_data.get("weight") / 1000 if user_data.get("weight") is not None else None,
            "gender":        user_data.get("gender"),
            "birth_date":    user_data.get("birthDate"),
        }

    def get_sleep_range(self, start_date: str, end_date: str) -> list[dict]:
        """
        Collect key sleep metrics day-by-day for a date range.
        Returns one entry per night with duration, stages, HRV, SpO2.
        """
        results = []
        current = date.fromisoformat(start_date)
        end     = date.fromisoformat(end_date)
        while current <= end:
            cdate = current.isoformat()
            try:
                raw = self._call("get_sleep_data", cdate)
                dto = raw.get("dailySleepDTO", {}) if isinstance(raw, dict) else {}
                results.append({
                    "date":                  cdate,
                    "total_sleep_seconds":   dto.get("sleepTimeSeconds"),
                    "deep_seconds":          dto.get("deepSleepSeconds"),
                    "light_seconds":         dto.get("lightSleepSeconds"),
                    "rem_seconds":           dto.get("remSleepSeconds"),
                    "awake_seconds":         dto.get("awakeSleepSeconds"),
                    "avg_hrv":               dto.get("avgOvernightHrv"),
                    "avg_spo2":              dto.get("averageSpO2Value"),
                    "avg_respiration":       dto.get("averageRespirationValue"),
                    "body_battery_change":   dto.get("bodyBatteryChange"),
                })
            except (GarminSessionExpiredError, GarminNotConnectedError, RuntimeError):
                raise
            except Exception as exc:
                # Report the real cause instead of masking every failure as "no data"
                results.append({"date": cdate, "error": f"{type(exc).__name__}: {exc}"})
            current += timedelta(days=1)
        return results
