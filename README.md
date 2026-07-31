# RunCoachMCP

Connect Claude to your Garmin Connect wellness data and COROS watch activity data via MCP. Ask Claude about your training load, sleep quality, HRV, body battery, recovery readiness, weight trends, and more.

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

┌─────────────────┐     OAuth2            ┌─────────────┐
│  localhost:8080 │ ───────────────────▶  │    COROS    │
│  (FastAPI web)  │ ◀─────────────────── │  Open API   │
└─────────────────┘     tokens            └─────────────┘
         │ save encrypted
         ▼
  ~/.claude2strava/
    coros_tokens.enc (AES-256)

┌─────────────────┐     MCP protocol     ┌──────────────┐
│  Claude Desktop │ ───────────────────▶  │  MCP server  │
│                 │ ◀─────────────────── │  (15 tools)  │
└─────────────────┘     JSON results      └──────────────┘
                                 │ reads tokens
                                 ▼
                          ~/.claude2strava/
                            garmin_session.enc
                            coros_tokens.enc
```

## Security

| Concern | How it's handled |
|---|---|
| Tokens on disk | AES-256-GCM (Fernet) encrypted, file mode `0600` |
| Token storage location | `~/.claude2strava/` — outside the repo |
| CSRF on OAuth callback | Per-request `state` in an HttpOnly cookie, checked on the COROS callback |
| Secrets in logs | `Authorization` headers never logged |
| COROS token expiry | Access token auto-refreshes; refresh token valid ~90 days |
| Garmin password | Never stored — only bearer tokens (di_token + refresh) and the profile name are persisted |
| Garmin 2FA | MFA state held in server memory only; opaque session ID in HttpOnly cookie |
| Local-only web UI | FastAPI binds to `127.0.0.1` only |

Your credentials and encryption key live in `.env`, which is excluded by `.gitignore`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `brew install uv`
- A Garmin Connect account (for sleep/HRV/recovery tools)
- A COROS account (optional — only needed for COROS watch activity and training load tools)
- [Claude Desktop](https://claude.ai/download)

## Setup

### 1. Register a COROS Open API application (optional)

1. Go to [open.coros.com](https://open.coros.com) and apply for developer access
2. Create an application and set the **Redirect URI** to `http://localhost:8080/auth/coros/callback`
3. Note your **Client ID** and **Client Secret**

Skip this step if you only want Garmin data.

### 2. Configure the project

```bash
cd /path/to/Claude2Strava
cp .env.example .env
```

Edit `.env`:

```dotenv
# Optional — only needed for COROS watch data
COROS_CLIENT_ID=your_coros_client_id
COROS_CLIENT_SECRET=your_coros_secret

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

### 4. Connect your accounts

```bash
uv run python -m claude2strava.web.app
```

Open [http://localhost:8080](http://localhost:8080). The dashboard shows both connectors — connect whichever you have.

**Garmin:** Click **Mit Garmin verbinden** and enter your Garmin Connect credentials. If your account has **two-factor authentication** enabled, you'll be prompted for the code sent to your email or authenticator app. Your password is never stored — only the session bearer tokens and your Garmin profile name (needed for the sleep/daily-stats endpoints) are encrypted and saved. Sessions last approximately 30 days.

**COROS:** Click **Mit COROS verbinden** and authorise via the COROS OAuth page. Access tokens are auto-refreshed; the refresh token is valid for approximately 90 days.

### 5. Configure Claude Desktop

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

Restart Claude Desktop. The MCP server starts automatically when Claude launches.

## Available MCP Tools

### Garmin Connect

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

### COROS

| Tool | What it does |
|---|---|
| `check_coros_connection` | Verify COROS is connected, show athlete profile |
| `list_coros_activities` | List watch activities in a date range (sport type, HR, distance, load) |
| `get_coros_activity` | Full detail: HR zones, pace, power, elevation, aerobic/anaerobic effect |
| `get_coros_daily_data` | Daily steps, calories, resting HR, and intensity minutes |
| `get_coros_sleep` | Sleep score, stage breakdown, SpO2 per night across a date range |
| `get_coros_training_load` | Daily training load (TRIMP-based), aerobic/anaerobic split, fitness/fatigue |

## Example Claude prompts

```
How was my sleep and HRV this week? Do I look recovered enough for a hard session?

Compare my body battery levels on days after long runs vs. rest days.

Show my HRV trend over the last month and flag any nights below baseline.

What's my training load trend over the last 4 weeks from my COROS watch?

Compare my COROS sleep scores with my Garmin body battery readings — do they agree?

How does my aerobic vs anaerobic training load split look this month?

Plot my weight and body fat percentage across this training block.
```

## Development

```bash
# Run tests
uv run pytest -v

# Run the web UI for authentication
uv run python -m claude2strava.web.app
```

## Token refresh

**Garmin:** Sessions last approximately 30 days. Re-authenticate via [http://localhost:8080](http://localhost:8080) when prompted.

**COROS:** Access tokens are auto-refreshed transparently. The refresh token is valid for approximately 90 days — re-authenticate via the dashboard when it expires.
