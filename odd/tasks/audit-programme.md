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

- [x] **AP-1 — Programme and audit planning fields.** Forecast 600-1,000.
  - `ProgramaAuditoria` model, service and approval; `Auditoria` gains programme, scope, criteria, team, audited-area owner; independence check; migration; MCP module and fields.
- [x] **AP-2 — Findings and automatic nonconformities.** Forecast 500-900.
  - `HallazgoAuditoria` model and service; automatic nonconformity for NC findings (AP5) through the nonconformity service; MCP module; decide and document what deleting an audit does to its findings (they hold nonconformity links).
  - Also (AP-1 review): `audit_programmes.approve` maps an `IntegrityError` to a Spanish `Conflict` (not `Conflict(None)`); tests for a non-mapping payload to `audits.create`/`update`, for editing an audit whose team member became inactive, and for MCP `qms_update`/`qms_get` on `programas_auditoria`.
- [ ] **AP-3 — Screens.** Forecast 800-1,300.
  - Programme list and detail with its audits; audit form with the new fields and pickers; audit detail with findings and links to their nonconformities; approval action.
  - Also (AP-2 review): an NC finding on a future-dated audit or with a long description must not fail with a nonconformity-service message (test it, or check before creating); an audit delete refused by a constraint other than the findings foreign key should not claim "has findings".
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
| AP-1 | Done | `f16eb68` (`programas_auditoria`, `auditoria_equipo`, audit `programa_id`, `alcance`, `criterios`, `responsable_area_id`, migration `d7f1b3a5c9e2`, `app/services/audit_programmes.py` with `approve`, independence and year checks, person delete protection, MCP module `programas_auditoria` (17 modules), audit form fields) | RED: 59 tests (`ImportError: audit_programmes`, `KeyError: 'programa_id'`, unknown module, counts). GREEN: 1,164 tests incl. PostgreSQL | Range `0864670..f16eb68`: **medium**, `slice_budget_reached` (1,498 code lines plus the tracker); consent granted; reliability review `review-4561977c88243678` **approved** and acknowledged; its warning (editing an audit whose team member became inactive is untested) and three suggestions moved into AP-2 |
| AP-2 | Done | `e6c3c58` (`hallazgos_auditoria`, migration `e9c2a4f6b8d1`, `app/services/audit_findings.py`, automatic nonconformity for NC findings with origin `auditoria`, severity, audit date and the active area owner, audit delete refused while it has findings, MCP module `hallazgos_auditoria` (18 modules), AP-1 review items), review follow-up `efda549` | RED: 61 `ImportError: audit_findings`, missing models and table, `APPROVE_CONFLICT`, counts, non-mapping payloads raising `TypeError`; follow-up: a re-typed finding whose nonconformity was deleted got none. GREEN: 1,191, then 1,192 tests incl. PostgreSQL | Range `80d663d..e6c3c58`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-086d9ff4a030a937` **approved** and acknowledged; its warning fixed in `efda549` (every NC finding without a link gets a nonconformity, whatever the previous type); two suggestions moved into AP-3 |
| AP-3..AP-4 | Pending | — | — | — |

## Findings during implementation

- A draft programme's year cannot change once it has audits (each audit's date must fall in its programme's year); saving an audit reads the programme row with a shared lock (`with_for_update(read=True)`) so a concurrent year change cannot slip in.
- `approve` refuses the `mcp` channel in the service; a CHECK constraint keeps a draft without approval data and an approved programme with both.
- Programmes share the `AUDITS` resource; `tests/test_mcp_modules.py` lists `auditorias` and `programas_auditoria` as sharing it.
- Test adaptations (standing approval): `tests/test_attribution.py`, `tests/test_mcp_http.py`, `tests/test_mcp_modules.py`, `tests/mcp_support.py`, `tests/test_migrations.py`.

- **Findings (AP-2):** a `COMPLETADA` audit still takes findings, only `CANCELADA` refuses them; a finding never moves to another audit; clauses match `4–10(.1–99){0,3}`; the automatic nonconformity has the area owner as responsible only when that person is active; a severity change follows the nonconformity only while it is `abierta`; deleting a nonconformity clears its finding's link with an audit row.
- The local test database container `iso-pg-test` had disappeared during AP-2; the writer recreated it (`postgres:17-alpine`, 127.0.0.1:55432, new random password). Reported to the user (2026-10-11).

## Next step

AP-3.
