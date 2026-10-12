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

## Tasks

Route for every task: **delegated direct**.

- [ ] **MR-1 — Reviews, inputs and actions (service).** Forecast 1,000-1,400.
  - Models, migration, the period input summary from the existing registers, hold and close transitions with the frozen summary, actions with follow-up rules, policy resource `MANAGEMENT_REVIEW`, MCP modules.
- [ ] **MR-2 — Screens and dashboard.** Forecast 900-1,400.
  - Review list and detail with the input sections and comments, review and action forms, hold and close actions, action register with filters, dashboard card with overdue actions.
- [ ] **MR-3 — Minutes PDF and docs.** Forecast 300-600.
  - Minutes PDF, README, `docs/mcp.md`, `docs/architecture/services.md`, gap analysis update (Wave 1 complete).

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `e5ecfed`: 1,282 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| MR-1..MR-3 | Pending | — | — | — |

## Next step

MR-1.
