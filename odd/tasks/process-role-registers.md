# Process and Role Registers

Repository locator: `odd/tasks/process-role-registers.md` · Engram mirror: `odd/process-role-registers/tasks`

## Objective

Give the four registers that are still reachable only through the MCP server and the JSON endpoints their web screens, and complete the process register (ISO 9001:2026 clauses 4.4 and 8.1) and the role register (5.3) so they hold what an auditor asks for.

## Problem and why it matters

- `ProcesoOperacion` (`procesos_operacion`) holds a name, a quality criterion, a supplier-control flag and a nonconformity text; it has no owner, inputs, outputs, resources, acceptance criteria or interactions (4.4.1 a–h, 8.1), and no screen, although quality objectives and risks already link to it.
- `RolResponsabilidad` (`roles_responsabilidades`) holds a name, a quality-commitment flag and a policy text; it has no responsibilities, authorities or reporting line (5.3), and no screen, although people and competence requirements already use it.
- `RecursoCapacitacion` and `AuditoriaIndicador` are legacy free-text registers without screens.
- Gap analysis, Wave 2 item 2 (`docs/analisis-brechas-iso-9001.md`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/process-role-registers` in worktree `iso9001-worktrees/user-management` from `main@0cf3e6b` | User: "sigue" after the assistant proposed this module next (2026-10-17) |
| PR0 | Scope "Pantallas y procesos y roles completos" | User choice (2026-10-17) |
| PR1 | A process gains a type (`estrategico`, `operativo`, `soporte`), an owner (person), inputs, outputs, resources and acceptance criteria (texts) and related processes (many-to-many with itself, no self-relation); the existing `criterio_calidad` is shown as the operating criteria; `control_proveedor` and `no_conformidad` stay | User choice; field mapping by the assistant |
| PR2 | A process page shows its related processes and the records that cite it: quality objectives and risks and opportunities | User choice |
| PR3 | A role gains responsibilities and authorities (texts) and "reports to" (another role, nullable, no cycles); its page shows the people who hold it and its competence requirements | User choice |
| PR4 | Training resources and audit indicators get list, detail and form screens with their current fields only | User choice |
| PR5 | Access keeps the current grant of each resource (every role reads; ADMIN and AUDITOR create and update; ADMIN deletes; `mcp` never deletes); the MCP modules expose the new fields | Existing policy |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 2 `size:exception` (ask only above 1,500 lines); production deployment needs explicit authorization | User authorizations (2026-10-02, 2026-10-17) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [ ] **RG-1 — Process and role model and services.** Forecast 700-1,100.
  - Migration (new columns, the process-relations table, the role reporting line), service rules (no self-relation, no reporting cycle, delete protection for related processes and reporting roles), MCP fields.
- [ ] **RG-2 — Process and role screens.** Forecast 900-1,400.
  - Lists with filters, details with related records (PR2, PR3), forms with pickers.
- [ ] **RG-3 — Training-resource and audit-indicator screens, and docs.** Forecast 600-1,000.
  - PR4 screens and the JSON endpoints' fate (kept for compatibility or retired, decided in the task); guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 4.4, 5.3, 7.1, 8.1 and the "registros sin pantallas web" list.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `0cf3e6b`: 1,420 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| RG-1..RG-3 | Pending | — | — | — |

## Next step

RG-1.
