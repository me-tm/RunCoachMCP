"""
COROS Open API async client.

All requests are authenticated via Bearer token. Access tokens are
auto-refreshed when within 60 seconds of expiry.

API base: https://open.coros.com
"""

import time
from typing import Any

import httpx

from .auth import CorosAuthError, refresh_access_token
from .token_store import CorosNotConnectedError, CorosTokenData, CorosTokenExpiredError, CorosTokenStore

COROS_API_BASE = "https://open.coros.com"


class CorosClient:
    def __init__(
        self,
        store: CorosTokenStore,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._store = store
        self._client_id = client_id
        self._client_secret = client_secret

    async def _token(self) -> CorosTokenData:
        data = self._store.load()
        if data.is_expired():
            if data.refresh_is_expired():
                raise CorosTokenExpiredError(
                    "COROS refresh token expired. Reconnect at http://localhost:8080."
                )
            refreshed = await refresh_access_token(
                self._client_id, self._client_secret, data.refresh_token
            )
            data = CorosTokenData(
                open_id=data.open_id,
                access_token=refreshed["access_token"],
                refresh_token=refreshed["refresh_token"],
                expires_at=refreshed["expires_at"],
                refresh_expires_at=refreshed["refresh_expires_at"],
            )
            self._store.save(data)
        return data

    async def _get(self, path: str, params: dict | None = None) -> Any:
        token_data = await self._token()
        headers = {"Authorization": f"Bearer {token_data.access_token}"}
        base_params = {"openId": token_data.open_id, **(params or {})}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{COROS_API_BASE}{path}",
                headers=headers,
                params=base_params,
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("result") not in ("0000", 0, "0"):
            raise CorosAuthError(f"COROS API error: {body.get('message', body)}")
        return body.get("data")

    async def get_profile(self) -> dict:
        """Fetch the authenticated athlete's COROS profile."""
        data = await self._get("/v2/coros/user/profile")
        return data or {}

    async def list_activities(
        self,
        start_day: str,
        end_day: str,
        page_number: int = 1,
        page_size: int = 50,
    ) -> list[dict]:
        """
        List activities in YYYYMMDD date range.
        Returns up to page_size items per call.
        """
        params = {
            "startDay": start_day.replace("-", ""),
            "endDay": end_day.replace("-", ""),
            "pageNumber": page_number,
            "pageSize": page_size,
        }
        data = await self._get("/v2/coros/sport/list", params)
        if not data:
            return []
        return data.get("dataList", []) if isinstance(data, dict) else []

    async def get_activity(self, fit_file_id: str) -> dict:
        """Get detailed data for a single activity."""
        params = {"fitFileId": fit_file_id}
        data = await self._get("/v2/coros/sport/detail", params)
        return data or {}

    async def list_daily_data(self, start_day: str, end_day: str) -> list[dict]:
        """
        Get daily wellness summary for each day in the range.
        Includes steps, calories, heart rate, and intensity minutes.
        """
        params = {
            "startDay": start_day.replace("-", ""),
            "endDay": end_day.replace("-", ""),
        }
        data = await self._get("/v2/coros/daily/list", params)
        if not data:
            return []
        return data.get("dataList", []) if isinstance(data, dict) else []

    async def list_sleep(self, start_day: str, end_day: str) -> list[dict]:
        """
        Get sleep data for each night in the range.
        """
        params = {
            "startDay": start_day.replace("-", ""),
            "endDay": end_day.replace("-", ""),
        }
        data = await self._get("/v2/coros/sleep/list", params)
        if not data:
            return []
        return data.get("dataList", []) if isinstance(data, dict) else []

    async def list_training_load(self, start_day: str, end_day: str) -> list[dict]:
        """
        Get training load metrics for each day in the range.
        Includes TRIMP, aerobic/anaerobic load, and fitness/fatigue estimates.
        """
        params = {
            "startDay": start_day.replace("-", ""),
            "endDay": end_day.replace("-", ""),
        }
        data = await self._get("/v2/coros/trainingload/list", params)
        if not data:
            return []
        return data.get("dataList", []) if isinstance(data, dict) else []
