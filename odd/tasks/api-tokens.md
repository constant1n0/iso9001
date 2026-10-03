# API Tokens

Repository locator: `odd/tasks/api-tokens.md` · Engram mirror: `odd/api-tokens/tasks`

## Objective

Give each user revocable, expiring API tokens that an agent adapter (the future MCP server) exchanges for an `Actor(channel="mcp", scopes=...)`, so AI agents act as a real user, under that user's role, narrowed by the token's scopes, and every write stays attributable.

## Problem and why it matters

- The service layer from `qms-foundations` already accepts an `Actor` with a channel and scopes, and the policy narrows a role by scopes (decision D7 of that feature). Nothing can yet authenticate a non-browser client.
- Agent clients (Claude Code, Codex, Pi, OpenCode, OpenClaw, Claude Desktop via `mcp-remote`) all support a static `Authorization: Bearer` header (`docs/iso-9001-gap-analysis.md` context; Engram #10726).
- Flask-JWT-Extended and PyJWT are installed but unused.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, delegated direct workers; branch `feat/api-tokens` in worktree `iso9001-worktrees/api-tokens` from `main@24a4495` | User chose `api-tokens` as the next change (2026-10-03) |
| T1 | Tokens are issued, listed and revoked only by an administrator through the Flask CLI; no web screen in this change | User (recommended option) |
| T2 | Token = `iso_<prefix>_<secret>`: 8-hex random prefix for lookup, 256-bit `secrets.token_urlsafe(32)` secret; shown once | Assistant proposal (exploration), not objected |
| T3 | Store only `HMAC-SHA256(key, token)` with the key derived from `SECRET_KEY`; compare with `hmac.compare_digest`. Rotating `SECRET_KEY` invalidates every token (documented) | Assistant proposal |
| T4 | Scopes `read` and `write`; default `read` only (least privilege); scopes only narrow the owner's role | Assistant proposal; D7 |
| T5 | Default expiry 90 days, maximum 365; revocation is immediate; `last_used_at` updated at most every 5 minutes | Assistant proposal |
| T6 | Authentication yields `Actor(channel="mcp", user_id, label, role=<owner's current role>, scopes=<token scopes>)`; unknown, malformed, expired or revoked tokens fail with one generic error | Assistant proposal |
| T7 | Issuing and revoking are written to the audit log and the security log; the secret and its hash never appear in either | Assistant proposal |
| T8 | Remove the unused `Flask-JWT-Extended` and `PyJWT` dependencies | Assistant proposal |
| Delivery | `auto-chain`, `stacked-to-main`, push/PR/merge when CI is green, as in `qms-foundations` | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Scope

### Included

- `ApiToken` model and Alembic migration.
- Token service: issue, authenticate, revoke, list.
- CLI commands and a CLI actor.
- Policy resource for token management.
- Dependency cleanup and documentation.

### Excluded

- The MCP server itself (next change, `mcp-server`).
- Bearer authentication on the existing Flask JSON API.
- A web screen for tokens (T1).
- User management, `qms-people`, production deployment.

## Constraints

- Same as `qms-foundations`: Python 3.11 in CI (worktree `venv` uses 3.12), stdlib `unittest`, strict test-first, Alembic migrations for PostgreSQL 17, services never import Flask, Spanish UI copy and English code/docs, conventional commits without AI attribution, about 400 changed lines per work unit as a planning heuristic.
- Never log, print (except the one-time CLI display), store or audit the plaintext token; never store or audit its hash.

## Design

- **Table `api_tokens`:** `id`; `user_id` (FK `users.id`, `ON DELETE CASCADE`); `name`; `prefix` (unique, 8 hex); `token_hash` (64 hex); `scopes` (text set, e.g. `read` or `read write`); `created_at`, `created_by_label`; `expires_at`; `revoked_at`, `revoked_by_label`; `last_used_at`. Indexes on `prefix` (unique) and `user_id`.
- **Service `app/services/api_tokens.py`** (framework-free):
  - `issue(session, actor, *, secret_key, user_id, name, scopes=("read",), days=90, now=None)` returns `(plaintext, ApiToken)`.
  - `authenticate(session, raw, *, secret_key, now=None)` returns an `Actor` or raises `AuthenticationFailed`.
  - `revoke(session, actor, token_id_or_prefix)`.
  - `list_(session, actor, user_id=None)`.
  - Policy resource `API_TOKENS`: ADMIN manages, nobody else.
  - Issue and revoke are audited; the audit recorder already drops any field whose name contains `token`.
- **CLI** (`app/commands.py`): `flask create-api-token --user USERNAME --name NAME [--scope read|write ...] [--days N]`, `flask list-api-tokens [--user USERNAME]`, `flask revoke-api-token PREFIX`. The CLI acts as `Actor(channel="cli")` with administrator rights, because it requires server access.

## Tasks

Route for every task: **delegated direct** (each touches two or more non-trivial files).

- [x] **AT-1 — `ApiToken` model and migration.** Forecast 200-300.
  - Acceptance: table, constraints and indexes as designed; migration upgrades and downgrades on PostgreSQL; deleting a user deletes their tokens.
- [x] **AT-2 — Token service.** Forecast 300-380.
  - Acceptance: issue returns a plaintext once and stores only the HMAC; authenticate accepts a valid token and rejects unknown, malformed, expired, revoked and tampered tokens with one generic error; the resulting `Actor` carries channel `mcp`, the owner's current role and the token scopes; `last_used_at` is throttled; issue and revoke are audited without the secret or hash; only ADMIN may issue, list or revoke.
- [x] **AT-3 — CLI commands.** Forecast 200-300.
  - Acceptance: create, list and revoke work through `flask`; the plaintext is printed once on create; list never prints secrets or hashes; errors are clear and exit non-zero.
- [x] **AT-5 — Review fixes (`review-6718387f79eedcee`).** One digest function for issue and authenticate; shared `status()` for authentication and CLI; clear CLI error without `SECRET_KEY`; `AuthFailure` enum; collision/`IntegrityError` tests; adapter rollback documented.
- [x] **AT-4 — Dependency cleanup and documentation.** Forecast 60-150.
  - Acceptance: `Flask-JWT-Extended` and `PyJWT` removed with nothing importing them; `docs/architecture/services.md` documents token authentication and how the MCP adapter must use it; README shows the CLI usage.

**Total forecast:** about 760-1,130 authored changed lines, so delivery is chained.

## Checks

```bash
$ export TEST_POSTGRES_URI="postgresql://postgres:DB_PASSWORD@127.0.0.1:55432/iso_test"
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

`DB_PASSWORD` comes from the `iso-pg-test` container environment and is never printed or committed.

- Baseline at `24a4495`: 403 tests (from `qms-foundations`).
- After each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json`; the first boundary is the branch point `24a4495`.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| AT-1 | Done | `8b5ea23` (table + migration `e6b1a4c8d3f7`) | RED: `ImportError: ApiToken`; migration test: `api_tokens` missing. GREEN: 409 tests incl. PostgreSQL (cascade on user delete, upgrade/downgrade) | Range `24a4495..a98d8a9` (AT-1..AT-4): **high** risk (auth/security hot paths); consent granted; 4-lens review `review-6718387f79eedcee` **approved** and acknowledged; risk lens no findings; 3 warnings + 5 suggestions fixed in AT-5 |
| AT-2 | Done | `50d9b67` (issue + authenticate, `AuthenticationFailed`, policy `API_TOKENS`), `1c065e5` (revoke + list) | RED: missing `app.services.api_tokens`, `KeyError: 'API_TOKENS'`. GREEN: 434 / 439 tests | Pending |
| AT-3 | Done | `93426f1` (security log functions), `a79147c` (CLI commands) | RED: missing `log_api_token_*`, 12 CLI tests failing. GREEN: 442 / 454 tests | Pending |
| AT-4 | Done | `cb7d76a` (JWT packages dropped, docs, README) | RED: 3 of 4 cleanup tests. GREEN: 458 tests | — |
| AT-5 | Done | `52af101` (review fixes) | RED: shared digest not used by `authenticate`; no `status` helper; missing/empty `SECRET_KEY` gave a raw traceback; no `AuthFailure` enum. GREEN: 468 tests incl. PostgreSQL | Medium, `under_budget` (188 lines); no later commit reaches the budget, so it stays unreviewed by RDD; it only applies the approved review's findings |

## Findings during implementation

- **Not in `AUDITED_MODELS`:** `ApiToken` has no `created_by_id`/`updated_by_id` and `last_used_at` changes on authentication without an audit row; `issue` and `revoke` call `audit.record` explicitly, and the recorder drops `token_hash` because its name contains `token`.
- **Key derivation:** `hmac(SECRET_KEY, b"iso9001-api-token-v1", sha256)`; services receive `secret_key=` from the adapter. Unknown prefixes are compared against a dummy hash so both paths do the same work.
- **Errors:** `AuthenticationFailed` has one generic message and carries `reason`/`token_prefix` for the adapter's security log only. A second revoke raises `Conflict`.
- **CLI actor:** `Actor(user_id=None, label="cli:<OS user>", role=ADMINISTRADOR, channel="cli")`. CLI messages are English like `create-admin`; service validation messages are Spanish.
- **Policy:** `API_TOKENS` read/create/update ADMIN only, delete nobody (revocation is an update); `mcp` is denied every action.
- **For the MCP adapter:** split tokens with `split("_", 2)` (secrets can contain `_`); `authenticate` only flushes `last_used_at`, so the adapter commits after a successful call.
- **Commit size:** `50d9b67` is 515 lines (service + errors + policy + tests); one honest slicing pass found no cohesive split, so its pull request needs a maintainer `size:exception`.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#53](https://github.com/constant1n0/iso9001/pull/53) | `53286de`, `8b5ea23` | `7457bfe` | |
| 2 | [#54](https://github.com/constant1n0/iso9001/pull/54) | `50d9b67` | `6bbe0e0` | maintainer-approved `size:exception` (515) |
| 3 | [#55](https://github.com/constant1n0/iso9001/pull/55) | `1c065e5`, `93426f1` | `728427c` | |
| 4 | [#56](https://github.com/constant1n0/iso9001/pull/56) | `a79147c` | `52cc731` | |
| 5 | Pending | `cb7d76a`, `a98d8a9`, `52af101`, this closing update | — | Final slice |

## Next step

**Feature complete.** Tokens can be issued, listed and revoked by an administrator through the CLI, and `api_tokens.authenticate(...)` turns a bearer token into an `Actor(channel="mcp", ...)` for the next change.

Next: `mcp-server` — the MCP server as a separate ASGI process (Streamable HTTP, bearer token; stdio for local use) over the service layer, following `docs/architecture/services.md`.
