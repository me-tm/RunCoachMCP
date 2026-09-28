# Claude2Garmin

Connect Claude to your Garmin Connect wellness data via MCP. Ask Claude about your training load, sleep quality, HRV, body battery, recovery readiness, weight trends, and more.

> Strava and COROS support have been removed from this project — this connector is dedicated to Garmin Connect only. Use the [official Strava MCP connector](https://www.strava.com/settings/api) if you need Strava data.

## Architecture

```
┌─────────────────┐   email + password    ┌─────────────┐
│  localhost:8080 │ ───────────────────▶  │   Garmin    │
│  (FastAPI web)  │ ◀─────────────────── │   Connect   │
└─────────────────┘   session tokens      └─────────────┘
         │ save encrypted
         ▼
  ~/.claude2garmin/
    garmin_session.enc (AES-256)

┌─────────────────┐     MCP protocol     ┌──────────────┐
│  Claude Desktop │ ───────────────────▶  │  MCP server  │
│                 │ ◀─────────────────── │  (9 tools)   │
└─────────────────┘     JSON results      └──────────────┘
                                 │ reads/rewrites tokens
                                 ▼
                          ~/.claude2garmin/
                            garmin_session.enc
```

## Security

| Concern | How it's handled |
|---|---|
| Tokens on disk | AES-256-GCM (Fernet) encrypted, file mode `0600` |
| Token storage location | `~/.claude2garmin/` — outside the repo |
| Secrets in logs | `Authorization` headers never logged |
| Garmin password | Never stored — only bearer tokens (di_token + refresh) and the profile name are persisted |
| Garmin 2FA | MFA state held in server memory only; opaque session ID in HttpOnly cookie |
| Local-only web UI | FastAPI binds to `127.0.0.1` only |

Your encryption key lives in `.env`, which is excluded by `.gitignore`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `brew install uv`
- A Garmin Connect account
- [Claude Desktop](https://claude.ai/download)

## Setup

### 1. Configure the project

```bash
cd /path/to/Claude2Garmin
cp .env.example .env
```

Edit `.env`:

```dotenv
# Generate an encryption key (run once):
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=<paste generated key here>

# Generate a session signing key (run once):
# python -c "import secrets; print(secrets.token_hex(32))"
WEB_SECRET_KEY=<paste generated key here>
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Connect your account

```bash
uv run python -m claude2garmin.web.app
```

Open [http://localhost:8080](http://localhost:8080) and click **Mit Garmin verbinden**, then enter your Garmin Connect credentials. If your account has **two-factor authentication** enabled, you'll be prompted for the code sent to your email or authenticator app. Your password is never stored — only the session bearer tokens and your Garmin profile name (needed for the sleep/daily-stats endpoints) are encrypted and saved.

### 4. Configure Claude Desktop

Add the MCP server to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "claude2garmin": {
      "command": "uv",
      "args": ["run", "python", "-m", "claude2garmin.mcp_server.server"],
      "cwd": "/Users/you/Documents/GIT/Claude2Garmin"
    }
  }
}
```

Restart Claude Desktop. The MCP server starts automatically when Claude launches.

## Available MCP Tools

| Tool | What it does |
|---|---|
| `check_garmin_connection` | Verify Garmin is connected, show the athlete name |
| `get_garmin_user_profile` | Age, height, gender, last recorded weight |
| `get_garmin_sleep` | Sleep stages (deep/light/REM/awake), HRV, SpO2, respiration for a date |
| `get_garmin_sleep_range` | Night-by-night sleep summary across a date range |
| `get_garmin_hrv` | Overnight HRV average, weekly avg, status, and 5-min sample readings |
| `get_garmin_daily_stats` | Steps, resting HR, body battery, stress, calories for a date |
| `get_garmin_body_battery` | Body battery (0–100) charged/drained across a date range |
| `get_garmin_weight` | Weight, BMI, body fat, muscle mass for a single day |
| `get_garmin_weight_range` | Body composition trend across a date range |

## Example Claude prompts

```
How was my sleep and HRV this week? Do I look recovered enough for a hard session?

Compare my body battery levels on days after long runs vs. rest days.

Show my HRV trend over the last month and flag any nights below baseline.

Plot my weight and body fat percentage across this training block.
```

## Development

```bash
# Run tests
uv run pytest -v

# Run the web UI for authentication
uv run python -m claude2garmin.web.app
```

## Session length

Garmin's own Bearer access token is short-lived (~JWT with a `exp` claim of roughly an hour), refreshed automatically by the `garminconnect` library using a longer-lived refresh token. Garmin rotates that refresh token on every use — the old one stops working the moment a new one is issued.

Earlier versions of this project discarded that rotation: each MCP server process refreshed the token in memory but never wrote the new refresh token back to `garmin_session.enc`. The *next* process then tried to reuse an already-consumed refresh token, Garmin rejected it, and the user was forced back to an email/password login — often well before any real Garmin-side expiry.

`GarminClient` now re-persists the current token pair to the encrypted session file after every Garmin API call whose tokens changed. As long as the MCP server (or the web dashboard's connection test) is used at least once before Garmin's refresh token would otherwise go stale, the session keeps renewing itself indefinitely — no fixed 30-day wall, and no need to reconnect through the dashboard unless the password changes, the session is revoked from the Garmin app, or the account goes untouched for an extended period.

If a session ever does need to be re-established, do it via [http://localhost:8080](http://localhost:8080).

> **Note:** if you're upgrading from `claude2strava`, your existing Garmin session at `~/.claude2strava/` is migrated automatically to `~/.claude2garmin/` on first run — no need to reconnect.
