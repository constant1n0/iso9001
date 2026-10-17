# Risks and Opportunities

Repository locator: `odd/tasks/risks-opportunities.md` · Engram mirror: `odd/risks-opportunities/tasks`

## Objective

Replace the free-text risks-and-opportunities list with two registers, as ISO 9001:2026 clause 6.1 treats them: risks assessed by likelihood and impact and opportunities by benefit and feasibility, each with its origin, process, owner, state and review date, actions with an owner, a deadline and an evaluation of their effectiveness, and a residual assessment after the actions; with screens, a heat map, a dashboard alert, a PDF and MCP modules.

## Problem and why it matters

- `RiesgoOportunidad` (`app/models.py`, table `riesgos_oportunidades`) holds only a type, a description, a quality-objective text and an action-plan text; it is reachable only through the JSON endpoints and the MCP module `riesgos_oportunidades`, with no screen.
- Clause 6.1 is mandatory and changed in 2026 (risks and opportunities as separate concepts); the gap analysis ranks it first in Wave 2 (`docs/analisis-brechas-iso-9001.md`).
- The management review's inputs count risks and opportunities from the old table (`app/services/management_reviews.py`, section `contexto`), and held reviews keep that data frozen.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/risks-opportunities` in worktree `iso9001-worktrees/user-management` from `main@24acc63` | User: "siguiente paso cuando quieras" after the assistant proposed Wave 2 starting with 6.1 (2026-10-17) |
| RO0 | Scope "Separados, con valoración y acciones" | User choice (2026-10-17) |
| RO1 | **Risks** (`riesgos`): title, description, origin (`interno` / `externo`), optional interested party, optional process, owner (person), likelihood and impact 1–5, level from their product (`bajo` 1–4, `medio` 5–9, `alto` 10–16, `critico` 20–25), treatment (`aceptar`, `mitigar`, `transferir`, `evitar`), residual likelihood and impact (after actions), state, identification date and next review date | User choice; bands and states by the assistant |
| RO2 | **Opportunities** (`oportunidades`): the same frame with benefit and feasibility 1–5, priority from their product (`baja` 1–6, `media` 8–12, `alta` 15–25) and decision (`aprovechar`, `posponer`, `descartar`) | User choice; bands by the assistant |
| RO3 | **Actions** (`acciones_riesgo`): for exactly one risk or one opportunity; description, owner, deadline, state (`pendiente`, `en_curso`, `completada`, `cancelada`), completion date, and an effectiveness evaluation (`eficaz` / `no_eficaz`, with date and comment) recorded only after completion (6.1.2 b) | User choice; lifecycle by the assistant |
| RO4 | An assessment may be pending: a record without likelihood and impact (or benefit and feasibility) is "sin valorar"; this keeps existing records migratable and is shown as a to-do | Assistant design |
| RO5 | Existing `riesgos_oportunidades` rows move to the register of their type, keeping description and dates; the quality-objective and action-plan texts are kept in a notes field; the old table and its JSON endpoints and MCP module go away (the MCP module is replaced by `riesgos`, `oportunidades` and `acciones_riesgo`); the downgrade moves them back | Assistant design (0 rows in production; the migration still has to be correct) |
| RO6 | Access keeps the current grant of `RISKS_OPPORTUNITIES`: every role reads; ADMIN and AUDITOR create and update; ADMIN deletes; `mcp` never deletes | Existing policy (`_JSON_REGISTER`) |
| RO7 | The management review keeps reading reviews already held: their frozen inputs keep the old `riesgos_oportunidades` keys and must still render; new reviews count risks by level and opportunities by priority | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 2 `size:exception` (ask only above 1,500 lines); production deployment needs explicit authorization | User authorizations (2026-10-02, 2026-10-17) |
| Size RO-2 | One-off `size:exception` for the services pull request (1,771 lines) | User (2026-10-17) |
| Surface RO-2 | `app/schemas.py` added to remove the dead `RiesgoOportunidadSchema` | User (2026-10-17) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **RO-1 — Models and migration.** Forecast 600-900.
  - `Riesgo`, `Oportunidad`, `AccionRiesgo` with CHECK constraints (scales 1–5, exactly one parent, effectiveness only after completion); migration with the data move and its downgrade; people, process and interested-party delete protection; policy unchanged.
- [x] **RO-2 — Services, MCP and review inputs.** Forecast 1,100-1,500.
  - Services with RO1–RO4 rules (level and priority, residual assessment, review-date overdue, action lifecycle and effectiveness), MCP modules replacing `riesgos_oportunidades`, management-review inputs on the new registers with old frozen data still rendering (RO7), removal of the old service and JSON endpoints.
  - **Acceptance check (RO-1 review):** a test writes to the old table after the RO-1 copy and proves the RO-2 migration carries that row into the new registers before the drop.
  - From RO-1: a migration that re-syncs the old rows (the old service keeps writing until RO-2) and then drops `riesgos_oportunidades`, with a downgrade that recreates it and moves rows back with new ids (risk and opportunity ids can collide in one table) and declares the lossy fields; the service requires an owner on new records; the audited-model count drops to 25.
- [x] **RO-3 — Screens and dashboard.** Forecast 1,100-1,500.
  - Risk and opportunity lists with filters, details with their actions, forms, heat map (5 × 5, CSS only), dashboard card (high or critical risks without open actions, overdue reviews, records without assessment).
  - Also (RO-2 review): a PostgreSQL test for a delete racing an action insert that maps the named foreign key to `RISK_HAS_ACTIONS` / `OPPORTUNITY_HAS_ACTIONS`; review-input band-edge cases (products 9 and 10, and a closed critical risk not counted as open).
