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

- [x] **QF-1 — Restrict nonconformity delete to ADMIN.** Forecast 40-80.
  - RED: an OPERATIVO and an AUDITOR POST to `/no_conformidades/eliminar/<id>` and the record survives; ADMIN still deletes.
  - Acceptance: non-admin delete is refused without deleting; the list hides the delete control for non-admins; existing tests stay green.
- [x] **QF-2 — Domain kernel and policy with today's matrix.** Forecast 300-380.
  - `Actor` (user id, label, role, channel, scopes), domain errors (`NotFound`, `Conflict`, `PermissionDenied`, `ValidationError`), `policy` encoding current behaviour including QF-1, error handlers registered before the global `Exception` handler.
  - Characterization tests for role denials and the JSON registers, written first.
- [x] **QF-3 — `AuditLog` model, migration, recorder and flush guard.** Forecast 300-380.
  - Acceptance: append-only API (no update or delete path); changed fields only; passwords never recorded; migration upgrades and downgrades on PostgreSQL.
- [x] **QF-4 — Record metadata mixin and migration.** Forecast 250-330.
  - Acceptance: explicit stamping helpers exist and are tested; legacy rows remain NULL; tz-aware timestamps. Wiring the helpers into real create/update paths is part of each service task (QF-5 to QF-8).
- [x] **QF-5 — Nonconformity service pilot.** Forecast 330-400.
  - Routes become thin adapters; create, update and delete go through the service with audit rows; the state constant is centralized and reused by forms and the dashboard.
  - The service stamps `created_*`/`updated_*` on real create and update paths (review finding R3-stamping-unwired).
  - The flush guard treats a new audited instance without a primary key as unaudited (review finding R3-guard-pending-pk), and nonconformity route tests run with the guard installed.
  - HTML form posts get a deliberate, tested outcome for `NotFound`, `Conflict` and `ValidationError` (review suggestion R3-domain-html-json).
  - The Flask-free check for `app.services` becomes an import-level (AST) check (review suggestion R3-flask-free-textual).
- [x] **QF-6 — Audit and document services.** Forecast 330-400.
- [x] **QF-7 — Generic CRUD helper and HTML registers.**
  - Also: normalise empty optional text to `None` in `app/services/fields.py` so an unchanged edit writes no audit row, and pin with a route test that an empty audit `estado` cannot pass the form (review suggestion R3-audit-form-empty-values). Training, satisfaction surveys, stakeholders and improvements (HTML). Forecast 350-400.
