# Management Review

Repository locator: `odd/tasks/management-review.md` · Engram mirror: `odd/management-review/tasks`

## Objective

Hold management reviews for a period with the inputs of ISO 9001:2026 clause 9.3.2 drawn automatically from the registers, record the conclusions and the outputs of 9.3.3 as actions with an owner, a deadline and follow-up, and keep the minutes as a PDF.

## Problem and why it matters

- Clause 9.3 is mandatory and absent: the only trace is the document category `INFORME_REVISION`; there is no review entity, no inputs, no outputs, no decisions and no follow-up (`docs/iso-9001-gap-analysis.md`, gap 2).
- The other Wave 1 modules (nonconformities and corrective actions, audits and findings, quality objectives) exist now and are the review's inputs.
- Gap analysis Wave 1 item 5, the last one.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/management-review` in worktree `iso9001-worktrees/user-management` from `main@e5ecfed` | User: "todas, elige tú el orden"; design choice (2026-10-12) |
| MR0 | Scope "Completa con datos y acciones" | User choice (2026-10-12) |
| MR1 | A review has meeting date, period (from, to; the period ends on or before the meeting date), attendees (people), state `borrador` → `celebrada` → `cerrada`, a comment per input, and conclusions on the suitability, adequacy and effectiveness of the QMS | User choice; fields by the assistant |
| MR2 | Inputs (9.3.2) come from the registers for the period: status of earlier reviews' actions; changes in stakeholders and in risks and opportunities; customer satisfaction; quality objectives; process indicators; nonconformities and corrective actions with their effectiveness; audits and findings by type; improvements; training. In `borrador` the summary is computed live; when the review is held (`celebrada`) it is frozen as stored data, so the minutes show what was presented. Inputs without a register (external providers) are covered by the comments | User choice; mapping and freezing by the assistant |
| MR3 | Outputs (9.3.3) are actions of type `mejora`, `cambio_sgc` or `recursos` with description, owner (person), deadline, state (`pendiente`, `en_curso`, `completada`, `cancelada`), completion date and result. Actions are added while the review is `borrador` or `celebrada`; once it is `cerrada` only their state, completion date and result change (follow-up). An action is overdue when `pendiente` or `en_curso` past its deadline; the dashboard flags overdue actions | User choice; lifecycle by the assistant |
| MR4 | Holding and closing are ADMIN decisions (top management) and web-only: `celebrada` needs a meeting date not in the future and at least one attendee; `cerrada` needs the conclusions. No reopening | Assistant design |
| MR5 | Access: ADMIN and AUDITOR read and write reviews in `borrador` and actions; ADMIN holds, closes and deletes; OPERATIVO has no access; `mcp` never deletes, holds or closes | Assistant design (existing `AUDITS`-style grant) |
| MR6 | A review can be deleted only in `borrador` and without actions | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 1 `size:exception` (ask only above 1,500 lines); production deployment needs migrations and explicit authorization | User authorizations (2026-10-02, 2026-10-09) |
| Test adaptations | Standing Wave 1 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR | User (2026-10-10) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |
| Size MR-1 | One-off `size:exception` for the service pull request (1,941 lines) | User (2026-10-12) |

## Tasks

Route for every task: **delegated direct**.

- [x] **MR-1 — Reviews, inputs and actions (service).** Forecast 1,000-1,400.
  - Models, migration, the period input summary from the existing registers, hold and close transitions with the frozen summary, actions with follow-up rules, policy resource `MANAGEMENT_REVIEW`, MCP modules.
- [x] **MR-2 — Screens and dashboard.** Forecast 900-1,400.
  - Review list and detail with the input sections and comments, review and action forms, hold and close actions, action register with filters, dashboard card with overdue actions.
- [x] **MR-3 — Minutes PDF and docs.** Forecast 300-600.
  - Minutes PDF, README, `docs/mcp.md`, `docs/architecture/services.md`, gap analysis update (Wave 1 complete).

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `e5ecfed`: 1,282 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| MR-1 | Done | `9195391` (models `revisiones_direccion`, `revision_asistentes`, `acciones_revision`, migration `a9c1e3f5d7b2`, policy `MANAGEMENT_REVIEW`, people delete protection), `562806b` (`app/services/management_reviews.py`: registers, `inputs`, `hold`, `close`, `preview`, `overdue_actions`; MCP modules `revisiones_direccion`, `acciones_revision`: 22 modules), review follow-up `c48ee51` | RED: `ImportError: RevisionDireccion`, attribution `23 != 21`, `KeyError: 'MANAGEMENT_REVIEW'`; follow-up: moving the review date past a completed action was not refused. GREEN: 1,309, then 1,310 tests incl. PostgreSQL; `9195391` alone passed 1,283 | Range `e5ecfed..562806b`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-57a059d44858b025` **approved** and acknowledged; its warning fixed in `c48ee51` |
| MR-2 | Done | `45f4f0b` (`/revisiones-direccion/` list, detail with the input sections (provisional in draft, frozen once held), comments and conclusions, hold and close for ADMIN, review and action forms, `action_counts` for the list), `263f19d` (action register `/revisiones-direccion/acciones`, dashboard card `#acciones-revision`), review follow-up `bd16465` | RED: 10 screen tests (`no attribute 'management_review_routes'`); characterization and UI permission additions failed with the blueprint unregistered (`200 != 404`, 26 subtests); follow-up: an unknown numeric owner emptied the register. GREEN: 1,323 tests incl. PostgreSQL; `45f4f0b` alone passed 1,321 | Range `562806b..263f19d` (includes the MR-1 follow-up `c48ee51`): **medium**, `slice_budget_reached`; consent granted; reliability review `review-81d88265ab7a3875` **approved** and acknowledged; its suggestion applied in `bd16465` |
| MR-3 | Done | `3eb14be` (minutes PDF `/revisiones-direccion/<id>/pdf` with header, inputs and comments, conclusions, actions and generation date, sharing `_review_context()` with the detail page; README section; `docs/mcp.md` (22 modules); `docs/architecture/services.md`; gap analysis: 9.3 covered and Wave 1 complete) | RED: 3 `no attribute 'render_pdf'`, missing PDF link, 8 UI permission subtests, 2 characterization URLs. GREEN: 1,327 tests incl. PostgreSQL; a real WeasyPrint PDF read back with `pdftotext` | Range `263f19d..3eb14be` (includes the MR-2 follow-up `bd16465`): **medium**, `slice_budget_reached`; consent granted; reliability review `review-49b719ca140fbe52` **approved** with no findings and acknowledged |

