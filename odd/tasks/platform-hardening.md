# Platform Hardening

Repository locator: `odd/tasks/platform-hardening.md` · Engram mirror: `odd/platform-hardening/tasks`

## Objective

Close three known gaps before more agents use the system: Gunicorn trusting proxy headers from anyone, the MCP server having no rate limit, and five registers that the MCP pages in memory.

## Problem and why it matters

- `gunicorn.conf.py` sets `forwarded_allow_ips = '*'` and `proxy_allow_ips = '*'`, so any peer that reaches Gunicorn directly can claim `https` through `X-Forwarded-Proto`. The app already resolves proxy headers itself, only for `TRUSTED_PROXIES` (PR #76), and Gunicorn 23 cannot express CIDR networks anyway.
- The MCP HTTP server has no rate limit: a valid token can call tools without bound, and failed bearer tokens can be tried without bound (`docs/mcp.md`, known gaps). Only Traefik's coarse limit (average 100, burst 50) applies.
- `no_conformidades`, `documentos`, `capacitaciones`, `satisfaccion_clientes` and `partes_interesadas` have no `list_page`, so `qms_list` loads every row and slices the page in memory (`app/mcp_server/operations.py`, `paged_in_db=False` in `registry.py`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one writer at a time; branch `feat/platform-hardening` in worktree `iso9001-worktrees/fixes-2026` from `main@5c9765d` | User: "todas, elige tú el orden" (2026-10-06) |
| P1 | Gunicorn no longer trusts proxy headers from any peer: drop `forwarded_allow_ips = '*'` and `proxy_allow_ips = '*'` (defaults: loopback only; PROXY protocol stays off). The app's `TRUSTED_PROXIES` middleware decides scheme and client address | Assistant design |
| P2 | MCP HTTP rate limits with the `limits` library on the web app's storage (`RATELIMIT_STORAGE_URI`, Redis in production), failing open with a log line if the storage is down: per token after authentication (default `120/minute`, `MCP_TOKEN_RATE_LIMIT`), and per client address for failed bearer tokens before the database lookup (default `20/minute`, `MCP_AUTH_FAILURE_RATE_LIMIT`). Refusal is HTTP 429 with `Retry-After` and a generic Spanish message; the security log records the token prefix or the address, never the token. stdio is local and stays unlimited | Assistant design |
| P3 | Database paging (`list_page` returning rows and total, stable order, same filters as `list_`) for the five registers; the registry marks them `paged_in_db=True`. The web HTML lists keep rendering every row (adding pagination controls there is a separate change) | Assistant design |
| Delivery | Small pull requests, merged when CI is green; one production deployment at the end after explicit authorization | User authorization for PRs (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

- [ ] **PH-1 — Gunicorn proxy trust.** Route: inline (one configuration file and one test). Forecast 20-40.
  - Acceptance: `gunicorn.conf.py` sets no `forwarded_allow_ips` or `proxy_allow_ips` wildcard; a test pins it; README notes that proxy headers are handled by `TRUSTED_PROXIES`.
- [ ] **PH-2 — MCP rate limits.** Route: delegated (middleware, context, configuration, security log, tests). Forecast 250-400.
  - Acceptance: the per-token limit answers 429 with `Retry-After` and logs the prefix; failed bearer tokens from one address are refused with 429 before the database lookup once over the limit; limits are configurable; a storage outage does not block valid requests; existing MCP tests stay green.
- [ ] **PH-3 — Database paging for five registers.** Route: delegated (five services, registry, tests). Forecast 300-450.
  - Acceptance: each service has `list_page` with the same filters and order as `list_`, counted in the database; `qms_list` uses it for all five; paging and totals are tested per register; `docs/mcp.md` no longer lists them as paged in memory.
- [ ] **PH-4 — Production deployment** after explicit authorization.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `5c9765d`: 694 tests.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| PH-1..PH-4 | Pending | — | — | — |

## Next step

PH-1.
