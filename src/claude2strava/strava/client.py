import time
from typing import Any, Optional

import httpx

from ..token_store import TokenData, TokenStore, TokenNotFoundError
from .auth import refresh_access_token, StravaAuthError


STRAVA_API_BASE = "https://www.strava.com/api/v3"


class StravaClient:
    def __init__(self, token_store: TokenStore, client_id: str, client_secret: str) -> None:
        self._store = token_store
        self._client_id = client_id
        self._client_secret = client_secret

    async def _get_valid_token(self) -> str:
        data = self._store.load()  # raises TokenNotFoundError if missing
        if data.is_expired():
            data = await self._refresh(data)
        return data.access_token

    async def _refresh(self, data: TokenData) -> TokenData:
        try:
            async with httpx.AsyncClient() as client:
                result = await refresh_access_token(
                    self._client_id,
                    self._client_secret,
                    data.refresh_token,
                    http_client=client,
                )
        except StravaAuthError as exc:
            raise StravaAuthError(
                "Could not refresh Strava token. Re-authenticate at http://localhost:8080"
            ) from exc
        updated = TokenData(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token", data.refresh_token),
            expires_at=result["expires_at"],
            athlete_id=data.athlete_id,
            athlete_name=data.athlete_name,
        )
        self._store.save(updated)
        return updated

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        token = await self._get_valid_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{STRAVA_API_BASE}{path}", headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Public API methods ──────────────────────────────────────────────────

    async def get_athlete(self) -> dict:
        return await self._get("/athlete")

    async def get_athlete_stats(self, athlete_id: int) -> dict:
        return await self._get(f"/athletes/{athlete_id}/stats")

    async def get_athlete_zones(self) -> dict:
        return await self._get("/athlete/zones")

    async def list_activities(
        self,
        after: Optional[int] = None,
        before: Optional[int] = None,
        activity_type: Optional[str] = None,
        limit: int = 30,
        page: int = 1,
    ) -> list[dict]:
        params: dict[str, Any] = {"per_page": min(limit, 200), "page": page}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        activities = await self._get("/athlete/activities", params=params)
        if activity_type:
            activities = [a for a in activities if a.get("type") == activity_type]
        return activities

    async def get_activity(self, activity_id: int) -> dict:
        return await self._get(f"/activities/{activity_id}", params={"include_all_efforts": True})

    async def get_activity_streams(
        self,
        activity_id: int,
        keys: Optional[list[str]] = None,
    ) -> dict:
        default_keys = ["time", "distance", "heartrate", "cadence", "altitude", "watts", "velocity_smooth"]
        requested = ",".join(keys or default_keys)
        return await self._get(
            f"/activities/{activity_id}/streams",
            params={"keys": requested, "key_by_type": True},
        )

    async def get_starred_segments(self) -> list[dict]:
        return await self._get("/segments/starred")
