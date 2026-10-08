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
  | `qms_modules` | The 13 modules with fields, allowed values, filters and what the caller may do |
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
| `MCP_TOKEN_RATE_LIMIT` | `120/minute` | HTTP only: requests per API token, counted after authentication |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | `20/minute` | HTTP only: failed bearer authentications per client address |
| `RATELIMIT_STORAGE_URI` | `memory://` | Where both limits are counted, shared with the web application (Redis in production) |

Run it with the systemd unit `iso9001-mcp.service` (copy, `daemon-reload`,
`enable --now`; see the header of the file), or by hand:

```bash
venv/bin/python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765
```

stdio re-authenticates the configured token on every tool call, so revoking it,
letting it expire or changing its owner's role takes effect on the next call
(a rejected call returns a tool error; the server keeps running).

`qms_list` pages every module in the database: each service's `list_page`
counts and fetches one page with the same filters and order as its `list_`
(a test checks that every registered service has one).

Known gap (follow-up): the `estado` filter of `no_conformidades` accepts only
the fixed states, so legacy free-text states cannot be filtered on.

A missing or invalid token gets `401` with `WWW-Authenticate: Bearer`; failures
are written to the security log (prefix and reason, never the token). Deploying
to production needs its own authorization.

### Rate limits

The HTTP transport applies two limits, written in `limits` notation (one rate
such as `120/minute`; a value that is not exactly one rate of at least one
request stops the server at start-up with exit code 2):

- **Per token** (`MCP_TOKEN_RATE_LIMIT`, default `120/minute`): every
  authenticated request counts against its token. Over the limit the request
  gets `429` and the security log records
  `API_TOKEN_RATE_LIMITED | prefix=<prefix> | ip=<address>`.
- **Per address, failed tokens** (`MCP_AUTH_FAILURE_RATE_LIMIT`, default
  `20/minute`): every failed bearer authentication (missing, malformed, unknown,
  revoked, expired...) counts against the client address, after the token is
  looked up, with one atomic hit, so parallel attempts cannot overshoot it.
  Once an address is over the limit, its failing requests get `429` instead of
  `401` and the security log records `MCP_AUTH_RATE_LIMITED | ip=<address>`
  next to the usual `API_TOKEN_AUTH_FAILED` line. A valid token from the same
  address is never refused by this limit, only by its own per-token limit.
  Requests without a client address share one failure bucket (`-`).

A refusal is `429` with a `Retry-After` header (seconds) and the body
`{"error": "Demasiadas solicitudes. Inténtelo de nuevo más tarde."}`. Requests
under both limits behave exactly as without them. The windows are moving
windows when the storage supports them (memory and Redis do), otherwise fixed
windows, counted in `RATELIMIT_STORAGE_URI` under the keys
`iso9001:mcp:token:<prefix>` and `iso9001:mcp:auth-fail:<address>`; the address
is the one uvicorn resolves, so behind Traefik keep `MCP_TRUSTED_PROXIES` right
or every caller shares the proxy's budget.

The limits fail open: if the storage stops answering (Redis down), requests are
served without limits and the `app.mcp_server.rate_limit` logger writes one
warning naming the error type (never the storage URI), plus one info line when
the storage answers again. A `redis://` or `rediss://` storage whose
`RATELIMIT_STORAGE_OPTIONS` lack `socket_connect_timeout` / `socket_timeout`
gets one second for each, so a stalled Redis delays a request by about a
second instead of the TCP timeout. An unusable
`RATELIMIT_STORAGE_URI` (unknown scheme, malformed, driver not installed) stops
the server at start-up with exit code 2 and a message without its credentials.

stdio has no rate limits: it runs locally as one configured token, so whoever
can start it already has access to the server itself.

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
claude mcp add-json --scope user iso9001 \
  '{"type":"http","url":"https://qms.example.com/mcp","headers":{"Authorization":"Bearer ${ISO9001_TOKEN}"}}'
```

The single quotes keep `${ISO9001_TOKEN}` literal: Claude Code stores the
placeholder and expands it from the environment when it connects, so the token
never lands in `~/.claude.json`. This also works, but the shell expands the
variable first and the token is stored in plain text:

```bash
claude mcp add --transport http iso9001 https://qms.example.com/mcp \
  --header "Authorization: Bearer ${ISO9001_TOKEN}"
```

Avoid `--scope project`: it writes `.mcp.json` into the repository (and needs
per-project approval).

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

`~/.pi/agent/mcp.json` (or a project `.pi/mcp.json`):

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

Pi calls MCP tools from its code mode by default (`mcp__iso9001__qms_modules`).
A project `.pi/mcp.json` is read by `pi -p`, but `pi mcp list` ignores it until
the project is trusted.

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

`opencode mcp list` shows whether it connected. For one-shot runs
(`opencode run`) close standard input (`< /dev/null`); a run left waiting on it
never reached the server.

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

`openclaw mcp probe iso9001` lists the tools. `openclaw agent` runs through the
gateway, which reads the gateway's own configuration; add `--local` to use the
configuration above directly.

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

- **Claude Code**: connected to production over Streamable HTTP with a
  personal token, reported by the maintainer (2026-10-06).
- **Codex 0.157, OpenCode 1.18, Pi 1.0, OpenClaw 2026.7 and Claude Code**: the
  snippets above (with a local URL) connected to a local server built from
  `main@69bcfa7` and called `qms_modules` with a read-only token, each through
  its normal one-shot command; the server log recorded the calls (2026-10-06).
  Each client's `${...}` or `{env:...}` placeholder was expanded from the
  environment.
- **Claude Desktop through `mcp-remote`**: not tested (no Linux build); the
  `--header` / `--header-file` options follow the `mcp-remote` v0.14.3
  documentation.

The server is stateless: it sends no `Mcp-Session-Id` and answers with JSON.

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
