# Rate-Limit Storage and Client Address

Repository locator: `odd/tasks/rate-limit-storage.md` · Engram mirror: `odd/rate-limit-storage/tasks`

## Objective

Make rate limits count real clients across every web worker: identify the client behind Traefik safely, keep the counters in Redis, and drop the blanket default limit that would block normal use once the counters are real.

## Problem and why it matters

- Flask-Limiter keeps its counters in process memory (`app/extensions.py`), so each Gunicorn worker counts separately, counters reset on restart, and production logs a warning on every start and CLI command.
- The web app runs behind Traefik, but nothing turns `X-Forwarded-For` into the client address: Gunicorn does not rewrite `REMOTE_ADDR` and the app has no proxy middleware. Every request therefore appears to come from Traefik, so all users share one counter per worker (including the login limit of 5 per minute).
- `security_logger.get_client_ip()` copies the first `X-Forwarded-For` value verbatim, so any client can choose the address written to the security log.
- `Limiter(default_limits=["200 per day", "50 per hour"])` applies to every non-static route. With real, shared counters it would block a user after 50 page views in an hour.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, delegated direct writer; branch `feat/rate-limit-storage` in worktree `iso9001-worktrees/fixes-2026` from `main@3fafdda` | User chose this follow-up (2026-10-05) |
| R1 | Honour `X-Forwarded-For` / `X-Forwarded-Proto` only when the direct peer is in `TRUSTED_PROXIES` (comma-separated addresses or CIDR networks; `*` refused, as `MCP_TRUSTED_PROXIES` does); the client is the right-most address that is not a trusted proxy. Empty by default (development and tests) | Assistant design |
| R2 | The security log uses the resolved `request.remote_addr`, never a raw header | Assistant design |
| R3 | Counters in the storage named by `RATELIMIT_STORAGE_URI` (Redis in production), default `memory://`; key prefix `iso9001`; in-memory fallback when the storage is unreachable, so logins keep working if Redis is down | Assistant design |
| R4 | Remove the blanket default limit; keep the targeted ones (login 5/min, reset request 3/h, administrator reset link 10/h, credential changes 10/h per account) | User choice (2026-10-05) |
| Delivery | One pull request if under about 400 changed lines; merge when CI is green; production deployment needs `.env` changes (`TRUSTED_PROXIES`, `RATELIMIT_STORAGE_URI`) and explicit authorization | Previous authorization for PRs; deployment asked separately |

## Tasks

Route for every task: **delegated direct** (two or more non-trivial files each).

- [ ] **RL-1 — Real client address.** Forecast 150-250.
  - WSGI middleware applied in `create_app`, configured from `TRUSTED_PROXIES`; `security_logger.get_client_ip()` returns `request.remote_addr`.
  - Acceptance: a trusted peer's `X-Forwarded-For` sets the client address (right-most untrusted hop) and `X-Forwarded-Proto` sets the scheme; an untrusted peer's headers are ignored; `*` is refused at start-up; the rate-limit key and the security log see the resolved address.
- [ ] **RL-2 — Shared counters, no blanket limit.** Forecast 80-150.
  - `RATELIMIT_STORAGE_URI`, `RATELIMIT_KEY_PREFIX`, `RATELIMIT_IN_MEMORY_FALLBACK_ENABLED` in the configuration; no `default_limits`; README and deployment notes list the new variables.
  - Acceptance: the storage URI reaches Flask-Limiter; ordinary pages are never limited; every targeted limit still answers 429 when exceeded.
- [ ] **RL-3 — Production deployment** (after authorization): `.env` gains `TRUSTED_PROXIES` (the Traefik network) and `RATELIMIT_STORAGE_URI` (the existing Redis, its own database index); restart by the user; smoke tests.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `3fafdda`: 659 tests. RDD on: assess each work-unit commit.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| RL-1..RL-3 | Pending | — | — | — |

## Next step

RL-1.