- [ ] **RO-4 — PDF and docs.** Forecast 400-700.
  - Register PDF; Spanish guide first, then the English guide; `docs/mcp.md`, `docs/architecture/services.md`; gap analysis 6.1.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `24acc63`: 1,353 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| RO-1 | Done | `7feff60` (`Riesgo`, `Oportunidad`, `AccionRiesgo` with CHECK constraints for the 1–5 scales, both-or-neither pairs, exactly one parent, the completion date and effectiveness only after completion; `nivel` and `prioridad` helpers; migration `c6e8a2f4b1d9` creates the tables and copies each old row by type keeping its id (option a: the old table stays until RO-2); title from the first non-blank line; notes labelled "Objetivo de la calidad" and "Plan de acción"; delete protection for people, processes and interested parties) | RED: 28 errors (`no attribute 'Riesgo'`, `'nivel'`) and 3 `Conflict not raised`; migration `UndefinedTable: riesgos`. GREEN: 1,385 tests OK incl. PostgreSQL; adapted `tests/test_attribution.py` (23 → 26) and `tests/test_migrations.py` | Range `24acc63..f9e1941`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-46a38e440d287493` **approved** and acknowledged; its warning needs no change (`_ACTION_COMPLETION` is the shared MR3 constraint in `app/models.py`, the same text as the migration's `COMPLETION`, and the refused-statement tests prove it); its suggestion is an RO-2 acceptance check (re-sync before the drop) |
| RO-2 | Done | `09ee964` (`app/services/risks.py`: registers `risks`, `opportunities`, `actions` with `create` and `update` taking `today`, the computed `nivel` and `prioridad` filters in SQL from `LEVEL_RANGES` and `PRIORITY_RANGES`, `revision_vencida`, `is_open`, `is_overdue`, `attention` with five keys; MCP modules `riesgos`, `oportunidades`, `acciones_riesgo` replacing `riesgos_oportunidades` (24 modules); review inputs by level and priority, with reviews held before still rendering their old frozen keys), `6a7cbd2` (migration `e3b5d7f9a1c4`: re-sync (delete stale copies, refresh, insert missing), drop the old table and the `tipoenum` type; downgrade moves rows back with new ids and rebuilds the new tables from the old one, lossy fields in its docstring; old model, `RiesgoOportunidadSchema`, service, JSON routes and MCP module removed) | RED: 20 `cannot import name 'risks'`, `Módulo desconocido: «riesgos»`, the migration test found the old table, review tests missing «Nivel: Crítico», MCP `24 != 22`, attribution `25 != 26`. GREEN: `09ee964` alone 1,409 tests OK; both commits 1,397 tests OK incl. PostgreSQL; acceptance check: a row written to the old table after `c6e8a2f4b1d9` reaches the new registers | Range `eec2899..d08343e`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-cf2d9a7efa93d36a` **approved** and acknowledged; its two test suggestions moved into RO-3 |
| RO-3 | Done | `38fede2` (service: `heat_map()`, `abierto` filter on both registers, `probabilidad` and `impacto` equality filters and `alto_sin_accion` for risks; the RO-2 review's PostgreSQL race test and band-edge cases), `8538151` (`/riesgos/` and `/oportunidades/` lists with filters, the 5 × 5 heat map in CSS linking each cell to the filtered list, details with inherent and residual levels and actions, risk, opportunity, action and effectiveness forms; one shared `Kind` design for both registers; `RISKS_OPPORTUNITIES` out of `NO_ROUTES` with 18 endpoints), `e55eb4e` (dashboard card "Riesgos y oportunidades" from `attention()`, the buttons' permission tests and navigation) | RED: 13 `no attribute 'risk_routes'`, unknown filter keywords and `no attribute 'heat_map'`, 126 characterization and 59 UI-permission failures; the race and band-edge tests passed at once and the race test was proven by breaking the FK mapping. GREEN: `8538151` alone 1,413 tests OK; all three 1,415 tests OK incl. PostgreSQL | Pending |
| RO-4 | Pending | — | — | — |

## Findings during implementation

- A CHECK such as `(a IS NULL AND b IS NULL) OR (a BETWEEN 1 AND 5 AND b BETWEEN 1 AND 5)` accepts half a pair, because the second branch is NULL and a NULL CHECK passes; each branch needs explicit `IS NOT NULL`.
- Alembic's `compare_metadata` does not compare CHECK constraints, so the migration tests run refused statements to prove them.
- Review inputs pass through `audit.json_safe`, which turns enums into Spanish labels, so input lists store member names explicitly. 9.3.2 e (effectiveness of actions on risks) stays comment-only in the review: a follow-up.
- Test adaptations (standing approval, RO-2): `tests/test_migrations.py` (RO-1 test pinned to `c6e8a2f4b1d9`, acceptance check), `tests/test_attribution.py` (26 → 25), `tests/test_access_characterization.py` (JSON entries removed, `RISKS_OPPORTUNITIES` in `NO_ROUTES`), the MCP tests (slugs, 22 → 24 modules), `tests/test_management_reviews.py`; the risk parts of `tests/test_risks_resources_service.py` and `tests/test_risks_resources_routes.py` removed.
- Negating a SQL filter over a nullable product such as `probabilidad * impacto >= 10` drops the unassessed rows, so the filters need explicit `IS NOT NULL` guards. The optional benefit × feasibility grid for opportunities was not built.
- Test adaptations (standing approval, RO-3): `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`, `tests/test_dashboard.py`, `tests/test_risks_service.py`, `tests/test_management_reviews.py`.
- The RO-2 writer ran `git rm`, staging two deletions, and restored the index itself; the parent checked the index was clean before committing.
- Never deploy RO-1 without RO-2: the old service keeps writing `riesgos_oportunidades` until RO-2 drops it.

## Next step

RO-4.
