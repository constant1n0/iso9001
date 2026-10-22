# Organization Context and QMS Scope

Repository locator: `odd/tasks/organization-context.md` · Engram mirror: `odd/organization-context/tasks`

## Objective

Record the organization's context (ISO 9001:2026 clause 4.1, including whether climate change is a relevant issue), the needs and expectations of interested parties (4.2) and the scope of the quality management system with the applicability of each requirement (4.3), as data an auditor can review, with screens, alerts, a scope PDF and the context changes as a management-review input.

## Problem and why it matters

- 4.1 and 4.3 are covered only by a controlled document; there is no register of internal and external issues, no record of the relevance of climate change, and the scope, applicability and justified exclusions are not data (`docs/analisis-brechas-iso-9001.md`, rows 4.1–4.3).
- `ParteInteresada` (`partes_interesadas`) has screens but only free text (needs and expectations, requirements, strategic objective): no type, relevance, owner, review date or climate-related requirements.
- The management review's input 9.3.2 b (changes in external and internal issues) has no register to draw from.
- Gap analysis, Wave 2 item 4.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/organization-context` in worktree `iso9001-worktrees/user-management` from `main@9edea71` | User: "continuemos" after the handoff naming this module next (2026-10-21) |
| OC0 | Scope "Contexto, partes y alcance completos" | User choice (2026-10-21) |
| OC1 | **Context issues** (`cuestiones_contexto`): type (`interna` / `externa`), category (external: `politica`, `economica`, `social`, `tecnologica`, `legal`, `ambiental`; internal: `cultura`, `recursos`, `conocimiento`, `desempeno`, `estructura`), description, effect (`positivo`, `negativo`, `neutro`), relevance (`pertinente` yes or no) with its justification, climate-change flag, owner (person), identification date, next review date, state (`vigente` / `superada`); risks and opportunities may name the issue that gives rise to them | User choice; category lists and the risk link by the assistant |
| OC2 | **Interested parties** gain a type (`interna` / `externa`), relevance (`pertinente` yes or no), an owner, a next review date and a climate-related-requirements flag; the existing texts stay | User choice |
| OC3 | **QMS scope** (`alcances_sgc`) is versioned data: version number, statement, products and services, sites, and one applicability entry per requirement of clauses 4–10 (from a fixed catalogue in code) with "aplica" or "no aplica" and a justification required when it does not apply; states `borrador` → `vigente` → `obsoleto`; only ADMIN approves (web-only); approving a draft makes the current version obsolete; current and obsolete versions never change; a new draft starts as a copy of the current one | User choice; lifecycle by the assistant (as document revisions) |
| OC4 | Access: every role reads; ADMIN and AUDITOR create and update context issues and scope drafts (new resource `ORGANIZATION_CONTEXT`); interested parties keep their current grant (`INTERESTED_PARTIES`: every role creates and updates); ADMIN deletes and approves the scope; `mcp` never deletes or approves | Existing grants; assistant design |
| OC5 | The dashboard flags issues and parties whose review is overdue; the management review gains the context changes for the period (9.3.2 b): issues new, changed or superseded in the period, relevant and climate-related counts; reviews held before still render | User choice; contents by the assistant |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 2 `size:exception` (ask only above 1,500 lines); production deployment needs explicit authorization | User authorizations (2026-10-02, 2026-10-17) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **OC-1 — Context issues and interested-party fields (model and service).** Forecast 900-1,300.
  - Models and migration (issues, party fields, the issue link on risks and opportunities), services with OC1, OC2 and OC4 rules, delete protection, MCP modules and fields.
- [ ] **OC-2 — QMS scope (model and service).** Forecast 800-1,200.
  - Versioned scope with the applicability catalogue, lifecycle and approval (OC3), MCP read and draft writes.
  - Also (OC-1 review): the interested-party web routes and JSON API pass `local_today()` to `stakeholders.create` and `update`, so a JSON client sending `fecha_revision` gets a validation result instead of a server error (test it through the JSON endpoint); `_current_issue` in `app/services/risks.py` reads the issue with a shared lock, like the module's other parent checks.
- [ ] **OC-3 — Screens and dashboard.** Forecast 1,100-1,500.
  - Issue register screens, updated party screens, scope pages (current, draft, history, approve), dashboard card (OC5).
- [ ] **OC-4 — PDF, review inputs and docs.** Forecast 600-900.
  - Scope PDF, the management-review context section (OC5), guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 4.1–4.3.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `9edea71`: 1,554 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`; run `docker start iso-pg-test` first).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| OC-1 | Done | `08f1875` (model `CuestionContexto` with type-matched categories, relevance with a justification when not relevant, climate flag; party columns `tipo`, `pertinente`, `responsable_id`, `fecha_revision`, `requisitos_climaticos`; `cuestion_id` on risks and opportunities; migration `c8f2a4e6b0d3`; policy `ORGANIZATION_CONTEXT`; delete protection), `000fc16` (stakeholder service: the new fields, a new review date never before today; risks: `cuestion_id` validated (the issue must exist and be `vigente`) and filtered; MCP fields for parties (dated writes) and risks), `d65ef34` (`app/services/context_issues.py`: register, filters, `linked`, `attention`, `changes` for the review inputs; MCP module `cuestiones_contexto`, 27 modules) | RED: missing `TipoContexto` and `CategoriaCuestion`, `KeyError: 'ORGANIZATION_CONTEXT'`, unknown module, counts `28 != 27` and `27 != 26`, `NoSuchTableError: cuestiones_contexto`. GREEN: the first two commits alone 1,560 tests OK; all three 1,584 tests OK incl. PostgreSQL | Range `9edea71..b01fb7e`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-7e6230154396ddbd` **approved** and acknowledged; its warning and suggestion moved into OC-2 |
| OC-2..OC-4 | Pending | — | — | — |

## Findings during implementation

- Decisions in OC-1: `efecto` and `fecha_identificacion` are required (no default to today); party `tipo` and `pertinente` stay optional (`None` is "sin evaluar"); `superadas` in `changes` counts issues `superada` now with `updated_at` in the period (no supersession date is kept, so a later edit moves an issue to a later period).
- For OC-3: the web routes and the JSON API of interested parties call `stakeholders.create` and `update` without `today`; a write that sets `fecha_revision` without it raises `ValueError`, so OC-3 must pass `local_today()` in both (the MCP module already does through `_DatedWrites`).
- The MCP `_DatedWrites` wrapper is a frozen dataclass: tests patch its wrapped `register`.
- Test adaptations (standing approval): `tests/test_attribution.py` (28), `tests/test_mcp_modules.py` and `tests/test_mcp_http.py` (27), `tests/test_policy.py`, `tests/test_access_characterization.py` (`NO_ROUTES`), `tests/mcp_support.py`, `tests/test_mcp_write_tools.py`, `tests/test_mcp_read_tools.py`, `tests/test_risks_service.py`, `tests/test_stakeholder_service.py`, `tests/test_migrations.py`.

## Next step

OC-2.
