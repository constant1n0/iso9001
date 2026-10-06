# MCP Server

Repository locator: `odd/tasks/mcp-server.md` · Engram mirror: `odd/mcp-server/tasks`

## Objective

Let AI agents (Claude Code, Codex, Pi, OpenCode, OpenClaw, and Claude Desktop through `mcp-remote`) operate the QMS through an MCP server that acts as a real user via an API token, goes through the same services, policy, audit log and attribution as the web UI, and never deletes.

## Problem and why it matters

- The service layer (`qms-foundations`) and API tokens (`api-tokens`) are in place; no agent-facing adapter exists yet.
- Agents need a small, well-described tool surface: 12 modules with list, get, create and update each would be about 48 tools, which crowds the agent's context.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, delegated direct workers; branch `feat/mcp-server` in worktree `iso9001-worktrees/mcp-server` from `main@4dc2b01` | User: "sí, arranca mcp-server" (2026-10-04) |
| M1 | Five generic tools with writes: `qms_modules`, `qms_list`, `qms_get`, `qms_create`, `qms_update`, with the module as a parameter; no delete tool (D3 of `qms-foundations`) | User (recommended option) |
| M2 | Pin `mcp==2.3.0` (`mcp>=2.3,<3` compatible): its Streamable HTTP app serves both the 2025-era `initialize` handshake and the stateless 2026-07-28 protocol; fallback `mcp==1.30.0` if a client fails | Research (SDK release notes, client issue trackers) |
| M3 | Remote transport: `streamable_http_app(stateless_http=True, json_response=True)` under uvicorn at `/mcp`; `TransportSecuritySettings(allowed_hosts=[...])` from configuration. Local transport: stdio | Research |
| M4 | Authentication by our own pure-ASGI middleware: `Authorization: Bearer <token>` → `api_tokens.authenticate` (in a worker thread) → `ContextVar[Actor]`; failure is a 401 with a plain `WWW-Authenticate: Bearer` and no OAuth metadata. No SDK `AuthSettings` (it publishes OAuth discovery metadata that static-token clients would follow). Stdio authenticates the token from an environment variable at start | Research |
| M5 | Tools are synchronous (the SDK runs them on worker threads); each call pushes a Flask app context, uses its own session, commits on successful writes and rolls back on any error | Research; `docs/architecture/services.md` |
| M6 | Tool annotations: `qms_modules`, `qms_list`, `qms_get` read-only; `qms_create` not idempotent, not destructive; `qms_update` idempotent and destructive (it overwrites fields) | MCP tool annotation semantics |
| M7 | Deployment artefacts (systemd unit, client configuration docs) are in scope; deploying to production needs separate authorization | Remote-operation policy |
| Delivery | `auto-chain`, `stacked-to-main`, push/PR/merge when CI is green | User authorization (2026-10-02) |
| RDD | On (global) | `gentle-ai review mode status` |

## Scope

### Included

- `mcp` dependency, server package, module registry, the five tools.
- Bearer middleware, HTTP and stdio entry points, transport security configuration.
- Tests (in-memory client and HTTP over ASGI).
- systemd unit, client configuration guide for the six clients.

### Excluded

- Delete through MCP (D3).
- MCP resources and prompts (tools are the only portable primitive).
- Rate limiting per token (follow-up).
- Production deployment and editing the user's local client configurations.

## Constraints

- Same as previous features: Python 3.11 in CI, stdlib `unittest`, strict test-first, conventional commits without AI attribution, about 400 changed lines per work unit as a heuristic, English code and docs (data and validation messages stay Spanish).
- `requirements.txt` stays a full pinned freeze: add `mcp==2.3.0` and its resolved transitive pins without breaking existing pins.
- The local server package must not shadow the SDK: name it `app/mcp_server/`, never `app/mcp/`.
- Never log, return or store a token; tool results never include password or token fields (reuse the audit sensitive-field filter).

## Design

