# RunCoachMCP

Connect Claude to your Garmin Connect wellness data via MCP. Ask Claude about your sleep quality, HRV, body battery, resting heart rate, weight trends, and recovery readiness.

> Strava support has been removed from this project — use the [official Strava MCP connector](https://www.strava.com/settings/api) instead.

## Architecture

```
┌─────────────────┐   email + password    ┌─────────────┐
│  localhost:8080 │ ───────────────────▶  │   Garmin    │
│  (FastAPI web)  │ ◀─────────────────── │   Connect   │
└─────────────────┘   session tokens      └─────────────┘
         │ save encrypted
         ▼
  ~/.claude2strava/
    garmin_session.enc (AES-256)

┌─────────────────┐     MCP protocol     ┌──────────────┐
│  Claude Desktop │ ───────────────────▶  │  MCP server  │
│                 │ ◀─────────────────── │   (9 tools)  │
└─────────────────┘     JSON results      └──────────────┘
                                 │ reads session
                                 ▼
                          ~/.claude2strava/
                            garmin_session.enc
```

## Security

| Concern | How it's handled |
|---|---|
| Tokens on disk | AES-256-GCM (Fernet) encrypted, file mode `0600` |
| Token storage location | `~/.claude2strava/` — outside the repo |
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
cd /path/to/Claude2Strava
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

### 3. Connect your Garmin account

```bash
uv run python -m claude2strava.web.app
```

Open [http://localhost:8080](http://localhost:8080) and click **Connect Garmin** (or go straight to [http://localhost:8080/auth/garmin](http://localhost:8080/auth/garmin)). Enter your Garmin Connect email and password.

If your account has **two-factor authentication** enabled, you'll be prompted for the code sent to your email or authenticator app. Your Garmin password is never stored — only the session bearer tokens and your Garmin profile name (needed for the sleep/daily-stats endpoints) are encrypted and saved to `~/.claude2strava/garmin_session.enc`.

Garmin sessions last approximately 30 days. If a session expires, reconnect through the dashboard.

### 4. Configure Claude Desktop

Add the MCP server to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "runcoach": {
      "command": "uv",
      "args": ["run", "python", "-m", "claude2strava.mcp_server.server"],
      "cwd": "/Users/you/Documents/GIT/Claude2Strava"
    }
  }
}
```

Restart Claude Desktop. The MCP server will start automatically when Claude launches.

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

How has my resting heart rate changed over the last 90 days?

Plot my weight and body fat percentage across this training block.
```

## Development

```bash
# Run tests
uv run pytest -v

# Run the web UI for re-authentication
uv run python -m claude2strava.web.app
```

## Session refresh

Garmin sessions last approximately 30 days. Re-authenticate via the web dashboard at [http://localhost:8080](http://localhost:8080) when prompted.
