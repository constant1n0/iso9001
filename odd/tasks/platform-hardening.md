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

- [x] **PH-1 — Gunicorn proxy trust.** Route: inline (one configuration file and one test). Forecast 20-40.
  - Acceptance: `gunicorn.conf.py` sets no `forwarded_allow_ips` or `proxy_allow_ips` wildcard; a test pins it; README notes that proxy headers are handled by `TRUSTED_PROXIES`.
- [x] **PH-2 — MCP rate limits.** Route: delegated (middleware, context, configuration, security log, tests). Forecast 250-400.
  - Acceptance: the per-token limit answers 429 with `Retry-After` and logs the prefix; failed bearer tokens from one address are refused with 429 before the database lookup once over the limit; limits are configurable; a storage outage does not block valid requests; existing MCP tests stay green.
- [x] **PH-3 — Database paging for five registers.** Route: delegated (five services, registry, tests). Forecast 300-450.
  - Acceptance: each service has `list_page` with the same filters and order as `list_`, counted in the database; `qms_list` uses it for all five; paging and totals are tested per register; `docs/mcp.md` no longer lists them as paged in memory.
- [x] **PH-4 — Production deployment** after explicit authorization.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `5c9765d`: 694 tests.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| PH-1 | Done | `1dae970` (route: inline) | RED: 2 `test_gunicorn_config` tests (`*` found). GREEN; access-log tests unchanged | Range `5c9765d..108ee2c`: **high**; consent granted; 4-lens review `review-efaa043a33d55277` **approved** and acknowledged |
| PH-2 | Done | `108ee2c`, review follow-ups `e03aded`, `f2bb09f` | RED: missing `rate_limit` module, no `Actor.token_prefix`; for the follow-ups: a valid token behind a throttled address got 429, 8 parallel failures all got 401, Redis options without timeouts, storage errors escaping `main`, a decoded password echoed in the start-up error. GREEN: 709, then 733, then 734 tests | Same review: the pre-authentication gate locked valid tokens out behind a noisy address and was not atomic, so failures are now counted after authentication with one atomic hit and only failing requests get 429; 1 s Redis timeouts by default; storage errors exit 2 without credentials; docs cover the window strategy and the shared bucket without an address. Range `108ee2c..e03aded` (**medium**, `slice_budget_reached`): reliability review `review-bce8c6165b608f49` **approved** and acknowledged; its redaction warning fixed in `f2bb09f`; its other warning (a throttled address still reaches the token lookup) is the accepted trade-off of failure-only refusals with 256-bit tokens |
| PH-3 | Done | `de63828`; dead in-memory branch and `paged_in_db` removed in `e03aded` | RED: 19 `list_page` tests (`AttributeError`), MCP paging `(3, 2, 1, False) != (0, 2, 0, False)` for the five modules. GREEN: 730 tests | Covered by `review-bce8c6165b608f49` |
| PH-4 | Done | merged as `69bcfa7` (PR [#79](https://github.com/constant1n0/iso9001/pull/79)); copied to `vulcano` on 2026-10-06, activated by the restart of the `3206275` deployment on 2026-10-09 | See below | — |

## Findings during implementation

- Gunicorn 23 validates `forwarded_allow_ips` with `ipaddress.ip_address` and matches by list membership, so it cannot express the Traefik network; its default (loopback) plus the app's `TRUSTED_PROXIES` middleware is the cleaner split.
- The MCP actor now carries `token_prefix` (never the secret or hash). Limits: `MCP_TOKEN_RATE_LIMIT` (default `120/minute`) and `MCP_AUTH_FAILURE_RATE_LIMIT` (default `20/minute`) on the web app's `RATELIMIT_STORAGE_URI`, moving window when the storage supports it; a storage outage fails open with one warning and one recovery line; invalid settings stop the server with exit code 2.
- `limits.parse` silently drops anything after `;` and accepts `0/minute`, so the settings are validated with `parse_many`.
- Every MCP module now pages in the database; `operations.list_records` always calls `list_page`.

## Production deployment (2026-10-06 and 2026-10-09)

The code (`69bcfa7`) was copied to `vulcano` on 2026-10-06 (backup `iso9001-code-*-pre-69bcfa7.tar.gz`); it went live with the restart of the `qms-people` deployment (`3206275`):

| Step | Result |
|---|---|
| Preflight | Was `69bcfa7` (platform hardening copied but not yet restarted), clean, migration `f2c7a9e4b1d6` |
| Backups | `~/work/backups/calidad-*-pre-3206275.dump` (`pg_dump -Fc`, 164 entries listed by `pg_restore -l`) and `iso9001-code-*-pre-3206275.tar.gz`, both mode 600 |
| Code | Incremental git bundle `69bcfa7..main`, fast-forward to `3206275`; `pip check` clean, no dependency changes |
| Database | `flask db upgrade` `f2c7a9e4b1d6` → `a3c5e7f9b2d4` → `b8d2f4a6c1e3` → `c9e3a5b7d1f4` before the restart; `flask db check` clean |
| Restart (user, with sudo) | `iso9001`, `iso9001-celery-worker`, `iso9001-celery-beat`, `iso9001-mcp` (also activates the platform hardening) |
| Smoke tests | Services active, no error entries in the journal; `/login` 200; `/personas/` and `/competencias/matriz` redirect to the login; the MCP registry has 15 modules; 21 consecutive bad bearer tokens from one address got 20 × 401 then 429 with `Retry-After: 60`, and the security log recorded `API_TOKEN_AUTH_FAILED` and `MCP_AUTH_RATE_LIMITED` with the real client address |

## Next step

**Feature complete and deployed.**