- [x] **QF-8 — JSON API and JSON registers through services.** Improvements JSON API and the five JSON registers; characterization tests first; `IntegrityError` → 409; drop the JSON GET cache (D11). Forecast 350-400.
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
- Running count: `288558e` 363 lines (documentation), `55c22a9` 93, `ada3b81` about 282, `3a74697` about 276, `89b1f31` 320.
- Slice plan: PR 1 = `288558e` ([#20](https://github.com/constant1n0/iso9001/pull/20), merged `ea5b91b`); PR 2 = `55c22a9` + `ada3b81` ([#21](https://github.com/constant1n0/iso9001/pull/21), merged `38f7646`); PR 3 = `3a74697` ([#22](https://github.com/constant1n0/iso9001/pull/22), merged `01cb6a6`); PR 4 = `89b1f31` + `9d4f877` ([#23](https://github.com/constant1n0/iso9001/pull/23), merged `ddf9e63`); PR 5 = `9559f5d` ([#24](https://github.com/constant1n0/iso9001/pull/24)); PR 6 = `aa12c2f`; PR 5 merged `72949cf`; PR 6 = `aa12c2f` ([#25](https://github.com/constant1n0/iso9001/pull/25), merged `0cc95e6`); PR 7 = `1e385cf` + `50a177f` ([#26](https://github.com/constant1n0/iso9001/pull/26), merged `3e15991`); PR 8 = `316a90b` ([#27](https://github.com/constant1n0/iso9001/pull/27), merged `21c42bf`); PR 9 = `478b16b` + `56b792e` ([#28](https://github.com/constant1n0/iso9001/pull/28), merged `3275856`); PR 10 = `db3ffa9` ([#29](https://github.com/constant1n0/iso9001/pull/29), merged `f0133fa`); PR 11 = `ca67da2` ([#30](https://github.com/constant1n0/iso9001/pull/30), merged `f3ca09e`); PR 12 = `2bc7b38` ([#31](https://github.com/constant1n0/iso9001/pull/31), merged `56d2562`); PR 13 = `26394a0` + `bd1aee8` ([#32](https://github.com/constant1n0/iso9001/pull/32), merged `ad51f70`); PR 14 = `566c3ee` ([#33](https://github.com/constant1n0/iso9001/pull/33), merged `3b09af3`); PR 15 = `f63e98b` ([#34](https://github.com/constant1n0/iso9001/pull/34), merged `be95ef0`); PR 16 = `8e205ad` ([#35](https://github.com/constant1n0/iso9001/pull/35), merged `a28d472`); PR 17 = `6a68070` ([#36](https://github.com/constant1n0/iso9001/pull/36), merged `af22527`); PR 18 = `9d3c637` ([#37](https://github.com/constant1n0/iso9001/pull/37), merged `dd2a980`); PR 19 = `bd9d5b6`; PR 20 = `f47a638` (459 lines, see findings); PR 21 = `5ee77e0`; PR 22 = `1080733`; PR 23 = `21e62c2`; PR 24 = `9e8c835` + this tracker update. Each is opened after the previous one merges, then rebased on `main` by merging `main` into the feature branch.

## Findings during implementation

Recorded by QF-2 characterization; encoded as-is and fixed by the task named.

- **JSON register writes return 500** (QF-8, fixed): POST and PUT on the five JSON registers and the improvements JSON API fail because `load_instance=True` schemas return model instances that the routes treat as dicts. Already reported by the September 2026 bug audit. The characterization tests pin `500` until QF-8 flips them to `201`/`200`.
- **No role gating on JSON registers and the improvements API** (QF-9): every role can create, update and delete.
- **OPERATIVO can delete** improvements, surveys, training records and stakeholders (QF-9).
- **`AuditoriaForm.validate` permission check is redundant** with `role_required` on audit routes (QF-6 cleanup). Removed in QF-6: the form is only used behind `role_required(AUDITOR)` and the service enforces policy.
- **Shared validators** (QF-6): `app/services/fields.py` holds text, date and enum validators used by the audit and document services; QF-7 can move nonconformities onto it. Enum fields accept the member or its name, because forms post names.
- **Register behaviour** (QF-7): blank optional text is stored as NULL (an unchanged edit writes no audit row); invalid training date filters are ignored; a duplicate stakeholder `nombre` raises `Conflict` with a Spanish message (before: 500); the improvements list is ordered by id; Mejora HTML and JSON handlers are separate, so the JSON API stays for QF-8.
- **JSON contract** (QF-8): POST 201 and PUT 200 return the schema `dump` (same keys as GET); DELETE keeps its status and body; unknown fields and non-object bodies give 422 (incl. `fecha_implementacion` on `/mejoras/api/`, which is not writable); duplicates 409; JSON clients get `{"error": ...}`; a bodiless DELETE 404 without `Accept: application/json` keeps `{"message": "Recurso no encontrado"}`. `AuditoriaIndicador.fecha_auditoria` accepts a datetime or ISO string, stored as naive UTC. JSON endpoint names now come from shared handlers (nothing referenced the old names).
- **Cache removed** (QF-8, D11): `Flask-Caching` was used only by the six JSON GET routes; the extension, `CACHE_*` config and the `Flask-Caching`/`cachelib` pins are gone. Existing deployments keep the packages installed until their environment is rebuilt; nothing imports them.
- **CRUD helper size** (QF-7): `f47a638` is 459 lines; `training.py` binds the helper's write functions at import, so a read-only first half would not import. One honest slicing pass found no cohesive split; its pull request needs a maintainer `size:exception` or must stay a single over-budget slice.
- **Audit and document behaviour** (QF-6): invalid audit date filters are ignored (before, SQLite compared strings and PostgreSQL could error); the paginated audit list is ordered by `id` (it had no ordering); a duplicate document `code` raises `Conflict` with a Spanish message; `Document.signature` is not writable.
- **No routes exist for users or the audit log**, so those resources enter the policy when their adapters exist.
- **Review suggestion R3-domain-html-json** (QF-5): for HTML form posts, `NotFound`, `Conflict` and `ValidationError` return a JSON body; decide flash-and-redirect behaviour and pin it with tests when services are wired into routes.
- **Review suggestion R3-flask-free-textual** (QF-5): the Flask-free guard for `app.services` is a source-text check; strengthen it to an import-level check.
- **Review warning R3-guard-pending-pk** (QF-5): pending inserts have no primary key in `before_flush`, so new instances share the key `(table, None)`; a new audited instance without a primary key must count as unaudited.
- **Review warning R3-stamping-unwired** (QF-5+): no real create/update path calls the stamping helpers yet; each service task wires them. Done for nonconformities in QF-5.
- **Nonconformity list filter** (QF-5): an invalid `fecha_detectada` query value is now ignored. Before, it returned an empty list on SQLite and a database error (500) on PostgreSQL.
- **Service pattern** (QF-5): `app/services/nonconformities.py` is the reference for QF-6 to QF-8 — `policy.require` first, then load, validate against a field whitelist, mutate, stamp and audit; services flush and never commit; no-op updates write no audit row. `app/utils/web_actor.current_actor()` builds the web `Actor`. HTML `Conflict`/`ValidationError` roll back, flash and redirect to a same-origin Referer (dashboard otherwise); `NotFound` keeps the JSON 404.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| Docs | Done | `288558e` | Structural readback | Passive (`non_executable_only`); boundary advanced to `288558e` |
| QF-1 | Done | `55c22a9` | RED: 3 of 5 new tests failed (OPERATIVO and AUDITOR deleted; delete control rendered). GREEN: 76 tests OK incl. PostgreSQL; parent spot check of the 5 new tests OK | Medium, `under_budget`; covered by review `review-346107ba1909d990` |
| QF-2 | Done | `ada3b81` (actor, domain errors, HTTP handlers), `3a74697` (policy), `89b1f31` (characterization) | RED: `ModuleNotFoundError` for `app.services` in 16 new tests and 2 characterization tests. GREEN: 96 tests OK incl. PostgreSQL; the full suite passed at each of the three commits | Range `288558e..89b1f31`: medium, `slice_budget_reached`; consent granted; review `review-346107ba1909d990` (lens `review-reliability`) **approved** and acknowledged (authority burned); reviewed boundary advanced to `89b1f31` |
| QF-3 | Done | `9559f5d` (table), `aa12c2f` (recorder), `1e385cf` (append-only + flush guard), then the review-findings fix in the commit that records this row | RED: `ImportError` for `app.services.audit` in 20 new tests; hardening RED: update without `before` not rejected, two instances with one audit row not caught. GREEN: 97 / 109 / 118 tests at the three commits, 121 after hardening, incl. PostgreSQL migration upgrade/downgrade, JSONB and CHECK checks | Range `9d4f877..1e385cf`: medium, `slice_budget_reached`; consent granted; review `review-788622beda6891f8` (lens reliability) **approved** and acknowledged; 3 warnings + 1 suggestion fixed in the hardening commit; boundary advanced to `1e385cf` |
| QF-4 | Done | The commit that records this row (mixin, migration `d5a9f3b7c1e2`, `app/services/attribution.py`) | RED: `KeyError: 'created_at'` for every audited model; PostgreSQL `UndefinedColumn` for the legacy-row and user-delete tests. GREEN: 132 tests OK incl. PostgreSQL (legacy rows keep NULL metadata after upgrade; deleting a user sets `*_by_id` to NULL; downgrade drops the columns) Range `1e385cf..316a90b` (QF-3 hardening + QF-4): medium, `slice_budget_reached`; consent granted; review `review-ce8842d37011fbe8` **approved** and acknowledged; 2 warnings moved to QF-5; boundary advanced to `316a90b` |
| QF-5 | Done | `478b16b` (guard pending-pk + AST Flask-free check), `56b792e` (HTML outcomes for domain errors), `db3ffa9` (queries + state constant), `ca67da2` (audited, stamped commands), `2bc7b38` (route adoption), then the review fixes in the commit that records this row | RED: guard did not catch two keyless inserts; `ImportError` for `app.services.nonconformities` in 26 service tests; HTML `Conflict`/`ValidationError` returned 409/422 instead of flash and redirect; 8 of 12 route tests returned 500 under the installed flush guard. GREEN: 134 / 138 / 145 / 164 tests at the four commits and 176 after route adoption, incl. PostgreSQL; review fixes RED: `estado=None` accepted on create, JSON `Conflict`/`ValidationError` without rollback; GREEN 177 | Range `316a90b..2bc7b38`: medium, `slice_budget_reached`; consent granted; review `review-0969fe6495b99bab` **approved** and acknowledged; boundary advanced to `2bc7b38`; its warning (null `estado` on create) and suggestion (JSON rollback) fixed in the review-fix commit |
| QF-6 | Done | `bd1aee8` (audit queries), `566c3ee` (audit commands + shared `app/services/fields.py`), `f63e98b` (audit routes), `8e205ad` (document queries), `6a68070` (document commands), then document routes in the commit that records this row | RED: `ImportError` for `app.services.audits` (26 tests) and `app.services.documents` (27); audit routes 8 failures + 2 errors and document routes 6 failures under the flush guard. GREEN: 182 / 203 / 214 / 218 / 241 tests at the five commits and 250 after document routes, incl. PostgreSQL | Range `2bc7b38..9d3c637` (QF-5 review fixes + QF-6): medium, `slice_budget_reached`; consent granted; review `review-396532a33f7e0e8d` **approved** and acknowledged; boundary advanced to `9d3c637`; one suggestion moved to QF-7 |
| QF-7 | Done | `bd9d5b6` (blank optional text → NULL), `f47a638` (spec-driven CRUD helper + training register), `5ee77e0` (training routes + shared `web_args.date_arg`), `1080733` (surveys), `21e62c2` (stakeholders), `9e8c835` (improvements HTML) | RED: 9 failures for blank optional text and an unchanged audit edit writing an audit row; `ModuleNotFoundError` for `app.services.training` (30 errors); route tests failing under the flush guard. GREEN: 258 / 275 / 281 / 288 / 296 / 303 tests at the six commits, incl. PostgreSQL | Range `9d3c637..8b65547` (QF-7): medium, `slice_budget_reached`; consent granted; review `review-d13891dd64311f23` **approved** and acknowledged; boundary advanced to `8b65547`; its suggestion (positive date-filter assertion) added in the commit that records this note. `Capacitacion.fecha` is a `Date` column, so the direct equality filter is correct |
| QF-8 | Done | `7f02002` (roles service), `4917f07` (roles JSON API + shared `app/routes/json_register.py`), `bcc71d9` (risks/opportunities + training resources), `02a47fb` (process operations + audit indicators), `1611515` (improvements JSON API + JSON GET cache removed) | RED: import errors for each new service; JSON route tests got 500 for create/update/conflict/validation and `{"message": ...}` 404 bodies; cache still present. GREEN: 307 / 315 / 334 / 356 / 366 tests at the five commits, incl. PostgreSQL | Pending assessment |

## Next step

Review QF-8, deliver PR 25-28, and implement QF-9 (permission deltas D1, `can()` in templates, guard test, docs).
