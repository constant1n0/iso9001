# MCP server

AI agents operate the QMS through an MCP server that acts as a real user. It
runs the same services, policy, audit log and attribution as the web UI, and it
can never delete.

## Architecture

- `app/mcp_server/` is an adapter over `app/services/` (see
  [services](architecture/services.md)). The package is not named `mcp` so it
  cannot shadow the SDK (`mcp==2.3.0`).
- Five generic tools, with the module as a parameter, keep the agent's context
  small:

  | Tool | Purpose |
  |---|---|
  | `qms_modules` | The 12 modules with fields, allowed values, filters and what the caller may do |
  | `qms_list` | One bounded page of a module, with the filters `qms_modules` lists |
  | `qms_get` | One record by `id` |
  | `qms_create` | Create a record |
  | `qms_update` | Overwrite the given fields of a record |

  There is no delete tool. Records never include password or token fields.
- Each call runs in its own Flask application context and session. A write
  commits after the service returns; any error rolls back. Domain errors reach
  the agent with their Spanish message; unexpected errors reach it as a generic
  message and are logged on the server.
- The actor is the token's owner with the token's scopes, channel `mcp`:
  `read` reads, `write` writes, and the owner's role still applies (an
  `OPERATIVO` token cannot write the JSON registers). Audit rows and `created_by`
  / `updated_by` carry the owner.
- Transports: stateless Streamable HTTP at `/mcp` (JSON responses) for remote
  clients, and stdio for a local process. The HTTP app answers the 2025-06-18
  and 2025-11-25 `initialize` handshake and stateless 2026-07-28 requests.

## Issue a token

From a trusted shell on the server (see the README for all options):

```bash
venv/bin/flask --app run.py create-api-token --user ana --name "Claude Code" --scope read --scope write --days 90
```

The token (`iso_<prefix>_<secret>`) is printed once. Export it on the machine
that runs the client, never in a file under version control:

```bash
export ISO9001_TOKEN='iso_xxxxxxxx_...'
```

Use a dedicated user per agent with the lowest role that works, and `read`
only when the agent does not need to write. Revoke with `revoke-api-token`.

## Server configuration

Set these in the application's `.env` (the systemd unit reads it):

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY`, `DATABASE_URI` | required | The usual application settings; rotating `SECRET_KEY` invalidates every token |
| `MCP_HOST` | `127.0.0.1` | Interface to bind |
| `MCP_PORT` | `8765` | Port to bind |
| `MCP_ALLOWED_HOSTS` | `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` | Comma-separated `Host` values accepted; anything else gets 421. Add the public host name |
| `MCP_TRUSTED_PROXIES` | `127.0.0.1` | Comma-separated proxy addresses whose `X-Forwarded-For` is believed (uvicorn `forwarded_allow_ips`); `*` is refused |
| `ISO9001_MCP_TOKEN` | none | stdio only: the token the process acts as; it refuses to start without a valid one and checks it again on every tool call |

Run it with the systemd unit `iso9001-mcp.service` (copy, `daemon-reload`,
`enable --now`; see the header of the file), or by hand:

```bash
venv/bin/python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765
```

stdio re-authenticates the configured token on every tool call, so revoking it,
letting it expire or changing its owner's role takes effect on the next call
(a rejected call returns a tool error; the server keeps running).

Known gaps (follow-ups): there is no rate limiting on failed bearer tokens
(add it at Traefik or in the app before wide exposure); `qms_list` pages in
memory for `no_conformidades`, `documentos`, `capacitaciones`,
`satisfaccion_clientes` and `partes_interesadas` (their services have no
`list_page`, see `Module.paged_in_db` in the registry); the `estado` filter of
`no_conformidades` accepts only the fixed states, so legacy free-text states
cannot be filtered on.

A missing or invalid token gets `401` with `WWW-Authenticate: Bearer`; failures
are written to the security log (prefix and reason, never the token). Deploying
to production needs its own authorization.

## Traefik routing

The server speaks plain HTTP on `127.0.0.1:8765`; Traefik terminates TLS. Route
either a path or a subdomain to that address and keep the original `Host`
header (Traefik's default):

- Path: `PathPrefix(`/mcp`)` on the QMS host, service URL `http://127.0.0.1:8765`.
- Subdomain: `Host(`mcp.qms.example.com`)`, same service URL.

