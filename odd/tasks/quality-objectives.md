# Quality Objectives

Repository locator: `odd/tasks/quality-objectives.md` · Engram mirror: `odd/quality-objectives/tasks`

## Objective

Plan measurable quality objectives with an owner, a target, a deadline and resources, record their measurements, and show whether each objective is on track (ISO 9001:2026 clause 6.2).

## Problem and why it matters

- The only trace of quality objectives is the free-text `RiesgoOportunidad.objetivo_calidad` in the risks register; nothing records targets, owners, deadlines, resources, how results are evaluated or the measurements (6.2.1, 6.2.2).
- Management review (next feature) needs objectives and their performance as an input (9.3.2 c).
- Gap analysis Wave 1 item 4 (`docs/iso-9001-gap-analysis.md`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/quality-objectives` in worktree `iso9001-worktrees/user-management` from `main@1b40054` | User: "todas, elige tú el orden"; design choice (2026-10-11) |
| QO0 | Scope "Objetivos con mediciones" | User choice (2026-10-11) |
| QO1 | An objective has title, description, process (optional link to `ProcesoOperacion`), indicator, unit, target value with direction (`mayor_igual` ≥ or `menor_igual` ≤), baseline, owner (person), year and deadline, measurement frequency (`mensual`, `trimestral`, `semestral`, `anual`), resources needed, and state (`activo`, `alcanzado`, `no_alcanzado`, `cancelado`) | User choice |
| QO2 | Measurements per objective: date, value, comment, recorded-by person; the system computes for each measurement and for the objective whether the target is met, the latest value and the trend | User choice |
| QO3 | A measurement is overdue when, for an `activo` objective, no measurement exists within the last period of its frequency (counted from the latest measurement, or from the objective's start when there is none); the dashboard flags objectives off target or with an overdue measurement | User choice; overdue rule by the assistant |
| QO4 | Every role reads objectives and measurements (objectives are communicated, 6.2.1 f); ADMIN and AUDITOR create and update; ADMIN deletes; `mcp` never deletes | User choice |
| QO5 | `RiesgoOportunidad.objetivo_calidad` stays as it is (risks register) | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 1 `size:exception` (ask only above 1,500 lines); production deployment needs migrations and explicit authorization | User authorizations (2026-10-02, 2026-10-09) |
| Test adaptations | Standing Wave 1 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR | User (2026-10-10) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **QO-1 — Objectives and measurements (service).** Forecast 700-1,100.
  - Models, migration, services with QO1–QO4 rules, target evaluation, overdue rule, policy resource `QUALITY_OBJECTIVES`, MCP modules.
- [x] **QO-2 — Screens and dashboard.** Forecast 800-1,300.
  - Objectives list with filters and status badges, objective detail with measurements table and trend chart (CSP-clean, existing chart library), forms, dashboard card.
  - Also: deleting a process referenced by an objective gives a specific Spanish `Conflict` with an explicit check (today only the foreign key refuses it, with the generic message; `app/services/process_operations.py`); QO-1 review: a no-op update of a measurement of a closed objective writes nothing instead of refusing (pin which rule wins).
- [x] **QO-3 — Reports and docs.** Forecast 200-400.
  - Objectives PDF (yearly summary), monthly report figures, README and `docs/mcp.md`.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `1b40054`: 1,225 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| QO-1 | Done | `5697670` (models `objetivos_calidad`, `mediciones_objetivo`, migration `b4d6f8a1c3e5`, policy `QUALITY_OBJECTIVES`, people delete protection), `dd461f0` (`app/services/quality_objectives.py` with `meets`, `summary`, `off_track`, MCP modules `objetivos_calidad`, `mediciones_objetivo`: 20 modules) | RED: 26 `ImportError: quality_objectives`, missing enums, `NoSuchTableError: objetivos_calidad`. GREEN: 1,253 tests incl. PostgreSQL; `5697670` alone passed 1,226 | Range `9271ac0..dd461f0`: **medium**, `slice_budget_reached` (delivered as two pull requests under 1,500 lines each); consent granted; reliability review `review-9cea81a3cc019ec8` **approved** and acknowledged; one suggestion moved into QO-2; the other (the MCP measurement seed uses the real clock) only matters before 2026-01-31 |
| QO-2 | Done | `531043a` (`/objetivos-calidad/` list with filters and badges, detail with measurements and trend chart in `app/static/js/objectives.js`, objective and measurement forms, state buttons `/<id>/estado/<state>`, dashboard card `#objetivos-calidad`, process delete message, no-op measurement update on a closed objective), review follow-up `a722740` | RED: 9 screen tests (`no attribute 'quality_objective_routes'`), 25 UI permission and 63 characterization failures, no-op and process-delete tests; follow-up RED: 34 failures and 68 subtest errors (field-whitelist order, state segments answering 302, non-finite numbers without a field error, statement count growing with objectives, every process conflict reported as objectives, measurement delete on a closed objective allowed). GREEN: 1,265, then 1,274 tests incl. PostgreSQL | Range `7d22f29..531043a`: **medium**, `slice_budget_reached` (1,499 lines); consent granted; reliability review `review-fd547ce610149721` **approved** and acknowledged; its three warnings and two suggestions fixed in `a722740` (range `531043a..a722740`: medium, `under_budget`) |
| QO-3 | Done | `5941360` (yearly PDF `/objetivos-calidad/pdf?anio=YYYY` with the year figures and each objective with its measurements; monthly report section "Objetivos de calidad"; README, `docs/mcp.md` (20 modules), `docs/architecture/services.md`), review follow-up `04d6dc7` | RED: 6 errors and 6 failures (`_monthly_report_context() got an unexpected keyword argument 'today'`, no `render_pdf` in the objectives routes, missing PDF link for 3 roles). GREEN: 1,281, then 1,282 tests incl. PostgreSQL | Range `531043a..5941360` (includes the QO-2 follow-up `a722740`): **medium**, `slice_budget_reached`; consent granted; reliability review `review-0bf3bc78954a5774` **approved** and acknowledged; its warning answered in `04d6dc7` (`summary` already sorts by date; the report query now orders too and a test stores measurements newest first) and its suggestion applied (an unnamed constraint never matches the objectives' foreign key) |

## Findings during implementation

- MCP numbers are decimal strings with four decimals (`"95.0000"`), through `audit.json_safe`. The generic MCP tools call `create` without a date, so the registry wraps measurement writes with `local_today()`.
- Added rules: `sentido` and `frecuencia` are required; `anio` defaults to the start date's year; the next due date is capped at `fecha_limite`; a measurement never moves to another objective; an objective's period must keep its measurements inside; a target or direction change is refused once measurements exist.
- Test adaptations (standing approval): `tests/test_attribution.py`, `tests/test_mcp_http.py`, `tests/test_mcp_modules.py`, `tests/test_access_characterization.py`, `tests/test_policy.py`, `tests/test_mcp_write_tools.py`, `tests/test_migrations.py`, `tests/mcp_support.py`.

- **Screens (QO-2):** state changes are buttons posting to `/<id>/estado/<state>` (member names only; anything else is 404); measurements are read-only unless the objective is `activo`, in the routes and in the service (delete included); the form field for the target is declared as `target` with `name='meta'` (a WTForms field called `meta` shadows `Form.meta`); target, baseline and measurement values must be finite numbers (`NOT_FINITE`).
- **Process delete:** a refused delete reports the objectives only when PostgreSQL names their foreign key; any other conflict, and SQLite, keep the generic conflict message (after a failed flush the session cannot run the explicit check).
- **Reports (QO-3):** an invalid `anio` (not four digits in 2000–2100) answers 404 and a missing one is the current year; the monthly figures judge active objectives started by the cutoff (the month's last day, or today if earlier) on their measurements up to it; objectives that became `alcanzado` or `no_alcanzado` are not counted by month because no state-change date is kept. `app/utils/reports.py` imports `local_today` inside the function (a module-level import is circular through `app/audit_notifications.py`).
- Test adaptations (standing approval, QO-3): `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`.
- Test adaptations (standing approval, QO-2): `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`, `tests/test_quality_objectives.py`.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#106](https://github.com/constant1n0/iso9001/pull/106) | tracker, QO-1 models | merged | |
| 2 | [#107](https://github.com/constant1n0/iso9001/pull/107) | QO-1 service, record | `7d22f29` | |
| 3 | [#108](https://github.com/constant1n0/iso9001/pull/108) | QO-2 screens `531043a` | merged | 1,499 lines |
| 4 | [#109](https://github.com/constant1n0/iso9001/pull/109) | QO-2 review follow-up, record | `2a04c42` | |
| 5 | Pending | QO-3, closing update | — | Final slice |

## Production deployment (pending authorization)

One migration (`b4d6f8a1c3e5` objectives and measurements) on top of `e9c2a4f6b8d1`: back up the database and the code, fast-forward, `flask db upgrade`, `flask db check`, then the user restarts the four services. Smoke tests: `/objetivos-calidad/` redirects to the login, the dashboard loads, `qms_modules` lists 20 modules.

## Next step

**Feature complete** once slice 5 merges. Next: the production deployment above, after explicit authorization; then management review, the last Wave 1 module.
