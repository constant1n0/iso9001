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
  - `issue(session, actor, user_id, name, scopes, days)` returns `(plaintext, ApiToken)`.
  - `authenticate(session, raw, now)` returns an `Actor` or raises `AuthenticationFailed`.
  - `revoke(session, actor, token_id_or_prefix)`.
  - `list_(session, actor, user_id=None)`.
  - Policy resource `API_TOKENS`: ADMIN manages, nobody else.
  - Issue and revoke are audited; the audit recorder already drops any field whose name contains `token`.
- **CLI** (`app/commands.py`): `flask create-api-token --user USERNAME --name NAME [--scope read|write ...] [--days N]`, `flask list-api-tokens [--user USERNAME]`, `flask revoke-api-token PREFIX`. The CLI acts as `Actor(channel="cli")` with administrator rights, because it requires server access.

## Tasks

Route for every task: **delegated direct** (each touches two or more non-trivial files).

- [ ] **AT-1 — `ApiToken` model and migration.** Forecast 200-300.
  - Acceptance: table, constraints and indexes as designed; migration upgrades and downgrades on PostgreSQL; deleting a user deletes their tokens.
- [ ] **AT-2 — Token service.** Forecast 300-380.
  - Acceptance: issue returns a plaintext once and stores only the HMAC; authenticate accepts a valid token and rejects unknown, malformed, expired, revoked and tampered tokens with one generic error; the resulting `Actor` carries channel `mcp`, the owner's current role and the token scopes; `last_used_at` is throttled; issue and revoke are audited without the secret or hash; only ADMIN may issue, list or revoke.
- [ ] **AT-3 — CLI commands.** Forecast 200-300.
  - Acceptance: create, list and revoke work through `flask`; the plaintext is printed once on create; list never prints secrets or hashes; errors are clear and exit non-zero.
- [ ] **AT-4 — Dependency cleanup and documentation.** Forecast 60-150.
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
| AT-1 | Pending | — | — | — |

## Next step

Implement AT-1 to AT-4 as independently green work units.
