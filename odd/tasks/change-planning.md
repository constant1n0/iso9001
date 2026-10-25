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
| Size CP-2 | One-off `size:exception` for the services pull request (2,387 lines) | User (2026-10-24) |
| Size CP-3 | One-off `size:exception` for the screens pull request (about 1,890 lines) | User (2026-10-24) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct** (each task touches more than two non-trivial files).

- [x] **CP-1 — Change and task models, migration and policy.** Forecast 600-900.
  - Models `CambioSGC` and `TareaCambio`, plain tables `cambio_procesos` and `cambio_documentos`, CHECKs (origin links, approval, implementation, effectiveness, cancellation, task completion), migration on top of `e4a6c8b0d2f5`, resource `QMS_CHANGES`, delete protection for owners, approvers and reviewers (`people.REFERENCES`), processes, and the origin records (review actions and reviews, nonconformities, audits, risks, opportunities, context issues) with Spanish messages.
- [x] **CP-2 — Change and task services and MCP modules.** Forecast 1,200-1,600.
  - Services with the CP1–CP5 rules (references, editing by state, transitions `approve`, `implement`, `review_effectiveness`, `cancel` with `may_*` guards, tasks), filters, `attention()`, `changes(session, desde, hasta)` for the review inputs, `linked` readers for the origin pages; MCP modules `cambios_sgc` and `tareas_cambio` (30 modules), transitions and deletes not exposed.
  - Also (CP-1 review): a test that inserts tasks, processes and documents out of order and loads the change through the session, pinning the order of `tareas` (`fecha_limite`, `id`), `procesos` (`proceso`) and `documentos` (`code`).
- [x] **CP-3 — Screens and dashboard.** Forecast 1,200-1,500.
  - `/cambios/` list with filters, detail with tasks, forms, transition forms, task forms; the related changes on the origin pages; dashboard card "Cambios" (CP6); navigation 6.3.
  - Also (CP-2 review): an approved change refuses clearing `fecha_prevista` with a rule message (today it drops out of every overdue check), with a test; `_update` and `_move` translate an integrity failure raised inside `audit.record` into `Conflict(WRITE_CONFLICT)` as `_create` does, with a test that drives one.
  - The document picker and the change detail respect the documents service's visibility rule (an OPERATIVO user does not see draft documents).
- [x] **CP-4 — PDFs, review inputs and docs.** Forecast 700-1,000.
  - The two PDFs (CP7), the `cambios` review input (CP6, capping the `in_period` lists), guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 6.3.
  - Also (CP-3 review): route tests for the origin picker's degraded branches (a proposed change whose review action was retyped or whose context issue was superseded keeps its link selected and labelled «(no elegible)» on the edit form, and re-posting it unchanged works; an origin group the actor cannot read is left out); a list page beyond the last one clamps to the last page instead of showing the empty state; the dashboard test with two overdue tasks in one change pins whether the alert counts tasks or changes, and the badge and the linked list agree.

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

