# Planning of Changes

Repository locator: `odd/tasks/change-planning.md` · Engram mirror: `odd/change-planning/tasks`

## Objective

Carry out changes to the quality management system in a planned manner (ISO 9001:2026 clause 6.3): a change register with its origin, its purpose and potential consequences, the integrity of the QMS, the resources and the responsibilities, the affected processes and documents, how it is communicated, the tasks that carry it out, its approval and the review of its effectiveness, with screens, alerts, PDFs and the changes as a management-review input.

## Problem and why it matters

- 6.3 is partial: a management review can record a change to the QMS only as a review action of type `cambio_sgc` (`AccionRevision`, `app/models.py:1159-1198`), with an owner, a deadline, a state and a result. `cambio_sgc` is only a label: the action links to nothing else (`docs/analisis-brechas-iso-9001.md`, row 6.3).
- No change can start outside a review, and nothing records the purpose, the consequences, the integrity of the QMS, the resources, the responsibilities or the effectiveness of a change.
- The 2026 edition strengthens 6.3: more planning, communication, follow-up and review of the effectiveness of QMS changes (gap analysis, 2026 table).
- Gap analysis, Wave 2 item 5.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/change-planning` in worktree `iso9001-worktrees/user-management` from `main@339a9a8` | User: "si" to starting 6.3 (2026-10-24) |
| CP0 | Scope "Registro completo con tareas" | User choice (2026-10-24) |
| CP1 | **Change** (`cambios_sgc`, model `CambioSGC`): title, description, origin (`revision_direccion`, `no_conformidad`, `auditoria`, `riesgo`, `oportunidad`, `cuestion_contexto`, `otro`) with an optional link to the record that gives rise to it (`accion_revision_id`, `no_conformidad_id`, `auditoria_id`, `riesgo_id`, `oportunidad_id`, `cuestion_id`, each RESTRICT and allowed only with its origin by a CHECK; a review action must be of type `cambio_sgc`; a context issue must be `vigente`); purpose and potential consequences (6.3 a), QMS integrity (b), resources (c), responsibilities and authorities (d), communication (to whom and how); owner (person); proposal date; planned implementation date; affected processes (`cambio_procesos`) and documents (`cambio_documentos`), plain tables whose ids travel in the change's audit rows | User choice; fields, origins and links by the assistant |
| CP2 | **Lifecycle**, only through the service: `propuesto` → `aprobado` → `implantado` → `eficacia_revisada`, and `cancelado` from `propuesto` or `aprobado` with a reason. Approval needs the four considerations (a–d), a planned date and at least one task, and records the approver (the user's linked person) and the date. Implementation needs every task `completada` or `cancelada`, at least one `completada`, and an implementation date not after today. The effectiveness review records a result (`eficaz` or `no_eficaz`, a comment required for `no_eficaz`), a date not before the implementation and not after today, and a reviewer who is not the owner | Assistant design (as corrective-action verification and risk-action effectiveness) |
| CP3 | **Editing**: while `propuesto` every field; once `aprobado`, the plan (title, description, origin and link, the four considerations) is fixed and only the owner, the planned date, the communication, the processes and the documents change; `implantado`, `eficacia_revisada` and `cancelado` change nothing. Only a `propuesto` change is deleted, with its tasks | Assistant design |
| CP4 | **Tasks** (`tareas_cambio`, model `TareaCambio`): description, owner, deadline, state (`pendiente`, `en_curso`, `completada`, `cancelada`), completion date (required with `completada` and only then, not after today) and result; created, changed and deleted only while the change is `propuesto` or `aprobado`; a task never moves to another change | Assistant design (as risk actions) |
| CP5 | **Access**: a new resource `QMS_CHANGES`; every role reads; ADMIN and AUDITOR create and update changes and tasks; ADMIN deletes. Approval and cancellation are ADMIN only; implementation and the effectiveness review are ADMIN or AUDITOR. Every state change is web only: `mcp` creates and updates changes and tasks but never approves, implements, reviews, cancels or deletes | Existing grants; assistant design |
| CP6 | The dashboard card "Cambios" (6.3) flags changes awaiting approval, approved changes past their planned date, overdue tasks and implemented changes awaiting the effectiveness review. The management review gains a `cambios` input for the period (proposed, approved, implemented, reviewed with their results, cancelled, overdue), rendered only when present so reviews held before still render. The review action, nonconformity, audit, risk, opportunity and context issue pages list the changes they gave rise to | Assistant design |
| CP7 | PDFs: the change register (`/cambios/pdf`) and the change plan (`/cambios/<id>/pdf`: plan, considerations, tasks, approval, implementation and effectiveness) | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 2 `size:exception` (ask only above 1,500 lines); production deployment needs explicit authorization | User authorizations (2026-10-02, 2026-10-17) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct** (each task touches more than two non-trivial files).

- [x] **CP-1 — Change and task models, migration and policy.** Forecast 600-900.
  - Models `CambioSGC` and `TareaCambio`, plain tables `cambio_procesos` and `cambio_documentos`, CHECKs (origin links, approval, implementation, effectiveness, cancellation, task completion), migration on top of `e4a6c8b0d2f5`, resource `QMS_CHANGES`, delete protection for owners, approvers and reviewers (`people.REFERENCES`), processes, and the origin records (review actions and reviews, nonconformities, audits, risks, opportunities, context issues) with Spanish messages.
- [ ] **CP-2 — Change and task services and MCP modules.** Forecast 1,200-1,600.
  - Services with the CP1–CP5 rules (references, editing by state, transitions `approve`, `implement`, `review_effectiveness`, `cancel` with `may_*` guards, tasks), filters, `attention()`, `changes(session, desde, hasta)` for the review inputs, `linked` readers for the origin pages; MCP modules `cambios_sgc` and `tareas_cambio` (30 modules), transitions and deletes not exposed.
  - Also (CP-1 review): a test that inserts tasks, processes and documents out of order and loads the change through the session, pinning the order of `tareas` (`fecha_limite`, `id`), `procesos` (`proceso`) and `documentos` (`code`).
- [ ] **CP-3 — Screens and dashboard.** Forecast 1,200-1,500.
  - `/cambios/` list with filters, detail with tasks, forms, transition forms, task forms; the related changes on the origin pages; dashboard card "Cambios" (CP6); navigation 6.3.
- [ ] **CP-4 — PDFs, review inputs and docs.** Forecast 700-1,000.
  - The two PDFs (CP7), the `cambios` review input (CP6), guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 6.3.

Forecast: about 3,700-5,000 authored lines, so the feature ships as a chain of pull requests (`auto-chain`, `stacked-to-main`).

## Acceptance criteria

- A change can be proposed from any of the seven origins, with or without the linked record, and the link is refused for another origin, for a missing record, for a review action of another type and for a superseded context issue.
- A change cannot be approved without the four considerations, a planned date and a task; cannot be implemented with open tasks; cannot have its effectiveness reviewed by its owner; and `mcp` cannot change its state.
- Fixed fields refuse changes after approval; nothing changes after implementation or cancellation.
- Records cited by a change cannot be deleted, and the refusal says why.
- The dashboard, the origin pages, the PDFs and the management-review inputs show the changes; reviews held before still render.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `339a9a8`: 1,632 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`; run `docker start iso-pg-test` first).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| CP-1 | Done | `a341b90` (enums `OrigenCambio`, `EstadoCambio`, `EficaciaCambio`, `EstadoTareaCambio`; models `CambioSGC` with six origin links, each RESTRICT with an origin CHECK, and the approval, implementation, effectiveness, ineffective-comment, independent-reviewer and cancellation CHECKs; `TareaCambio` with the completion CHECK; plain tables `cambio_procesos` and `cambio_documentos`; `CHANGE_LINKS`, `proceso_ids`, `documento_ids`; migration `29637060aaf0`; resource `QMS_CHANGES`), `3451ebe` (delete protection: the four person columns in `people.REFERENCES`; processes `LINKED_TO_CHANGES`; review actions `ACTION_CITED_BY_CHANGES` and reviews `ACTIONS_CITED_BY_CHANGES` before `HAS_ACTIONS`; nonconformities, audits, risks, opportunities and context issues `*_CITED_BY_CHANGES`, checked first and mapped by foreign key name where the service already had a map) | RED: 48 errors and 2 failures, e.g. `module 'app.models' has no attribute 'CambioSGC'`, `KeyError: 'QMS_CHANGES'`, `31 != 29`, `NoSuchTableError: cambios_sgc`, missing `RISK_CITED_BY_CHANGES`. GREEN: `a341b90` alone 1,657 tests OK; both 1,679 tests OK incl. PostgreSQL; adapted `tests/test_attribution.py` (31), `tests/test_policy.py`, `tests/test_access_characterization.py` (`NO_ROUTES`), `tests/test_migrations.py` | Range `339a9a8..3451ebe`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-af97da5665dad63f` **approved** and acknowledged; its suggestion (relationship order observed through a loaded instance) moved into CP-2 |

## Findings during implementation

- The refusal messages state only the citation (for example «No se puede eliminar un riesgo al que se refieren cambios en el SGC.»), without the usual "remove it first" advice, because an approved change's origin link cannot change.
- Where a service had no foreign-key map (nonconformities, review actions), a citation added after the check ends as a plain `Conflict` with the default message, never a raw `IntegrityError`. Deleting an audit programme never deletes its audits, so it needs no check.
- For CP-2: create tasks as `TareaCambio(cambio_id=...)`, not by appending to `cambio.tareas` (appending marks the change as modified and the audit guard then expects an audit row for it); `tareas` uses `passive_deletes='all'`, so delete a change's tasks before the change. The database does not check that a linked review action is of type `cambio_sgc`, that a linked context issue is `vigente`, or the approval prerequisites: the service must.
- `ServiceBase.setUp` assigns `self.audit` to the audit module, so a test helper named `audit` is shadowed.

## Delivery

| Slice | Pull request | Commits | Merged as |
|---|---|---|---|
| 1 | pending | tracker `f677450`, CP-1 models `a341b90` | |
| 2 | pending | CP-1 delete protection `3451ebe`, this record | |

## Next step

CP-2: change and task services and the MCP modules.