- **Package `app/mcp_server/`:**
  - `server.py` builds the `MCPServer` and registers the tools.
  - `registry.py` maps module slugs to services, policy resources, writable fields, allowed values and list filters.
  - `context.py` holds the actor `ContextVar`, the app-context helper and the error mapping.
  - `http.py` holds the ASGI bearer middleware and app factory.
  - `__main__.py` is the CLI: `python -m app.mcp_server --transport stdio|http --host --port`.
- **Module slugs (Spanish, like the UI):** `no_conformidades`, `auditorias`, `documentos`, `capacitaciones`, `satisfaccion_clientes`, `partes_interesadas`, `mejoras`, `roles_responsabilidades`, `riesgos_oportunidades`, `recursos_capacitacion`, `procesos`, `indicadores_auditoria`.
- **Tools:**
  - `qms_modules()` lists the modules with fields (type, required, allowed values), supported filters, and what the calling actor may do (from `policy.can`).
  - `qms_list(module, filters, page, per_page)` returns bounded pages.
  - `qms_get(module, id)` returns one record.
  - `qms_create(module, data)` and `qms_update(module, id, data)` return the stored record.
  - Records are serialised with `audit.snapshot` (JSON-safe, sensitive fields dropped).
  - Domain errors become MCP tool errors (`isError`) with the safe Spanish message; unexpected errors become a generic message and are logged.
- **Configuration (environment):**
  - `MCP_ALLOWED_HOSTS` (comma-separated);
  - `MCP_HOST` / `MCP_PORT` (default `127.0.0.1:8765`);
  - `ISO9001_MCP_TOKEN` for stdio;
  - the usual `SECRET_KEY` and `DATABASE_URI` from `.env`.

## Tasks

Route for every task: **delegated direct**.

- [x] **MS-1 — Dependency, server skeleton, actor context, `qms_modules`.** Forecast 300-380.
  - Acceptance: `mcp==2.3.0` and transitive pins added without conflicts; in-memory client lists exactly the five tool names (later tasks register the rest) and `qms_modules` returns all 12 modules with fields and the caller's permissions; no `app/mcp/` package.
- [x] **MS-2 — Read tools `qms_list` and `qms_get`.** Forecast 300-380.
  - Acceptance: every module lists and gets through its service; filters validated per module; paging bounded (reuse `crud.page_bounds` limits); a `read`-scoped token reads; unknown module or id gives a clean tool error.
- [x] **MS-3 — Write tools `qms_create` and `qms_update`.** Forecast 300-380.
  - Acceptance: writes go through services with policy, scopes, stamping and audit (`channel="mcp"`); a `read`-only token cannot write; OPERATIVO cannot write the JSON registers (D1); commit on success, rollback on error; validation and conflict errors are clean tool errors.
- [x] **MS-4 — Streamable HTTP, bearer middleware, entry points.** Forecast 300-380.
  - Acceptance: missing or invalid token gives 401 with plain `WWW-Authenticate: Bearer`, logged without the token; a valid token reaches the tools as its actor; two different tokens in consecutive requests never leak actors; disallowed `Host` is rejected; stdio authenticates `ISO9001_MCP_TOKEN` at start and refuses to start without a valid token; both legacy `initialize` (2025-06-18, 2025-11-25) and stateless 2026-07-28 requests are answered.
- [x] **MS-6 — Review fixes (`review-461c7a5e0cfa0b69`).** Stdio re-authenticates the token on every call (revocation, expiry and role changes apply immediately); uvicorn trusts `X-Forwarded-For` only from `MCP_TRUSTED_PROXIES` (default `127.0.0.1`, wildcard refused); enum filters come from the registry (no-conformidades `estado` is now an enum filter; legacy states list but cannot be filtered); explicit token extraction; `Module.paged_in_db`.
- [x] **MS-5 — Deployment and client guide.** Forecast 150-250.
  - Acceptance: `iso9001-mcp.service` systemd unit consistent with the existing units; `docs/mcp.md` with configuration for Claude Code, Codex, Pi, OpenCode, OpenClaw and Claude Desktop (`mcp-remote --header-file`), Traefik routing notes, token issuance with the CLI, and a per-client smoke-test checklist; README pointer.