Traefik runs on the same host, so its connection comes from `127.0.0.1` and the
default `MCP_TRUSTED_PROXIES` is right: the security log then records the real
caller from `X-Forwarded-For`. Requests from any other peer cannot set that
header's effect. If the proxy runs elsewhere, list its address in
`MCP_TRUSTED_PROXIES`.

Then set `MCP_ALLOWED_HOSTS` to the public host name (for example
`MCP_ALLOWED_HOSTS=qms.example.com`) and restart the unit. The client URL is
`https://<host>/mcp`. Never expose port 8765 without TLS: the bearer token
travels in a header.

## Client configuration

Replace `https://qms.example.com/mcp` with your URL. Every remote client sends
`Authorization: Bearer <token>`.

### Claude Code

```bash
claude mcp add --transport http iso9001 https://qms.example.com/mcp \
  --header "Authorization: Bearer ${ISO9001_TOKEN}"
```

Local stdio instead (the token is read once at start):

```bash
claude mcp add iso9001-local --transport stdio --env ISO9001_MCP_TOKEN="$ISO9001_TOKEN" \
  -- /path/to/iso9001/venv/bin/python -m app.mcp_server --transport stdio
```

### Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.iso9001]
url = "https://qms.example.com/mcp"
bearer_token_env_var = "ISO9001_TOKEN"
```

### Pi

`~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "iso9001": {
      "url": "https://qms.example.com/mcp",
      "headers": { "Authorization": "Bearer ${ISO9001_TOKEN}" }
    }
  }
}
```

### OpenCode

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "iso9001": {
      "type": "remote",
      "url": "https://qms.example.com/mcp",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:ISO9001_TOKEN}" }
    }
  }
}
```

### OpenClaw

```json
{
  "mcp": {
    "servers": {
      "iso9001": {
        "transport": "streamable-http",
        "url": "https://qms.example.com/mcp",
        "headers": { "Authorization": "Bearer ${ISO9001_TOKEN}" }
      }
    }
  }
}
```

### Claude Desktop

Claude Desktop starts local processes only, so use `mcp-remote` as a bridge.
Keep the token out of the process list with a header file (one `Name: value`
per line, mode `600`):

```bash
umask 077
printf 'Authorization: Bearer %s\n' "$ISO9001_TOKEN" > ~/.iso9001-mcp-headers
```

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "iso9001": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://qms.example.com/mcp",
        "--transport", "http-only",
        "--header-file", "/home/you/.iso9001-mcp-headers"
      ]
    }
  }
}
```

### Local stdio (any client that launches a command)

```json
{
  "mcpServers": {
    "iso9001-local": {
      "command": "/path/to/iso9001/venv/bin/python",
      "args": ["-m", "app.mcp_server", "--transport", "stdio"],
      "cwd": "/path/to/iso9001",
      "env": { "ISO9001_MCP_TOKEN": "<paste the token here>" }
    }
  }
}
```

### Verification status

Verified here: the server over Streamable HTTP with the Python SDK client and
raw JSON-RPC requests for the three protocol revisions, and the `mcp-remote`
`--header` / `--header-file` options (v0.14.3 documentation). Unverified: no
real client from the list has connected yet, so every snippet above awaits its
smoke test. Least certain are Codex `bearer_token_env_var`, the Pi `mcp.json`
shape and its `${ENV}` expansion, the OpenCode `{env:...}` expansion, the
OpenClaw `mcp.servers` shape, and which protocol revision Pi, OpenClaw and
OpenCode negotiate. Check the client's documentation if a step fails.

## Smoke test

Per client, after configuring it with a `read` token for a user that can read
non-conformities:

1. **Claude Code:** `claude mcp list` shows `iso9001` connected; ask it to run
   `qms_modules`, then list non-conformities.
2. **Codex:** `codex mcp list` shows the server; ask it to call `qms_modules`.
3. **Pi:** open the MCP panel (`/mcp`) and check the five `qms_*` tools; call
   `qms_modules`.
4. **OpenCode:** `opencode mcp list` shows `iso9001` connected; call `qms_modules`.
5. **OpenClaw:** check the server is listed with the five tools; call `qms_modules`.
6. **Claude Desktop:** restart it, check the tools icon lists `iso9001`, and ask
   for the modules.

Then, once, with a `write` token for a test user: create a non-conformity, read
it back with `qms_get` and confirm the audit row has channel `mcp`. A wrong
token must give `401` (for example `curl -i -X POST https://qms.example.com/mcp`
without a header).
