# stock-mcp

An MCP server that exposes DSA's **问股** (Agent Chat / ask-stock) capability as a
cross-client tool.

## What it does

```
Claude Desktop / Code / Cursor
         |
         |  Streamable HTTP (MCP)
         v
    stock-mcp  (this server)
         |
         |  HTTP (REST)
         v
  DSA FastAPI API
         |
    /api/v1/agent/chat
    /api/v1/agent/skills
```

- **ask_stock** — send a natural-language stock question, get the Agent Chat answer back.
- **list_agent_skills** — list available strategy skills for `ask_stock`.

## Prerequisites

- Python 3.11+
- DSA API server running and reachable (e.g. `python main.py --serve-only` inside the
  `daily_stock_analysis` repo, default `http://127.0.0.1:8000`).

## Install

```bash
cd stock-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
# With defaults (DSA on 127.0.0.1:8000, no auth)
python -m stock_mcp

# Or via the installed console script
dsa-mcp-server

# With env overrides
DSA_API_BASE_URL=http://127.0.0.1:8000 DSA_API_AUTH_ENABLED=true DSA_API_PASSWORD=xxx python -m stock_mcp
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DSA_API_BASE_URL` | `http://127.0.0.1:8000` | DSA FastAPI base URL |
| `DSA_MCP_HOST` | `127.0.0.1` | MCP HTTP bind host |
| `DSA_MCP_PORT` | `8765` | MCP HTTP bind port |
| `DSA_MCP_PATH` | `/mcp` | MCP endpoint path |
| `DSA_API_AUTH_ENABLED` | `false` | Whether DSA requires admin auth |
| `DSA_API_PASSWORD` | *(empty)* | DSA admin password (required when auth enabled) |
| `DSA_API_TIMEOUT` | `30` | Timeout for short calls (skills, login) |
| `DSA_API_REQUEST_TIMEOUT` | `600` | Timeout for `ask_stock` (agent chat) |
| `DSA_MCP_LOG_LEVEL` | `INFO` | Server log level |

## Client configuration

### Claude Desktop

Add to your `claude_desktop_config.json` (macOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "dsa-ask-stock": {
      "url": "http://127.0.0.1:8765/mcp",
      "env": {
        "DSA_API_BASE_URL": "http://127.0.0.1:8000",
        "DSA_API_AUTH_ENABLED": "false"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add dsa-ask-stock http://127.0.0.1:8765/mcp
```

### Cursor

In **Settings → MCP**, add a Streamable HTTP server pointing to `http://127.0.0.1:8765/mcp`.

## Auth notes

If DSA is running with `ADMIN_AUTH_ENABLED=true`:

```bash
export DSA_API_AUTH_ENABLED=true
export DSA_API_PASSWORD="your-dsa-admin-password"
python -m stock_mcp
```

The server logs in once at startup via `POST /api/v1/auth/login` and reuses the session
cookie across tool calls.

## Backend limitation

If DSA's `AGENT_BACKEND` is set to `codex_app_server`, `ask_stock` returns a clear error:
Codex requires the streaming Chat interface (`/chat/stream`), which is not exposed over
MCP in this tool. Switch `AGENT_BACKEND` to `litellm` (or `auto`) for MCP compatibility,
or use the DSA web Chat page directly.

## Security

The server binds to `127.0.0.1` by default. Do not expose the MCP port publicly without
auth — the MCP transport is designed for local/loopback use.