## Findings during implementation

- **Inputs:** every section is cut off at the earlier of `hasta` and today. Earlier reviews' actions come from every review dated before this one, drafts included; stakeholders and risks count records created or modified in the period by UTC day (risks have no level, so they are counted by type); objectives overlapping the period, except cancelled, are judged as of the cut-off.
- `datos_entrada` stores "no data" as SQL NULL (`none_as_null=True`); with the default JSON `null` the CHECK that a draft holds no frozen inputs never fires.
- Moving an action away from `completada` without clearing its completion date is refused, not cleared silently; moving the review date past a completed action is refused (`ACTIONS_COMPLETED_BEFORE`).
- **Screens (MR-2):** hold and close are ADMIN-only buttons covered by the screens test (the characterization table keeps the endpoints any granted role reaches); the input labels live in `input_sections()` in the routes module so the minutes PDF reuses them; the new-action form also takes the follow-up fields; register filters that name nothing (state, type, overdue flag, owner) are ignored.
- **Minutes (MR-3):** the file is named after the meeting date (`acta_revision_direccion_YYYY-MM-DD.pdf`); a draft prints "BORRADOR — datos provisionales". The gap analysis marks 9.3 covered and Wave 1 complete, and states that the other clause rows still describe the baseline before Wave 1 (re-assessing them is a follow-up).
- Test adaptations (standing approval, MR-3): `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`.
- Test adaptations (standing approval, MR-2): `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`.
- MCP extras get only the row, so the review module uses `preview_of` (no permission check) after the module's read check.
- Test adaptations (standing approval): `tests/test_attribution.py`, `tests/test_policy.py`, `tests/test_access_characterization.py`, `tests/test_migrations.py`, `tests/test_mcp_modules.py`, `tests/test_mcp_http.py`, `tests/mcp_support.py`.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#112](https://github.com/constant1n0/iso9001/pull/112) | tracker, MR-1 models | merged | |
| 2 | [#113](https://github.com/constant1n0/iso9001/pull/113) | MR-1 service, follow-up, record | `612bde6` | One-off `size:exception` (1,941 lines) |
| 3 | [#114](https://github.com/constant1n0/iso9001/pull/114) | MR-2 screens `45f4f0b` | merged | 1,379 lines |
| 4 | [#115](https://github.com/constant1n0/iso9001/pull/115) | MR-2 register and card, follow-up, record | `442d4b5` | |
| 5 | [#116](https://github.com/constant1n0/iso9001/pull/116) | MR-3, closing update | `60f62c6` | |

## Production deployment (2026-10-13)

`main@60f62c6` runs on `vulcano`. The user authorized this deployment over SSH as `dcm`, with no `sudo` by the agent.

| Step | Result |
|---|---|
| Preflight | Was `024828f`, clean, migration `b4d6f8a1c3e5` |
| Backups | `~/work/backups/calidad-*-pre-60f62c6.dump` (`pg_dump -Fc`, 316 entries listed by `pg_restore -l`) and `iso9001-code-*-pre-60f62c6.tar.gz` (without `instance/documents`), both mode 600 |
| Code | Incremental git bundle `024828f..main`, fast-forward to `60f62c6`; `pip check` clean, no dependency changes |
| Database | `flask db upgrade` `b4d6f8a1c3e5` → `a9c1e3f5d7b2`; `flask db check` clean |
| Restart (user, with sudo) | All four services |
| Smoke tests | Services active, no error entries in the journal; `/login` 200; `/`, `/revisiones-direccion/` and its action register redirect to the login; `/mcp` 401 with `WWW-Authenticate: Bearer`; 22 MCP modules |

## Next step

**Feature complete and deployed; Wave 1 complete.** Follow-up: re-assess the gap analysis clause rows against the delivered Wave 1 (they still describe the baseline).
