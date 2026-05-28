import secrets
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from ..config import get_settings
from ..crypto import Crypto
from ..garmin.client import (
    GarminClient,
    complete_mfa,
    extract_session,
    login as garmin_login,
)
from ..garmin.session_store import (
    GarminNotConnectedError,
    GarminSessionData,
    GarminSessionExpiredError,
    GarminSessionStore,
)
from ..strava.auth import (
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    generate_state,
    StravaAuthError,
)
from ..token_store import TokenData, TokenStore


TEMPLATES_DIR = Path(__file__).parent / "templates"

# Use Jinja2 directly — avoids Starlette's Jinja2Templates wrapper which has
# a cache-key incompatibility with Jinja2 3.1.6 on Python 3.14.
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _render(template_name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_jinja_env.get_template(template_name).render(**ctx))


app = FastAPI(title="Claude2Strava OAuth UI", docs_url=None, redoc_url=None)

# ── Server-side state ─────────────────────────────────────────────────────────

# Strava OAuth: session_id → {state, verifier}
_pending_oauth: dict[str, dict] = {}

# Garmin 2FA: session_id → partially-authenticated Garmin API object
_pending_garmin: dict[str, object] = {}

# Lazily built singletons
_settings      = None
_strava_store  = None
_garmin_store  = None
_crypto        = None


def _init_deps() -> None:
    global _settings, _strava_store, _garmin_store, _crypto
    if _settings is None:
        _settings = get_settings()
        _crypto = Crypto(_settings.get_fernet_key())
        _strava_store = TokenStore(_settings.token_dir, _crypto)
        _garmin_store = GarminSessionStore(_settings.token_dir, _crypto)


def _get_strava():
    _init_deps()
    return _settings, _strava_store


def _get_garmin_store() -> GarminSessionStore:
    _init_deps()
    return _garmin_store


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    _init_deps()

    # Strava status
    strava_connected = _strava_store.exists()
    athlete_name = ""
    if strava_connected:
        try:
            athlete_name = _strava_store.load().athlete_name
        except Exception:
            strava_connected = False

    # Garmin status
    garmin_connected = _garmin_store.exists()
    garmin_username = ""
    if garmin_connected:
        try:
            garmin_username = _garmin_store.load().username
        except Exception:
            garmin_connected = False

    return _render(
        "index.html",
        connected=strava_connected,
        athlete_name=athlete_name,
        garmin_connected=garmin_connected,
        garmin_username=garmin_username,
    )


# ── Strava OAuth ───────────────────────────────────────────────────────────────

@app.get("/auth/strava/start")
async def auth_start():
    settings, _ = _get_strava()
    pkce = generate_pkce_pair()
    state = generate_state()
    session_id = secrets.token_urlsafe(16)
    _pending_oauth[session_id] = {"state": state, "verifier": pkce.verifier}
    auth_url = build_auth_url(settings.strava_client_id, state, pkce.challenge, settings.web_port)
    redirect = RedirectResponse(url=auth_url, status_code=302)
    redirect.set_cookie(key="oauth_sid", value=session_id, httponly=True, samesite="lax", max_age=600)
    return redirect


@app.get("/auth/strava/callback", response_class=HTMLResponse)
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    settings, store = _get_strava()
    session_id = request.cookies.get("oauth_sid")
    session = _pending_oauth.pop(session_id, {}) if session_id else {}

    if error:
        return _render("callback.html", success=False, message=f"Strava denied access: {error}")
    if not session or session.get("state") != state:
        return _render("callback.html", success=False, message="Invalid OAuth state — possible CSRF. Please try again.")
    if not code:
        return _render("callback.html", success=False, message="No authorization code received.")

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            token_data = await exchange_code(
                settings.strava_client_id,
                settings.strava_client_secret,
                code,
                session["verifier"],
                settings.web_port,
                http_client=client,
            )
    except StravaAuthError as exc:
        return _render("callback.html", success=False, message=str(exc))

    athlete = token_data.get("athlete", {})
    full_name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    store.save(TokenData(
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        expires_at=token_data["expires_at"],
        athlete_id=athlete.get("id", 0),
        athlete_name=full_name,
    ))
    response = _render("callback.html", success=True, athlete_name=full_name)
    response.delete_cookie("oauth_sid")
    return response


