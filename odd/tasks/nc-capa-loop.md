# Nonconformity, Corrective Action and Effectiveness (nc-capa-loop)

Repository locator: `odd/tasks/nc-capa-loop.md` · Engram mirror: `odd/nc-capa-loop/tasks`

## Objective

Turn nonconformities into a controlled loop: record origin, severity, containment and root cause; plan one or more corrective actions with an owner and dates; verify each action's effectiveness independently; and close the nonconformity only when every action proved effective (ISO 9001:2026 clause 10.2, with 8.7).

## Problem and why it matters

- `NoConformidad.estado` is free text constrained by the service to "Abierta", "En proceso" or "Cerrada" (legacy values tolerated), and `accion_correctiva` is a single free-text field (`app/models.py`, `app/services/nonconformities.py`).
- Nothing records containment, root cause, the actions taken, who owns them, when they were done, or whether they were effective, so clause 10.2 cannot be evidenced.
- Gap analysis Wave 1 item 2 (`docs/iso-9001-gap-analysis.md`); decision D8 of `qms-foundations` deferred the state enum to this change.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/nc-capa-loop` in worktree `iso9001-worktrees/user-management` from `main@4bd9cdf` | User: "todas, elige tú el orden"; design choice (2026-10-09) |
| N0 | Scope "Ciclo completo" | User choice (2026-10-09) |
| N1 | New nonconformity fields: origin (`auditoria`, `cliente`, `proceso`, `proveedor`, `otro`), severity (`mayor`, `menor`, `observacion`), containment (text), root cause (text) | User choice |
| N2 | States `Abierta` → `Acción planificada` → `En verificación` → `Cerrada`, plus `Cancelada` with a reason. Legacy values convert: `Abierta` stays, `En proceso` → `Acción planificada`, `Cerrada` stays; any other legacy text → `Abierta` (the migration reports how many rows it changed) | User choice; legacy fallback by the assistant |
| N3 | A nonconformity has several corrective actions: description, owner (person), planned date, done date, and an effectiveness verification (result `eficaz` / `no eficaz`, date, verifier person, evidence) | User choice |
| N4 | Verification is done by ADMIN or AUDITOR, and the verifier is never the action's owner | User choice |
| N5 | State follows the actions: the first action moves an `Abierta` NC to `Acción planificada`; when every action is done it moves to `En verificación`; a `no eficaz` verification moves it back to `Acción planificada` (a new action is needed); closing is an explicit act by ADMIN or AUDITOR, allowed only when every action is verified `eficaz`; cancelling needs a reason. A closed or cancelled NC is read-only except reopening by ADMIN | User choice; reopening rule by the assistant |
| N6 | The `Mejora` register stays separate; `accion_correctiva` text is kept as a legacy field (shown read-only once actions exist) | User choice |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; production deployment needs migrations and explicit authorization. Standing `size:exception` for every Wave 1 pull request that passes its review; ask again only above 1,500 changed lines | User authorization (2026-10-02); Wave 1 size exception (2026-10-09) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct** (two or more non-trivial files each).

- [x] **NC-1 — Nonconformity fields and states.** Forecast 350-450.
  - Columns for N1, the state as a constrained enum with the N2 conversion in the migration (upgrade and downgrade), service validation and transitions that do not depend on actions (cancel with reason, reopen by ADMIN), MCP fields.
  - Acceptance: legacy rows convert as N2; invalid transitions refused with Spanish messages; existing NC screens, PDF and reports still work.
- [ ] **NC-2 — Corrective actions and effectiveness.** Forecast 350-450.
  - `AccionCorrectiva` model and migration, service (create, update, delete, verify), policy resource, state synchronisation N5, close rule, MCP module.
  - Build `close` and the automatic moves on `nonconformities._transition` (snapshot, `_set_state`, stamp, audit, flush) with the role check done like `cancel`; make the date injected by the adapter (`local_today()`) mandatory for terminal transitions instead of the server clock fallback (NC-1 review).
  - Acceptance: N3–N5 enforced in the service (including concurrent edits refused cleanly); audit rows for every write.
- [ ] **NC-3 — Screens.** Forecast 400-600.
  - NC form with the new fields and person picker; NC detail page with actions, add/edit/delete action, verify action, close, cancel, reopen; state badges; list filters by state, origin and severity.
  - Acceptance: role-based access; forms keep input on errors; CSP-clean.
- [ ] **NC-4 — Reports and docs.** Forecast 150-300.
  - PDF of a nonconformity with its actions and verification; monthly report counts by state; README and `docs/mcp.md`.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `4bd9cdf`: 889 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| NC-1 | Done | `e34df98` (columns `origen`, `gravedad`, `contencion`, `causa_raiz`, `motivo_cancelacion`; `estado` enum with CHECK; migration `e7a9c1d3f5b8`; `cancel`, `reopen`; closed and cancelled NCs read-only; screens, PDF, dashboard and MCP updated), review follow-up in the next commit | RED: 11 modules `ImportError: EstadoNoConformidad`; PostgreSQL conversion still legacy text; then 20 state tests (`no attribute 'cancel'`, fields not allowed, CHECK failed). GREEN: 915 tests incl. PostgreSQL | Range `4bd9cdf..e34df98`: **medium**, `slice_budget_reached` (1,406 lines; standing Wave 1 size exception); consent granted; reliability review `review-62d26d7fd282d214` **approved** and acknowledged; the migration log now says "rewrote" (the count is every row, not only changed ones); the clock suggestion moved into NC-2 |
| NC-2..NC-4 | Pending | — | — | — |

## Findings during implementation

- The edit surface was widened for NC-1 with the user's approval (2026-10-09): `tests/test_ui_calidad.py`, `tests/test_mcp_modules.py`, `tests/test_service_list_page.py`, `tests/test_audit.py`, `docs/architecture/services.md` (one stale example).
- MCP and audit snapshots show enum display values ("Cancelada") while filters take member names (`cancelada`).
- The nonconformity policy only knows resource and action, so cancel (ADMIN, AUDITOR) and reopen (ADMIN) check the role in the service after `policy.require(UPDATE)`.
- An administrator can still delete a closed nonconformity (only updates are blocked); whether closed records may be deleted at all belongs with the soft-delete decision of document control (D3).
- Legacy rows keep their existing `fecha_cierre`.

## Next step

NC-2.
