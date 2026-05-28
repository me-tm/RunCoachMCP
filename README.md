# Claude2Strava

Connect Claude to your Strava workout history via MCP. Ask Claude about your training load, race performance, heart rate trends, segment PRs, and more.

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

┌─────────────────┐     MCP protocol     ┌─────────────┐
│  Claude Desktop │ ───────────────────▶  │  MCP server │
│                 │ ◀─────────────────── │  (7 tools)  │
└─────────────────┘     JSON results      └─────────────┘
                                 │ reads tokens
                                 ▼
                          ~/.claude2strava/
                            tokens.enc
```

## Security

| Concern | How it's handled |
|---|---|
| Tokens on disk | AES-256-GCM (Fernet) encrypted, file mode `0600` |
| Token storage location | `~/.claude2strava/tokens.enc` — outside the repo |
| CSRF on OAuth callback | Per-request `state` UUID in a signed HttpOnly cookie |
| Code interception | PKCE (`S256`) added to every OAuth flow |
| Secrets in logs | `Authorization` headers never logged |
| Token expiry | Client auto-refreshes 60 s before expiry |
| Local-only web UI | FastAPI binds to `127.0.0.1` only |

Your Strava credentials (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`) and encryption key live in `.env` which is excluded by `.gitignore`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `brew install uv`
- A Strava account
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

### 5. Configure Claude Desktop

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

Restart Claude Desktop. The Strava MCP server will start automatically when Claude launches.

## Available MCP Tools

| Tool | What it does |
|---|---|
| `check_connection` | Verify Strava is connected, show athlete profile |
| `list_activities` | Browse activities with date/type filters and pagination |
| `get_activity` | Full detail: splits, best efforts, elevation, gear |
| `get_activity_streams` | Time-series HR, pace, cadence, altitude, power |
| `get_athlete_stats` | YTD / all-time / recent totals for run/ride/swim |
| `get_athlete_zones` | Heart rate and power training zones |
| `get_starred_segments` | Your starred Strava segments |

## Example Claude prompts

```
How many kilometres have I run this year compared to last year?

Show me my 5 longest runs of 2024 and compare their average heart rates.

What's my average moving time for runs over 20km?

Analyse my heart rate data from my last long run and identify any cardiac drift.

Which of my starred segments have I improved on most in the last 6 months?
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