@app.get("/api/check")
async def api_check():
    _, store = _get_strava()
    if not store.exists():
        return JSONResponse({"ok": False, "error": "No token stored — connect Strava first."})
    try:
        data = store.load()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Token decryption failed: {exc}"})
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://www.strava.com/api/v3/athlete",
                headers={"Authorization": f"Bearer {data.access_token}"},
            )
        if resp.status_code == 200:
            a = resp.json()
            name = f"{a.get('firstname', '')} {a.get('lastname', '')}".strip()
            return JSONResponse({"ok": True, "athlete": name, "id": a.get("id")})
        elif resp.status_code == 401:
            return JSONResponse({"ok": False, "error": "Access token expired — reconnect Strava to refresh."})
        else:
            return JSONResponse({"ok": False, "error": f"Strava returned HTTP {resp.status_code}."})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Network error: {exc}"})


@app.post("/auth/strava/disconnect")
async def disconnect():
    _, store = _get_strava()
    store.delete()
    return RedirectResponse(url="/", status_code=303)


# ── Garmin Connect ─────────────────────────────────────────────────────────────

@app.get("/auth/garmin", response_class=HTMLResponse)
async def garmin_login_page():
    return _render("garmin_login.html", error="")


@app.post("/auth/garmin/connect", response_class=HTMLResponse)
async def garmin_connect(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Authenticate with Garmin. Handles both direct success and 2FA challenge."""
    import asyncio
    garmin_store = _get_garmin_store()

    try:
        # Run synchronous garminconnect login in a thread
        loop = asyncio.get_event_loop()
        api, needs_mfa, mfa_state = await loop.run_in_executor(None, garmin_login, email, password)
    except Exception as exc:
        return _render("garmin_login.html", error=f"Login fehlgeschlagen: {exc}")

    if needs_mfa:
        # Store (api, mfa_state) server-side; only an opaque session ID in the cookie
        session_id = secrets.token_urlsafe(16)
        _pending_garmin[session_id] = (api, mfa_state)
        resp = _render("garmin_verify.html", error="")
        resp.set_cookie(key="garmin_sid", value=session_id, httponly=True, samesite="lax", max_age=300)
        return resp

    # Login succeeded without 2FA — persist tokens
    token_json = extract_session(api)
    garmin_store.save(GarminSessionData(username=email, token_json=token_json))
    return _render("garmin_callback.html", success=True, username=email)


@app.post("/auth/garmin/verify", response_class=HTMLResponse)
async def garmin_verify(
    request: Request,
    mfa_code: str = Form(...),
):
    """Complete a pending Garmin 2FA challenge."""
    import asyncio
    garmin_store = _get_garmin_store()

    session_id = request.cookies.get("garmin_sid")
    pending = _pending_garmin.pop(session_id, None) if session_id else None
    if pending is None:
        return _render("garmin_login.html", error="Session abgelaufen — bitte erneut einloggen.")

    api, mfa_state = pending
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, complete_mfa, api, mfa_state, mfa_code.strip())
    except Exception as exc:
        return _render("garmin_verify.html", error=f"2FA fehlgeschlagen: {exc}")

    token_json = extract_session(api)
    username = getattr(api, "username", "") or getattr(api, "email", "")
    garmin_store.save(GarminSessionData(username=username, token_json=token_json))
    response = _render("garmin_callback.html", success=True, username=username)
    response.delete_cookie("garmin_sid")
    return response


@app.post("/auth/garmin/disconnect")
async def garmin_disconnect():
    garmin_store = _get_garmin_store()
    garmin_store.delete()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/check-garmin")
async def api_check_garmin():
    """Test that the stored Garmin session is still valid."""
    import asyncio
    garmin_store = _get_garmin_store()

    if not garmin_store.exists():
        return JSONResponse({"ok": False, "error": "Nicht verbunden — erst über Dashboard verbinden."})
    try:
        client = GarminClient(garmin_store)
        loop = asyncio.get_event_loop()
        profile = await loop.run_in_executor(None, client.check_connection)
        name = f"{profile.get('displayName', '')}".strip() or profile.get("userName", "")
        return JSONResponse({"ok": True, "username": name})
    except GarminSessionExpiredError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except GarminNotConnectedError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Fehler: {exc}"})


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    settings = get_settings()
    uvicorn.run("claude2strava.web.app:app", host="127.0.0.1", port=settings.web_port, reload=False)


if __name__ == "__main__":
    main()