| CP-2 | Done | `7ae726f` (`app/services/qms_changes.py`: registers `changes` and `tasks` with `create`, `update` and `delete` taking `today`; origin links checked (matching origin, existing record, review action `cambio_sgc`, issue `vigente`, each locked `FOR SHARE`); processes and documents in the audit snapshots; editing by state (`CHANGE_WRITABLE`, `APPROVED_WRITABLE`); delete only `propuesto`, tasks first; transitions `approve`, `implement`, `review_effectiveness`, `cancel` with `may_approve`, `may_implement`, `may_review_effectiveness`, `may_cancel` (role and state; never `mcp`); filters incl. `fuera_de_plazo` and `vencida`; readers `attention`, `in_period` (`PeriodChanges`), `for_origin`; the CP-1 review ordering test), `3114de4` (MCP modules `cambios_sgc` and `tareas_cambio` through `_DatedRegister`, create, update, list and get only; 30 modules) | RED: `cannot import name 'qms_changes'` (125 errors), `KeyError: 'cambios_sgc'`, `30 != 28`, «Módulo desconocido: «cambios_sgc»»; the ordering test passed at once (CP-1 already orders the relationships). GREEN: `7ae726f` alone 1,742 tests OK; both 1,748 tests OK incl. PostgreSQL; adapted `tests/test_mcp_modules.py`, `tests/test_mcp_http.py`, `tests/test_mcp_write_tools.py`, `tests/mcp_support.py` | Range `3451ebe..3114de4`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-53b8b900068f7295` **approved** and acknowledged; its warning (an approved change can clear its planned date) and suggestion (integrity failures inside `audit.record` in `_update`) moved into CP-3 |
| CP-3 | Done | `480fc75` (CP-2 review: an approved change refuses clearing `fecha_prevista`; integrity failures inside `audit.record` in update, approve and cancel become `Conflict(WRITE_CONFLICT)`; filter `tareas_vencidas`), `54bbaa4` (blueprint `changes` at `/cambios`: list with filters and paging, detail with the plan a) to d), processes, documents through the documents visibility rule, tasks and the decision data; new, edit and delete forms showing only the fields the state allows; POST transitions `aprobar`, `implantar`, `eficacia`, `cancelar`, each re-checking its `may_*` guard; task forms; navigation "Cambios" 6.3 in "Calidad"), `e4278b5` (dashboard card "Cambios" from `attention`; the review (its `cambio_sgc` actions with «Planificar cambio»), nonconformity, audit, risk, opportunity and context-issue pages list the changes that cite them; cited issues, risks and opportunities no longer offer a delete button) | RED: `_change_conditions() got an unexpected keyword argument`, raw `IntegrityError` on update, approve and cancel, `ValidationError not raised`; 16 errors `module 'app.routes' has no attribute 'change_routes'`; missing origin cards, «Planificar cambio» and dashboard card. GREEN: `54bbaa4` alone 1,768 tests OK; all three 1,774 tests OK incl. PostgreSQL; adapted `tests/test_access_characterization.py` (8 endpoints, `QMS_CHANGES` out of `NO_ROUTES`), `tests/test_ui_permissions.py`, `tests/test_dashboard.py` | Range `3114de4..e4278b5`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-dfb96ff7eff0443c` **approved** and acknowledged; its warning (untested degraded picker branches) and two suggestions (out-of-range page, overdue-task count) moved into CP-4 |
| CP-4 | Done | `1aaaf29` (`/cambios/pdf`, the register by state with the late changes, and `/cambios/<id>/pdf`, the plan with a) to d), tasks and the decision data, sharing a `cambios/pdf_template.html` layout; links from the list and detail; review input `cambios` under input c) with the period counts, the late changes and the implemented or reviewed changes capped with `LIST_LIMIT` and their full count, on screen and in the minutes PDF only when present; the minutes PDF names the changes each review action gives rise to; `in_period` gains `cut_off` (the review passes the earlier of today and the period end); CP-3 review: picker degraded-branch route tests, a page past the last one shows the last page, the overdue-task alert counts changes («Cambios con tareas vencidas») so the badge matches the linked list; the unclosed licence comment in six change templates closed), `024dc97` (guides, Spanish first, `docs/mcp.md` with 30 modules, `docs/architecture/services.md`, READMEs; gap analysis: 6.3 covered, 17 covered, 10 partial, 6 not covered, 1 documentary only), `aec9bb4` (a test that every template closes its HTML comments) | RED: `no attribute 'render_pdf'`, `302 != 404`, `KeyError: 'cambios'`, missing review figures and minutes text, `[1] != []` for the page clamp, `KeyError: 'Cambios con tareas vencidas'`, missing PDF links; the two picker tests passed at once (CP-3 already handled those branches); the comment test fails on the CP-3 templates at `e4278b5` (six templates, comment at offset 0 never closed). GREEN: `1aaaf29` alone 1,790 tests OK incl. PostgreSQL; link check 0 errors; the comment test passes; adapted `tests/test_dashboard.py`, `tests/test_access_characterization.py`, `tests/test_ui_permissions.py` | Range `e4278b5..024dc97`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-b0232da1be7213a8` **approved** and acknowledged; three suggestions: the comment one is done in `aec9bb4` (assessed **medium**, `under_budget`, so no review of its own), the other two are follow-ups |

## Findings during implementation

- The refusal messages state only the citation (for example «No se puede eliminar un riesgo al que se refieren cambios en el SGC.»), without the usual "remove it first" advice, because an approved change's origin link cannot change.
- Where a service had no foreign-key map (nonconformities, review actions), a citation added after the check ends as a plain `Conflict` with the default message, never a raw `IntegrityError`. Deleting an audit programme never deletes its audits, so it needs no check.
- For CP-2: create tasks as `TareaCambio(cambio_id=...)`, not by appending to `cambio.tareas` (appending marks the change as modified and the audit guard then expects an audit row for it); `tareas` uses `passive_deletes='all'`, so delete a change's tasks before the change. The database does not check that a linked review action is of type `cambio_sgc`, that a linked context issue is `vigente`, or the approval prerequisites: the service must.
- `ServiceBase.setUp` assigns `self.audit` to the audit module, so a test helper named `audit` is shadowed.
- CP-2 choices: the period reader is `in_period` (`changes` is the register) and returns uncapped lists, so CP-4 caps them in the review inputs; `attention` and `for_origin` take the actor and check the read grant; overdue tasks count only while their change is `propuesto` or `aprobado`; an update that changes nothing is accepted and writes nothing, even on a closed change; `fecha_propuesta` is fixed once approved; MCP field notes are in English like their neighbours.
- A change built without process or document lists lazy-loaded them between `audit.record` and the `row.after` patch, so the autoflush persisted the audit row and the patch raised `AuditLogImmutable`; both lists are now set, even empty, when the change is built.
- For CP-3: the `may_*` guards check only role and state; the owner-reviewing refusal comes back as a message on submit.
- CP-3 choices: the origin-record picker groups the records by origin (filtering as the origin changes would need `app/static/js/app.js`, outside the surface) and the form refuses a record of another origin on its field; a refused transition is flashed and redirects, as provider decisions do, so a typed comment is lost; a user who cannot read the record behind a change sees «Registro vinculado» without a link; delete confirmation uses the existing `data-confirm` attribute; `auditoria_routes` imports `citing_changes` inside the view to avoid an import cycle (`change_routes` imports `committed` from it).
- For CP-4: `_review_context` already carries `cambios` to the review PDF, unused; `origin_of`, `cambios/_macros.html` and `cambios/_citing.html` can be reused; `in_period` is still unused.
- WTForms 3 dict choices put every option in an optgroup, so a `GroupedSelect` widget renders the empty choice ungrouped.
- CP-4: the CP-3 change templates opened their licence comment without closing it. In the extending templates and the macros nothing outside the blocks renders, but `cambios/_citing.html` is included, so in the browser the «Cambios relacionados» card on the origin pages was hidden inside a comment from `#164` until `1aaaf29`; no deployment carried it. `tests/test_template_comments.py` now guards every template.
- `tests/test_ui_cierre.py` allows a `<style>` block only in templates named `pdf_template.html` (and `reporte_mensual.html`), so two PDFs in one directory share a `pdf_template.html` layout.
- `in_period` judged lateness at `hasta`, which counted future planned dates as late for a period ending after today; `cut_off` fixes it.

