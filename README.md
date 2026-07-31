# RunCoachMCP

Connect Claude to your Strava workout history and Garmin Connect wellness data via MCP. Ask Claude about your training load, race performance, heart rate trends, segment PRs, sleep quality, HRV, body battery, and more.

## Architecture

```
┌─────────────────┐     OAuth2 + PKCE     ┌─────────────┐
│  localhost:8080  │ ───────────────────▶  │   Strava    │
│  (FastAPI web)  │ ◀─────────────────── │     API     │
└─────────────────┘     tokens            └─────────────┘
         │ save encrypted
         ▼
  ~/.claude2strava/
    tokens.enc (AES-256)

┌─────────────────┐   email + password    ┌─────────────┐
│  localhost:8080  │ ───────────────────▶  │   Garmin    │
│  (FastAPI web)  │ ◀─────────────────── │   Connect   │
└─────────────────┘   session tokens      └─────────────┘
         │ save encrypted
         ▼
  ~/.claude2strava/
    garmin_session.enc (AES-256)

┌─────────────────┐     MCP protocol     ┌──────────────┐
│  Claude Desktop │ ───────────────────▶  │  MCP server  │
│                 │ ◀─────────────────── │  (13 tools)  │
└─────────────────┘     JSON results      └──────────────┘
                                 │ reads tokens
                                 ▼
                          ~/.claude2strava/
                            tokens.enc
                            garmin_session.enc
```

## Security

| Concern | How it's handled |
|---|---|
| Tokens on disk | AES-256-GCM (Fernet) encrypted, file mode `0600` |
| Token storage location | `~/.claude2strava/` — outside the repo |
| CSRF on OAuth callback | Per-request `state` UUID in a signed HttpOnly cookie |
| Code interception | PKCE (`S256`) added to every Strava OAuth flow |
| Secrets in logs | `Authorization` headers never logged |
| Token expiry | Strava client auto-refreshes 60 s before expiry |
| Garmin password | Never stored — only bearer tokens (di_token + refresh) and the profile name are persisted |
| Garmin 2FA | MFA state held in server memory only; opaque session ID in HttpOnly cookie |
| Local-only web UI | FastAPI binds to `127.0.0.1` only |

Your Strava credentials (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`) and encryption key live in `.env` which is excluded by `.gitignore`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `brew install uv`
- A Strava account
- A Garmin Connect account (optional — only needed for sleep/HRV/recovery tools)
- [Claude Desktop](https://claude.ai/download)

## Setup

### 1. Register a Strava API application

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application (name/website can be anything personal)
3. Set **Authorization Callback Domain** to `localhost`
4. Note your **Client ID** and **Client Secret**

### 2. Configure the project

```bash
cd /path/to/Claude2Strava
cp .env.example .env
```

Edit `.env`:

```dotenv
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=your_secret_here

# Generate an encryption key (run once):
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=<paste generated key here>

# Generate a session signing key (run once):
# python -c "import secrets; print(secrets.token_hex(32))"
WEB_SECRET_KEY=<paste generated key here>
```

### 3. Install dependencies

```bash
uv sync
```

### 4. Connect your Strava account

```bash
uv run python -m claude2strava.web.app
```

Open [http://localhost:8080](http://localhost:8080) and click **Connect with Strava**. After authorizing, you'll see a success page. You can close the browser and stop the web server — you only need to do this once (or if you revoke access).

### 5. Connect your Garmin account (optional)

With the web UI running, click **Connect Garmin** on the dashboard (or go to [http://localhost:8080/auth/garmin](http://localhost:8080/auth/garmin)). Enter your Garmin Connect email and password.

If your account has **two-factor authentication** enabled, you'll be prompted for the code sent to your email or authenticator app. Your Garmin password is never stored — only the session bearer tokens and your Garmin profile name (needed for the sleep/daily-stats endpoints) are encrypted and saved to `~/.claude2strava/garmin_session.enc`.

Garmin sessions last approximately 30 days. If a session expires, reconnect through the dashboard.

### 6. Configure Claude Desktop

Add the MCP server to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "strava": {
      "command": "uv",
      "args": ["run", "python", "-m", "claude2strava.mcp_server.server"],
      "cwd": "/Users/you/Documents/GIT/Claude2Strava"
    }
  }
}
```

Restart Claude Desktop. The MCP server will start automatically when Claude launches.

## Available MCP Tools

### Strava

| Tool | What it does |
|---|---|
| `check_connection` | Verify Strava is connected, show athlete profile |
| `list_activities` | Browse activities with date/type filters and pagination |
| `get_activity` | Full detail: splits, best efforts, elevation, gear |
| `get_activity_streams` | Time-series HR, pace, cadence, altitude, power |
| `get_athlete_stats` | YTD / all-time / recent totals for run/ride/swim |
| `get_athlete_zones` | Heart rate and power training zones |
| `get_starred_segments` | Your starred Strava segments |

### Garmin Connect

| Tool | What it does |
|---|---|
| `check_garmin_connection` | Verify Garmin is connected, show the athlete name |
| `get_garmin_sleep` | Sleep stages (deep/light/REM/awake), HRV, SpO2, respiration for a date |
| `get_garmin_hrv` | Overnight HRV average, weekly avg, status, and 5-min sample readings |
| `get_garmin_daily_stats` | Steps, resting HR, body battery, stress, calories for a date |
| `get_garmin_body_battery` | Body battery (0–100) charged/drained across a date range |
| `get_garmin_sleep_range` | Night-by-night sleep summary across a date range |

## Example Claude prompts

```
How many kilometres have I run this year compared to last year?

Show me my 5 longest runs of 2024 and compare their average heart rates.

What's my average moving time for runs over 20km?

Analyse my heart rate data from my last long run and identify any cardiac drift.

Which of my starred segments have I improved on most in the last 6 months?

How was my sleep and HRV this week? Do I look recovered enough for a hard session?

Compare my body battery levels on days after long runs vs. rest days.

Show my HRV trend over the last month and flag any nights below baseline.
```

## Development

```bash
# Run tests
uv run pytest -v

# Run the web UI for re-authentication
uv run python -m claude2strava.web.app
```

## Token refresh

Strava access tokens expire every 6 hours. The MCP server auto-refreshes them before each API call using the stored refresh token (which doesn't expire). If the refresh token ever becomes invalid (e.g., you revoke access in Strava), re-run the web UI to reconnect.

Garmin sessions last approximately 30 days. Re-authenticate via the web dashboard at [http://localhost:8080](http://localhost:8080) when prompted.
