# QMS Foundations (Wave 0)

Repository locator: `odd/tasks/qms-foundations.md` · Engram mirror: `odd/qms-foundations/tasks`

## Objective

Give the application one framework-independent service layer, an append-only audit trail with record attribution, and a central permission policy, so that the Flask UI and the future MCP server for AI agents share the same validated, authorized and attributable operations.

## Problem and why it matters

- Business logic lives inside route handlers, coupled to WTForms, `flash` and redirects. An MCP adapter would have to duplicate validation and authorization.
- No record stores who changed what or when, and any authenticated user can hard-delete a nonconformity. ISO 9001 7.5.3 traceability cannot be shown, and agent actions would be unattributable.
- Authorization is a single-role, redirect-style decorator on 9 of about 39 mutating endpoints.

Inputs: `docs/iso-9001-gap-analysis.md` (roadmap Wave 0) and the exploration below.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD with delegated direct workers; feature branch `feat/qms-foundations` in worktree `iso9001-worktrees/qms-foundations` | User authorized Wave 0 implementation under ODD (2026-10-02) |
| D1 | Adopt the proposed role-by-action matrix, as a separate commit after a behaviour-neutral refactor | User |
| D2 | OPERATIVO never deletes in Wave 0 | User (recommended) |
| D3 | Hard delete is ADMIN only, with an `AuditLog` snapshot of the deleted record; no delete through the MCP channel; soft delete deferred to Wave 1 | User (recommended) |
| D4, D5 | Separate `Person` entity for `NoConformidad.responsable` and `Auditoria.auditor` only — **deferred to change `qms-people`** | User (recommended) |
| D6 | Production row counts skipped for now | User (recommended) |
| D7 | Token seams here (`Actor.channel`, `Actor.scopes`; policy intersects scopes with role); API tokens in the next change `api-tokens` | User (recommended) |
| D8 | Nonconformity state enum deferred to Wave 1 (`nc-capa-loop`); only centralize the state constant now | User (recommended) |
| D9 | Audit log append-only at application level; database hardening later | User (recommended) |
| D10 | Audit log records writes only | User (recommended) |
| D11 | Drop the per-process cache on JSON GET endpoints | User (recommended) |
| Research | Not selected | User (recommended) |
| Delivery | `auto-chain` with `stacked-to-main` chain strategy | User selected "Auto" PR strategy and `stacked-to-main` (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Scope

### Included

- QF-1 fix of the nonconformity delete defect (ADMIN only).
- Domain kernel: `Actor`, domain errors, central policy, error handlers.
- `AuditLog` and record metadata.
- Services for every mutating endpoint, starting with nonconformities.
- Permission deltas from D1, `can()` in templates, guard tests, docs.

### Excluded

- `Person` (`qms-people`), API tokens (`api-tokens`), the MCP server, the nonconformity state enum (`nc-capa-loop`), and new ISO modules (Wave 1+).
- The other two verified report defects (monthly report all-time totals, dashboard mixing years): separate small direct fixes.
- Push, pull requests and merge: user decisions under repository policy.
- Production access or data changes.

## Constraints

- Python 3.11 in CI; local runs use the worktree `venv` (Python 3.12). Stdlib `unittest` only; do not add pytest.
- Strict test-first: observe RED before implementing each behaviour, then GREEN, then refactor.
- Schema changes require Alembic migrations that work on PostgreSQL 17; the migration test runs against `TEST_POSTGRES_URI`.
- New columns and foreign keys are nullable; never backfill invented attribution.
- Services never import Flask; they take an explicit `Session` and `Actor`; the adapter owns the commit.
- Never log password hashes or tokens in the audit log.
- Strict CSP: no inline scripts or styles in templates.
- Spanish UI copy; English code, tests and docs. Conventional commits without AI attribution.
- About 400 authored changed lines per task is a planning heuristic only.

## Target design (from exploration)

- **Service layer (option B):** functions per aggregate plus a generic CRUD helper for the plain registers; bespoke services for `NoConformidad`, `Auditoria` and `Document`. `session.get`/`select` instead of `Model.query`; strict field whitelists instead of `Model(**data)`; `IntegrityError` maps to `Conflict` (HTTP 409).
- **Audit log (option A):** explicit recording inside services and the CRUD helper, plus a test-only `before_flush` guard that fails when an audited model changes without an `AuditLog` row.
  - Table `audit_logs`: `id` (big identity), `occurred_at` (timestamptz, default now), `entity_type`, `entity_id`, `action`, `actor_user_id` (FK `users`, `SET NULL`), `actor_label`, `channel` (CHECK `web|mcp|cli|system`), `before`/`after` (changed fields only, JSONB on PostgreSQL), `request_id`.
  - Indexes on `(entity_type, entity_id, id)`, `(occurred_at)`, `(actor_user_id)`.
- **Metadata mixin:** `created_at`, `created_by_id`, `updated_at`, `updated_by_id`, all nullable; legacy rows stay NULL.
- **Policy (option A):** pure `policy.can(actor, action, resource)` / `require(...)` raising `PermissionDenied`; exposed to templates as `can()`; channel `mcp` never deletes (D3).

### Permission matrix after QF-9 (D1)

| Resource | Read | Create / update | Delete |
|---|---|---|---|
| Nonconformities, improvements, surveys, training, stakeholders | All roles | All roles | ADMIN |
| Audits | ADMIN, AUDITOR | ADMIN, AUDITOR | ADMIN |
| Documents | ADMIN | ADMIN | ADMIN |
| JSON registers (roles, risks/opportunities, training resources, processes) | All roles | ADMIN, AUDITOR | ADMIN |
| Users, audit log | ADMIN, AUDITOR | ADMIN | None |

## Tasks

Forecasts count authored additions plus deletions. Route for every task: **delegated direct**, because each touches two or more non-trivial files (code plus tests).

- [ ] **QF-1 — Restrict nonconformity delete to ADMIN.** Forecast 40-80.
  - RED: an OPERATIVO and an AUDITOR POST to `/no_conformidades/eliminar/<id>` and the record survives; ADMIN still deletes.
  - Acceptance: non-admin delete is refused without deleting; the list hides the delete control for non-admins; existing tests stay green.
- [ ] **QF-2 — Domain kernel and policy with today's matrix.** Forecast 300-380.
  - `Actor` (user id, label, role, channel, scopes), domain errors (`NotFound`, `Conflict`, `PermissionDenied`, `ValidationError`), `policy` encoding current behaviour including QF-1, error handlers registered before the global `Exception` handler.
  - Characterization tests for role denials and the JSON registers, written first.
- [ ] **QF-3 — `AuditLog` model, migration, recorder and flush guard.** Forecast 300-380.
  - Acceptance: append-only API (no update or delete path); changed fields only; passwords never recorded; migration upgrades and downgrades on PostgreSQL.
- [ ] **QF-4 — Record metadata mixin and migration.** Forecast 250-330.
  - Acceptance: services set `created_*`/`updated_*`; legacy rows remain NULL; tz-aware timestamps.
- [ ] **QF-5 — Nonconformity service pilot.** Forecast 330-400.
  - Routes become thin adapters; create, update and delete go through the service with audit rows; the state constant is centralized and reused by forms and the dashboard.
- [ ] **QF-6 — Audit and document services.** Forecast 330-400.
- [ ] **QF-7 — Generic CRUD helper and HTML registers.** Training, satisfaction surveys, stakeholders and improvements (HTML). Forecast 350-400.
- [ ] **QF-8 — JSON API and JSON registers through services.** Improvements JSON API and the five JSON registers; characterization tests first; `IntegrityError` → 409; drop the JSON GET cache (D11). Forecast 350-400.
- [ ] **QF-9 — Permission deltas (D1), `can()` in templates, guard test, docs.** Forecast 200-300.
  - Acceptance: the matrix above holds for web requests; navigation follows the policy; a guard test fails if a route writes audited models through `db.session` directly; README or docs describe the service layer and the audit log.

**Total forecast:** about 2,450-3,050 authored changed lines, above the 400-line review budget, so delivery is chained.

## Checks

Run per task in the worktree:

```bash
$ export TEST_POSTGRES_URI="postgresql://postgres:DB_PASSWORD@127.0.0.1:55432/iso_test"
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

`DB_PASSWORD` comes from the `iso-pg-test` container environment and must never be printed or committed.

- Baseline at `b794952`: 71 tests, all passing, including PostgreSQL tests (2026-10-02).
- After each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json`. The first boundary is the branch point `b794952`.

## Delivery

- Strategy: `auto-chain`, chain strategy `stacked-to-main`: each pull request targets the previous slice branch, the first targets `main`.
- Slice boundaries and the commits each pull request holds are recorded here as they are created.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| QF-1 | Pending | — | — | — |

## Next step

Commit QF-1, assess it, then implement QF-2.