## Checks

```bash
$ export TEST_POSTGRES_URI="postgresql://postgres:DB_PASSWORD@127.0.0.1:55432/iso_test"
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

- Baseline at `4dc2b01`: 468 tests.
- After each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json`; first boundary `4dc2b01`.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| MS-1 | Done | `23a099c` | RED: `ModuleNotFoundError: app.mcp_server`. GREEN: 478 tests | Pending |
| MS-2 | Done | `50d6919` | RED: `Unknown tool: qms_list` across 12 module subtests and filter subtests. GREEN: 487 | Covered by `review-461c7a5e0cfa0b69` |
| MS-3 | Done | `cb9d2b5` | RED: 8 write-tool tests failing. GREEN: 495 | Covered by `review-461c7a5e0cfa0b69` |
| MS-4 | Done | `3b82ee4` (Streamable HTTP + bearer middleware), `93201a9` (stdio/HTTP CLI) | RED: import errors for `http` and `__main__`. GREEN: 505 / 511 | Covered by `review-461c7a5e0cfa0b69` |
| MS-5 | Done | `7438adc` (systemd unit, `docs/mcp.md`, README) | RED: guide and unit missing. GREEN: 517; `pip check` clean | Pending |

## Findings during implementation

- **Dependencies:** `mcp==2.3.0` pulls `cryptography` 50, which forces `cffi` 2.x and breaks `weasyprint` 63; `cryptography==45.0.7` keeps `cffi==1.17.1`. `typing_extensions` moves 4.12.2 → 4.16.0; `idna==3.20` pinned. `PyJWT==2.15.1` returns as a transitive dependency of `mcp` (the cleanup test now forbids only `flask-jwt-extended`). The 26 `requirements.txt` lines are a lockfile freeze and are not counted as authored lines.
- **End-to-end smoke (real HTTP, SDK client, throwaway SQLite DB, token from the CLI):** five tools listed; `qms_modules` returned 12 modules for an administrator token; `qms_create` stored a nonconformity, `qms_get` read it back stamped with the owner; missing id gave a clean Spanish tool error; an unauthenticated POST got 401 `WWW-Authenticate: Bearer`; the token never appeared in the log. A real stdio subprocess resolved the token's actor and refused to start without a token.
- **Flaky test fixed before delivery:** `test_mcp_http.py` split a tampered token with `split("_")`; secrets can contain `_`, so it now uses `split("_", 2)` (amended into `3b82ee4`, never pushed).
- **Follow-ups:** plain registers list in full and the page is sliced in memory (fine at today's sizes); per-token rate limiting (documented as a known gap); real-client smoke tests (Codex `bearer_token_env_var`, Pi, OpenCode `{env:}` and OpenClaw config shapes are marked unverified in `docs/mcp.md`).
- **SDK notes:** raising `ToolError` returns its message with `is_error`; any other exception returns a generic message and is logged. A Starlette lifespan must wrap each async test body (anyio task groups must exit in the entering task).

## Delivery

| Slice | Pull request | Commits | Merged as |
|---|---|---|---|
| 1 | [#58](https://github.com/constant1n0/iso9001/pull/58) | `775f039` | `25a3660` |
| 2 | [#59](https://github.com/constant1n0/iso9001/pull/59) | `23a099c` | `f1c644f` |
| 3 | [#60](https://github.com/constant1n0/iso9001/pull/60) | `50d6919` | `62d54c8` |
| 4 | [#61](https://github.com/constant1n0/iso9001/pull/61) | `cb9d2b5` | `d75d682` |
| 5 | [#62](https://github.com/constant1n0/iso9001/pull/62) | `3b82ee4` | `957cd9e` |
| 6 | [#63](https://github.com/constant1n0/iso9001/pull/63) | `93201a9` | `ae3cc8b` |
| 7 | [#64](https://github.com/constant1n0/iso9001/pull/64) | `7438adc` | `a45cbf7` |
| 8 | [#65](https://github.com/constant1n0/iso9001/pull/65) | `1e50e4f`, `5a728e7`, `55e6636` | `6935de0` |

## Next step

**Feature complete in code.** Agents can now operate the QMS through five MCP tools, as a real user, under policy, token scopes, audit and attribution, without delete.

Remaining, outside this change:
1. ~~Production deployment~~ — done on 2026-10-04, see below.
2. **Real-client smoke tests** with Claude Code, Codex, Pi, OpenCode, OpenClaw and Claude Desktop (`docs/mcp.md` checklist), then mark the verified configuration shapes.
3. **Follow-ups:** per-token rate limiting; service-side paging for the 4 registers that page in memory; open small fixes from the gap analysis (monthly report totals, dashboard satisfaction chart); `qms-people` and Wave 1 modules.

## Production deployment (2026-10-04)

`main@6935de0` (qms-foundations, api-tokens and mcp-server) runs on `vulcano`. The user authorized this deployment to `vulcano`, over SSH as `dcm`, with no `sudo` by the agent.

| Step | Result |
|---|---|
| Preflight | Was `b794952`, migration `b7e2c9d41f03`; services active; data: 1 user, 0 domain records |
| Backups | `~/work/backups/calidad-20261004-pre-6935de0.dump` (`pg_dump -Fc`, verified with `pg_restore -l`) and `iso9001-code-20261004-pre-6935de0.tar.gz`, both mode 600; Traefik route backup `iso9001.yml.bak-20261004` |
| Code | Incremental git bundle `b794952..main`, fast-forward to `6935de0` |
| Dependencies | `pip install -r requirements.txt`; `pip check` clean; `mcp 2.3.0`, `cryptography 45.0.7`, `cffi 1.17.1` |
| Database | `flask db upgrade` to `e6b1a4c8d3f7` (`c4d8e1f2a9b7`, `d5a9f3b7c1e2`, `e6b1a4c8d3f7`); `flask db check` clean |
| Configuration | `.env`: `MCP_ALLOWED_HOSTS=calidad.absolutoffice.com`, `MCP_TRUSTED_PROXIES=172.18.0.0/16` |
| systemd (user, with sudo) | Restarted `iso9001`, `iso9001-celery-worker`, `iso9001-celery-beat`; installed `iso9001-mcp.service` with a drop-in binding `172.18.0.1:8765` (`After/Wants=docker.service`); enabled |
| Firewall (user, with sudo) | UFW: `172.18.0.0/16 → 172.18.0.1:8765/tcp` |
| Traefik | Router `iso9001-mcp`: `Host(calidad.absolutoffice.com) && PathPrefix(/mcp)` → `172.18.0.1:8765`, middlewares `vpn-only`, `sec-headers`, `ratelimit` (average 100, burst 50), access log off |
| Smoke tests | All four services active and enabled; from the allowed LAN: `/login` 200 with HSTS; `/mcp` 401 with `WWW-Authenticate: Bearer` without a token and with an invalid token; Traefik container reaches the MCP; Celery worker ready |

Pending after deployment:
- ~~the administrator issues personal tokens with `flask create-api-token` on `vulcano`~~ done (2026-10-04);
- real-client smoke tests per `docs/mcp.md`: **Claude Code works** against production over Streamable HTTP with a personal read/write token (`claude mcp add --transport http --scope user`, reported by the user on 2026-10-06); Codex, OpenCode, Pi and OpenClaw verified against a local server built from `main@69bcfa7` (2026-10-06, see `docs/mcp.md`); Claude Desktop remains untested;
- users are informed of the permission changes (only ADMIN deletes; OPERATIVO cannot write the JSON registers).
