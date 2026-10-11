# Audit Programme and Findings

Repository locator: `odd/tasks/audit-programme.md` · Engram mirror: `odd/audit-programme/tasks`

## Objective

Plan internal audits in an approved yearly programme, give each audit its scope, criteria, lead auditor, team and audited-area owner with an independence check, record classified findings with clause and evidence, and turn every nonconformity finding into a nonconformity automatically (ISO 9001:2026 clause 9.2, feeding 10.2).

## Problem and why it matters

- `Auditoria` is a standalone record: area, date, auditor (text plus person link), result text, corrective action text and state (`app/models.py`, `app/services/audits.py`).
- There is no audit programme, no scope or criteria, no audit team, no independence check and no findings, so clause 9.2.2 cannot be evidenced and audit nonconformities are not linked to the corrective-action loop.
- Gap analysis Wave 1 item 3 (`docs/iso-9001-gap-analysis.md`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/audit-programme` in worktree `iso9001-worktrees/user-management` from `main@88cd706` | User: "todas, elige tú el orden"; design choice (2026-10-10) |
| AP0 | Scope "Programa anual + hallazgos" | User choice (2026-10-10) |
| AP1 | A programme per year (unique year) with objectives and scope; states `borrador` → `aprobado` (ADMIN approves, with date and approver person); an approved programme's year cannot change | User choice; states by the assistant |
| AP2 | Each audit may belong to a programme (optional for legacy audits) and gains scope, criteria, lead auditor (the existing `auditor_id`), team (people) and audited-area owner (person) | User choice |
| AP3 | Independence: neither the lead auditor nor any team member may be the audited-area owner (Spanish `ValidationError`) | User choice |
| AP4 | Findings per audit: type `nc_mayor`, `nc_menor`, `observacion`, `oportunidad_mejora`; description, ISO clause reference (text such as "8.5.1") and evidence | User choice |
| AP5 | A nonconformity finding (`nc_mayor`, `nc_menor`) creates its nonconformity automatically in the same transaction: origin `auditoria`, severity `mayor` / `menor`, description and detection date from the finding and audit, responsible person from the audited-area owner; the finding keeps the link. Deleting or downgrading such a finding never deletes the nonconformity | User choice; lifecycle rule by the assistant |
| AP6 | `AuditoriaIndicador` stays a separate register | User choice |
| AP7 | Access: audits, programmes and findings keep the `AUDITS` grant (ADMIN and AUDITOR read and write, ADMIN deletes); OPERATIVO does not see them, as today | Existing policy |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 1 `size:exception` (ask only above 1,500 lines); production deployment needs migrations and explicit authorization | User authorizations (2026-10-02, 2026-10-09) |
| Test adaptations | Standing Wave 1 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR | User (2026-10-10) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [ ] **AP-1 — Programme and audit planning fields.** Forecast 600-1,000.
  - `ProgramaAuditoria` model, service and approval; `Auditoria` gains programme, scope, criteria, team, audited-area owner; independence check; migration; MCP module and fields.
- [ ] **AP-2 — Findings and automatic nonconformities.** Forecast 500-900.
  - `HallazgoAuditoria` model and service; automatic nonconformity for NC findings (AP5) through the nonconformity service; MCP module.
- [ ] **AP-3 — Screens.** Forecast 800-1,300.
  - Programme list and detail with its audits; audit form with the new fields and pickers; audit detail with findings and links to their nonconformities; approval action.
- [ ] **AP-4 — Reports and docs.** Forecast 250-500.
  - Audit PDF with scope, criteria, team and findings; programme PDF or summary; README and `docs/mcp.md`.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `88cd706`: 1,126 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| AP-1..AP-4 | Pending | — | — | — |

## Next step

AP-1.
