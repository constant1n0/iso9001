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

- [x] **RL-1 — Real client address.** Forecast 150-250.
  - WSGI middleware applied in `create_app`, configured from `TRUSTED_PROXIES`; `security_logger.get_client_ip()` returns `request.remote_addr`.
  - Acceptance: a trusted peer's `X-Forwarded-For` sets the client address (right-most untrusted hop) and `X-Forwarded-Proto` sets the scheme; an untrusted peer's headers are ignored; `*` is refused at start-up; the rate-limit key and the security log see the resolved address.
- [x] **RL-2 — Shared counters, no blanket limit.** Forecast 80-150.
  - `RATELIMIT_STORAGE_URI`, `RATELIMIT_KEY_PREFIX`, `RATELIMIT_IN_MEMORY_FALLBACK_ENABLED` in the configuration; no `default_limits`; README and deployment notes list the new variables.
  - Acceptance: the storage URI reaches Flask-Limiter; ordinary pages are never limited; every targeted limit still answers 429 when exceeded.
- [x] **RL-3 — Production deployment** (after authorization): `.env` gains `TRUSTED_PROXIES` (the Traefik network) and `RATELIMIT_STORAGE_URI` (the existing Redis, its own database index); restart by the user; smoke tests.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `3fafdda`: 659 tests. RDD on: assess each work-unit commit.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| RL-1 | Done | `cff9b8a` | RED: missing module; then 7 integration tests (spoofed `1.2.3.4` logged, second client behind the proxy refused `302 != 429`, scheme not taken, `*` accepted). GREEN: 683 tests incl. PostgreSQL | Range `3fafdda..3172798`: **high**; consent granted; 4-lens review `review-1ed5d2375ba8e9ed` **approved** and acknowledged; risk lens no findings |
| RL-2 | Done | `3172798`, review follow-up in the next commit | RED: missing `RATELIMIT_*` settings, no `iso9001` key prefix, ordinary page refused within 75 requests, no fallback on a refused connection. GREEN: 691 tests; the in-memory start-up warning is gone | Same review; applied: Redis connect and socket timeouts of 1 s (`RATELIMIT_STORAGE_OPTIONS` for `redis://`/`rediss://`), shared `build_app_with_schema` test helper, prefix test no longer tied to the private key layout. 694 tests |
| RL-3 | Done | deployed `86275f9` (PR [#76](https://github.com/constant1n0/iso9001/pull/76), merged as `86275f9`) | See the deployment record below | — |

## Findings during implementation

- Gunicorn's `forwarded_allow_ips` only sets the scheme; it never rewrites `REMOTE_ADDR`. `gunicorn.conf.py` still trusts `*` for that header, so a peer that reaches Gunicorn directly can claim `https`; limiting it to the Traefik network is a follow-up.
- `TRUSTED_PROXIES` also refuses `0.0.0.0/0`, `::/0` and networks written with host bits; a malformed `X-Forwarded-For` hop stops the walk, so the request stays attributed to the proxy rather than to a value the client wrote.
- Flask-Limiter skips its storage set-up when `RATELIMIT_ENABLED` is false and keeps prefix and dead-storage state on the shared limiter across `init_app` calls; tests that need a dead storage use their own limiter.
- With the default `memory://`, Flask-Limiter no longer warns at start-up even when production forgets `RATELIMIT_STORAGE_URI`; the README says so, and the deployment sets it explicitly.

## Production deployment (2026-10-06)

The user authorized this deployment to `vulcano` over SSH as `dcm`, with no `sudo` by the agent.

| Step | Result |
|---|---|
| Preflight | Was `e378bb9`, clean; no migrations or dependency changes |
| Backups | `~/work/backups/iso9001-code-*-pre-86275f9.tar.gz` and `env-*-pre-86275f9`, both mode 600 |
| Code | Incremental git bundle `e378bb9..main`, fast-forward to `86275f9` |
| Configuration | `.env` gained `TRUSTED_PROXIES=172.18.0.0/16` and `RATELIMIT_STORAGE_URI` on Celery's Redis (`127.0.0.1:6380`, same password) in database index 2, which was empty and answered `PING`; the app loads both, with key prefix `iso9001` and 1 s Redis timeouts |
| Restart (user, with sudo) | `iso9001`, `iso9001-celery-worker`, `iso9001-celery-beat`, `iso9001-mcp` |
| Smoke tests | Services active, no error entries in the journal; `/login` 200; `/mcp` 401 with `WWW-Authenticate: Bearer`; five wrong logins for a made-up user answered 302 and the sixth 429; the security log shows the workstation's real LAN address (`192.168.101.x`) instead of the Traefik address (`172.18.x.x`) and a `RATE_LIMIT_EXCEEDED` line; Redis database 2 holds the `LIMITER/iso9001/<client>/auth.login/5/1/minute` counter |

A POST over HTTPS now needs a matching `Referer`, because Flask-WTF sees the real `https` scheme; browsers send it, scripted clients must too.

## Next step

**Feature complete and deployed.** Follow-up: restrict Gunicorn's `forwarded_allow_ips` (still `*`) to the Traefik network.
