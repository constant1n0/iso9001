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

- [x] **RG-1 — Process and role model and services.** Forecast 700-1,100.
  - Migration (new columns, the process-relations table, the role reporting line), service rules (no self-relation, no reporting cycle, delete protection for related processes and reporting roles), MCP fields.
- [x] **RG-2 — Process and role screens.** Forecast 900-1,400.
  - Lists with filters, details with related records (PR2, PR3), forms with pickers.
- [ ] **RG-3 — Training-resource and audit-indicator screens, and docs.** Forecast 600-1,000.
  - PR4 screens and the JSON endpoints' fate (kept for compatibility or retired, decided in the task); guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 4.4, 5.3, 7.1, 8.1 and the "registros sin pantallas web" list.
  - Also (RG-2 review): the role-delete expectations in `tests/test_ui_permissions.py` and `tests/test_access_characterization.py` assert which seeded role holds the competence requirement instead of relying on a comment.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `0cf3e6b`: 1,420 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| RG-1 | Done | `16f4715` (migration `f5a7c9e1b3d6`: process `tipo`, `responsable_id`, `entradas`, `salidas`, `recursos`, `criterios_aceptacion`, table `proceso_relaciones` with CHECK `proceso_id < relacionado_id` (no self-relation, canonical order); role `responsabilidades`, `autoridades`, `reporta_a_id` with no self-reference; services: `relacionados` replaced as a set, `related`, `cited_by` (objectives, risks, opportunities), roles' cycle walk, `holders` (people incl. inactive, competence requirements), delete refused while related or reported to; people: a process owner cannot be deleted; MCP fields, `relacionados` extra, `tipo` and `responsable_id` filters, still 24 modules) | RED: 15 failures and 17 errors (`Campos no permitidos: relacionados`, missing `TipoProceso`, `cited_by`, `holders`, `NoSuchTableError: proceso_relaciones`). GREEN: 1,440 tests OK incl. PostgreSQL; adapted `tests/test_mcp_modules.py` (processes in the bespoke writable map) and `tests/test_migrations.py` | Range `0cf3e6b..eb40233`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-cbc338fceeaabbcb` **approved** and acknowledged; both warnings (a rename to a taken name with a relations change, impossible ids on update) were pinned by tests in `d0ba34b`, which passed at once on SQLite and PostgreSQL: the behaviour was already right. The local PostgreSQL test container had stopped; it was restarted and the full suite re-run: 1,444 tests OK |
| RG-2 | Done | `3f6d742` (`/procesos/` and `/roles/` lists, details and forms in `app/routes/process_role_routes.py`; process page with related processes both ways and its citing objectives, risks and opportunities; role page with the reporting line up to the top, direct subordinates, holders and competence requirements; `process_operations.counts` for the list columns in grouped queries; navigation "Procesos 4.4" and "Roles y responsabilidades 5.3"; the JSON API at `/proceso_operacion/` and `/rol_responsabilidad/` unchanged) | RED: 52 failures (404s) in the screen tests, 24 UI-permission and 70 characterization failures. GREEN: 1,460 tests OK incl. PostgreSQL; adapted `tests/test_access_characterization.py` and `tests/test_ui_permissions.py` | Range `b3c89ba..20f9a99`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-030365ac582a73be` **approved** and acknowledged; its suggestion moved into RG-3 |
| RG-3 | Pending | — | — | — |

## Findings during implementation

- The process delete keeps calling `crud.delete`, because existing tests patch `process_operations.crud.delete`.
- `proceso_relaciones` has no audit model (the `persona_roles` precedent); process create and update rows carry `relacionados`.
- The reporting-cycle check takes no locks, so two concurrent updates could still form a cycle.
- Delete buttons are hidden for a related or cited process and for a role with requirements or subordinates; a direct POST still gets the service's refusal. An edit form posting no related process clears the relations (WTForms posts an empty list).
- For RG-3: the JSON auto-schema dump leaves out `responsable_id`, `reporta_a_id` and `relacionados` (`app/schemas.py`).

## Next step

RG-3.