## Follow-ups

- A service test passing a non-date `cut_off` to `qms_changes.in_period` (CP-4 review).
- The register PDF test with two late changes, pinning the most overdue first (CP-4 review).

## Delivery

| Slice | Pull request | Commits | Merged as |
|---|---|---|---|
| 1 | [#159](https://github.com/constant1n0/iso9001/pull/159) | tracker `f677450`, CP-1 models `a341b90` | `8982daa` |
| 2 | [#160](https://github.com/constant1n0/iso9001/pull/160) | CP-1 delete protection `3451ebe`, record `fc677d2` | `920f929` |
| 3 | [#161](https://github.com/constant1n0/iso9001/pull/161) | CP-2 services `7ae726f` (one-off `size:exception`, 2,387 lines, approved by the user on 2026-10-24) | `8dffcce` |
| 4 | [#162](https://github.com/constant1n0/iso9001/pull/162) | CP-2 MCP modules `3114de4`, record `e7c6750` | `b88e692` |
| 5 | [#163](https://github.com/constant1n0/iso9001/pull/163) | CP-3 service fixes `480fc75` and screens `54bbaa4` (one-off `size:exception`, about 1,890 lines, approved by the user on 2026-10-24) | `6065586` |
| 6 | [#164](https://github.com/constant1n0/iso9001/pull/164) | CP-3 dashboard and origin pages `e4278b5`, record `73653fc` | `385cf77` |
| 7 | pending | CP-4 PDFs and review input `1aaaf29` | |
| 8 | pending | CP-4 docs `024dc97`, template comment test `aec9bb4`, closing record | |

## Production deployment (pending authorization)

One migration (`29637060aaf0`, the change and task tables) on top of `e4a6c8b0d2f5`: back up the database and the code, fast-forward, `flask db upgrade`, `flask db check`, then the user restarts the four services. Smoke tests: `/cambios/` and `/cambios/pdf` redirect to the login, 30 MCP modules including `cambios_sgc` and `tareas_cambio`.

## Next step

**Feature complete** once slice 8 merges. Next: the production deployment above, after explicit authorization. Wave 2 continues with customers and complaints (8.2, 9.1.2), nonconforming outputs (8.7) and the link between nonconformities and processes.
