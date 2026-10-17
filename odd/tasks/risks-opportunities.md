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
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; production deployment needs explicit authorization | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [ ] **RO-1 — Models and migration.** Forecast 600-900.
  - `Riesgo`, `Oportunidad`, `AccionRiesgo` with CHECK constraints (scales 1–5, exactly one parent, effectiveness only after completion); migration with the data move and its downgrade; people, process and interested-party delete protection; policy unchanged.
- [ ] **RO-2 — Services, MCP and review inputs.** Forecast 1,100-1,500.
  - Services with RO1–RO4 rules (level and priority, residual assessment, review-date overdue, action lifecycle and effectiveness), MCP modules replacing `riesgos_oportunidades`, management-review inputs on the new registers with old frozen data still rendering (RO7), removal of the old service and JSON endpoints.
- [ ] **RO-3 — Screens and dashboard.** Forecast 1,100-1,500.
  - Risk and opportunity lists with filters, details with their actions, forms, heat map (5 × 5, CSS only), dashboard card (high or critical risks without open actions, overdue reviews, records without assessment).
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
| RO-1..RO-4 | Pending | — | — | — |

## Next step

RO-1.
