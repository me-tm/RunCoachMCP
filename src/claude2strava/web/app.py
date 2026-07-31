import secrets
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from ..config import get_settings
from ..coros.auth import (
    build_auth_url as coros_build_auth_url,
    exchange_code as coros_exchange_code,
    generate_state as coros_generate_state,
    CorosAuthError,
)
from ..coros.token_store import CorosNotConnectedError, CorosTokenData, CorosTokenStore
from ..crypto import Crypto
from ..garmin.client import (
    GarminClient,
    complete_mfa,
    extract_session,
    login as garmin_login,
    profile_names,
)
from ..garmin.session_store import (
    GarminNotConnectedError,
    GarminSessionData,
    GarminSessionExpiredError,
    GarminSessionStore,
)


TEMPLATES_DIR = Path(__file__).parent / "templates"

# Use Jinja2 directly — avoids Starlette's Jinja2Templates wrapper which has
# a cache-key incompatibility with Jinja2 3.1.6 on Python 3.14.
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _render(template_name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_jinja_env.get_template(template_name).render(**ctx))


app = FastAPI(title="RunCoach MCP Connect UI", docs_url=None, redoc_url=None)

# ── Server-side state ─────────────────────────────────────────────────────────

# Garmin 2FA: session_id → partially-authenticated Garmin API object
_pending_garmin: dict[str, object] = {}

# Lazily built singletons
_settings      = None
_garmin_store  = None
_coros_store   = None
_crypto        = None


def _init_deps() -> None:
    global _settings, _garmin_store, _coros_store, _crypto
    if _settings is None:
        _settings = get_settings()
        _crypto = Crypto(_settings.get_fernet_key())
        _garmin_store = GarminSessionStore(_settings.token_dir, _crypto)
        _coros_store  = CorosTokenStore(_settings.token_dir, _crypto)


def _get_garmin_store() -> GarminSessionStore:
    _init_deps()
    return _garmin_store


def _get_coros_store() -> CorosTokenStore:
    _init_deps()
    return _coros_store


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    _init_deps()

    # Garmin status
    garmin_connected = _garmin_store.exists()
    garmin_username = ""
    if garmin_connected:
        try:
            garmin_username = _garmin_store.load().username
        except Exception:
            garmin_connected = False

    # COROS status
    coros_connected = _coros_store.exists()
    coros_nickname = ""
    if coros_connected:
        try:
            coros_nickname = _coros_store.load().open_id
        except Exception:
            coros_connected = False

    return _render(
        "index.html",
        garmin_connected=garmin_connected,
        garmin_username=garmin_username,
        coros_connected=coros_connected,
        coros_nickname=coros_nickname,
    )


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

    # Login succeeded without 2FA — persist tokens plus the display name,
    # which the MCP client needs for the stats/sleep endpoints
    token_json = extract_session(api)
    display_name, full_name = await loop.run_in_executor(None, profile_names, api)
    garmin_store.save(GarminSessionData(
        username=email,
        token_json=token_json,
        display_name=display_name,
        full_name=full_name,
    ))
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
    display_name, full_name = await loop.run_in_executor(None, profile_names, api)
    garmin_store.save(GarminSessionData(
        username=username,
        token_json=token_json,
        display_name=display_name,
        full_name=full_name,
    ))
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
        name = (profile.get("full_name") or profile.get("display_name") or "").strip()
        return JSONResponse({"ok": True, "username": name})
    except GarminSessionExpiredError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except GarminNotConnectedError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Fehler: {exc}"})


# ── COROS OAuth ───────────────────────────────────────────────────────────────

# COROS OAuth: session_id → state string
_pending_coros: dict[str, str] = {}


@app.get("/auth/coros/start")
async def coros_auth_start():
    _init_deps()
    state = coros_generate_state()
    session_id = secrets.token_urlsafe(16)
    _pending_coros[session_id] = state
    auth_url = coros_build_auth_url(_settings.coros_client_id, state, _settings.web_port)
    redirect = RedirectResponse(url=auth_url, status_code=302)
    redirect.set_cookie(key="coros_sid", value=session_id, httponly=True, samesite="lax", max_age=600)
    return redirect


@app.get("/auth/coros/callback", response_class=HTMLResponse)
async def coros_auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    _init_deps()
    coros_store = _get_coros_store()
    session_id = request.cookies.get("coros_sid")
    expected_state = _pending_coros.pop(session_id, None) if session_id else None

    if error:
        return _render("coros_callback.html", success=False, message=f"COROS denied access: {error}")
    if not expected_state or expected_state != state:
        return _render("coros_callback.html", success=False, message="Invalid OAuth state — possible CSRF. Please try again.")
    if not code:
        return _render("coros_callback.html", success=False, message="No authorization code received.")

    try:
        import httpx
        import time
        async with httpx.AsyncClient() as http:
            token_data = await coros_exchange_code(
                _settings.coros_client_id,
                _settings.coros_client_secret,
                code,
                _settings.web_port,
                http_client=http,
            )
    except CorosAuthError as exc:
        return _render("coros_callback.html", success=False, message=str(exc))

    now = int(time.time())
    coros_store.save(CorosTokenData(
        open_id=token_data.get("openId", ""),
        access_token=token_data["accessToken"],
        refresh_token=token_data["refreshToken"],
        expires_at=now + int(token_data.get("tokenExpiresIn", 86400)),
        refresh_expires_at=now + int(token_data.get("refreshTokenExpiresIn", 7776000)),
    ))
    nickname = token_data.get("openId", "")
    response = _render("coros_callback.html", success=True, nickname=nickname)
    response.delete_cookie("coros_sid")
    return response


@app.post("/auth/coros/disconnect")
async def coros_disconnect():
    coros_store = _get_coros_store()
    coros_store.delete()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/check-coros")
async def api_check_coros():
    """Verify stored COROS tokens are present and not expired."""
    coros_store = _get_coros_store()
    if not coros_store.exists():
        return JSONResponse({"ok": False, "error": "Nicht verbunden — erst über Dashboard verbinden."})
    try:
        data = coros_store.load()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Token-Fehler: {exc}"})
    if data.refresh_is_expired():
        return JSONResponse({"ok": False, "error": "COROS refresh token abgelaufen — bitte erneut verbinden."})
    return JSONResponse({"ok": True, "username": data.open_id})


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    settings = get_settings()
    uvicorn.run("claude2strava.web.app:app", host="127.0.0.1", port=settings.web_port, reload=False)


if __name__ == "__main__":
    main()
