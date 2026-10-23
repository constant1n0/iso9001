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
| Surface OC-3 | `app/routes/scope_routes.py` and `tests/test_scope_screens.py` added so the scope screens ship as their own commit | User (2026-10-23) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **OC-1 — Context issues and interested-party fields (model and service).** Forecast 900-1,300.
  - Models and migration (issues, party fields, the issue link on risks and opportunities), services with OC1, OC2 and OC4 rules, delete protection, MCP modules and fields.
- [x] **OC-2 — QMS scope (model and service).** Forecast 800-1,200.
  - Versioned scope with the applicability catalogue, lifecycle and approval (OC3), MCP read and draft writes.
  - Also (OC-1 review): the interested-party web routes and JSON API pass `local_today()` to `stakeholders.create` and `update`, so a JSON client sending `fecha_revision` gets a validation result instead of a server error (test it through the JSON endpoint); `_current_issue` in `app/services/risks.py` reads the issue with a shared lock, like the module's other parent checks.
- [x] **OC-3 — Screens and dashboard.** Forecast 1,100-1,500.
  - Issue register screens, updated party screens, scope pages (current, draft, history, approve), dashboard card (OC5).
  - Also (OC-2 review): `update_draft` (and the readers) backfill catalogue codes missing from a draft's stored applicability instead of raising `KeyError`; test with a draft that lacks a code.
- [x] **OC-4 — PDF, review inputs and docs.** Forecast 600-900.
  - Scope PDF, the management-review context section (OC5), guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 4.1–4.3.
  - Also (OC-3 review): a web test that edits a risk whose context issue was superseded, keeping it, and saves.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `9edea71`: 1,554 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`; run `docker start iso-pg-test` first).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| OC-1 | Done | `08f1875` (model `CuestionContexto` with type-matched categories, relevance with a justification when not relevant, climate flag; party columns `tipo`, `pertinente`, `responsable_id`, `fecha_revision`, `requisitos_climaticos`; `cuestion_id` on risks and opportunities; migration `c8f2a4e6b0d3`; policy `ORGANIZATION_CONTEXT`; delete protection), `000fc16` (stakeholder service: the new fields, a new review date never before today; risks: `cuestion_id` validated (the issue must exist and be `vigente`) and filtered; MCP fields for parties (dated writes) and risks), `d65ef34` (`app/services/context_issues.py`: register, filters, `linked`, `attention`, `changes` for the review inputs; MCP module `cuestiones_contexto`, 27 modules) | RED: missing `TipoContexto` and `CategoriaCuestion`, `KeyError: 'ORGANIZATION_CONTEXT'`, unknown module, counts `28 != 27` and `27 != 26`, `NoSuchTableError: cuestiones_contexto`. GREEN: the first two commits alone 1,560 tests OK; all three 1,584 tests OK incl. PostgreSQL | Range `9edea71..b01fb7e`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-7e6230154396ddbd` **approved** and acknowledged; its warning and suggestion moved into OC-2 |
| OC-2 | Done | `3b9a1ec` (model `AlcanceSGC` with partial unique indexes for one draft and one current version, approval-data CHECK, plain table `aplicabilidad_alcance` with a justification CHECK; migration `e4a6c8b0d2f5`; approvers protected from deletion), `f5b1fc5` (OC-1 review: the interested-party web routes pass `local_today()`; `_current_issue` reads the issue `FOR SHARE`), `51a2f2d` (`app/services/qms_scope.py`: a 38-entry catalogue (4.1 to 10.3), `new_draft` (the first one needs a statement and applies everything; later ones copy the current version), `update_draft` with the applicability mapping, `approve` (ADMIN, not `mcp`, the approver is the user's linked person, approving one's own draft allowed), `delete_draft`, readers; MCP module `alcances_sgc`, 28 modules, approval and delete not exposed) | RED: `cannot import name 'AlcanceSGC'`, `NoSuchTableError: alcances_sgc`, no `local_today` in the party routes, no shared lock, counts `29 != 28` and `28 != 27`. GREEN: the first two commits alone 1,587 tests OK; all three 1,607 tests OK incl. PostgreSQL | Range `ba4a5d9..1975930`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-b7dde63afc6688a8` **approved** and acknowledged; its warning (partial indexes without `sqlite_where` in the migration) needs no change: migrations use PostgreSQL-only DDL and no other database is supported (`docs/operaciones.md`), as the document-revision migration already does; its suggestion moved into OC-3 |
| OC-3 | Done | `143e8d2` (`/contexto/` list with filters, detail with the linked risks and opportunities, forms with type-matched categories and the relevance justification; interested-party form fields, badges and a new party page; the context-issue picker on risk and opportunity forms with `cuestion_id` lists; dashboard card "Contexto" from `attention()`; navigation group "Contexto" 4.1 and 4.2), `5f23028` (`/alcance/` current version, history, version pages, draft form with one row per catalogue entry and row errors through `applicability_errors`, ADMIN-only approval; `qms_scope` readers and `update_draft` backfill missing catalogue codes (OC-2 review); navigation 4.3) | RED: 11 `no attribute 'context_routes'`, the missing-code draft raised `KeyError '8.3'`. GREEN: `143e8d2` alone 1,615 tests OK; both 1,623 tests OK incl. PostgreSQL; adapted `tests/test_access_characterization.py`, `tests/test_ui_permissions.py`, `tests/test_stakeholder_routes.py` (forms now re-render on refusals), `tests/test_qms_scope.py` | Range `953acb5..4fe0bfa`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-9a2336ac9b3539ca` **approved** and acknowledged; its warning (an edit keeping a superseded issue) is safe in code (`_current_issue` returns when the value is unchanged) and its end-to-end test moved into OC-4 |
| OC-4 | Done | `64f303b` (`/alcance/pdf` and `/alcance/<id>/pdf`: version, state, approval or obsolescence, counts, texts, the applicability table and the non-applicable requirements again; review inputs: `contexto` gains `cuestiones` (the seven counts and the new or changed issues) and `alcance` (the version in force when the inputs are drawn), rendered only when present so older frozen reviews still render; guides (Spanish first), `docs/mcp.md` (28 modules), `docs/architecture/services.md`; gap analysis: 4.1, 4.2 and 4.3 covered (16 covered, 11 partial, 6 not covered, 1 documentary only); the OC-3 review test) | RED: 5 `no attribute 'render_pdf'`, 9 missing PDF links, `KeyError: 'alcance'`, missing review figures and minutes labels; the superseded-issue edit test passed at once (the code was already safe). GREEN: 1,632 tests OK incl. PostgreSQL; adapted `tests/test_management_reviews.py`, `tests/test_access_characterization.py` | Range `94e8ec9..949f0a8`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-8592317a0e039240` **approved** and acknowledged; its suggestion (the review's issue list puts new issues before changed ones and then caps them) is accepted as the intended order and noted as a follow-up test |

## Findings during implementation

- Decisions in OC-1: `efecto` and `fecha_identificacion` are required (no default to today); party `tipo` and `pertinente` stay optional (`None` is "sin evaluar"); `superadas` in `changes` counts issues `superada` now with `updated_at` in the period (no supersession date is kept, so a later edit moves an issue to a later period).
- For OC-3: the web routes and the JSON API of interested parties call `stakeholders.create` and `update` without `today`; a write that sets `fecha_revision` without it raises `ValueError`, so OC-3 must pass `local_today()` in both (the MCP module already does through `_DatedWrites`).
- OC-2 findings: interested parties have no JSON API (`register_json_api` in `app/routes/json_register.py` is not wired to them), so only the web routes needed the date; `aplicabilidad_alcance` is a plain table because every mapped class must carry the record-metadata columns, and its contents travel in the version's audit rows; draft writes take no `today` (no draft rule uses a date); `tests/test_iso_edition_label.py` forbids the literal "ISO 9001:2015" under `app/`.
- OC-3: the party list drops the "Requisitos" and "Objetivo estratégico" columns (shown on the new party page); "Eliminar borrador" uses the update permission, as the service does; asking for a new draft while one exists opens it with the one-draft warning. A navigation entry calling `url_for` on an unregistered blueprint breaks every page, so navigation lands with its blueprint.
- The MCP `_DatedWrites` wrapper is a frozen dataclass: tests patch its wrapped `register`.
- Test adaptations (standing approval): `tests/test_attribution.py` (28), `tests/test_mcp_modules.py` and `tests/test_mcp_http.py` (27), `tests/test_policy.py`, `tests/test_access_characterization.py` (`NO_ROUTES`), `tests/mcp_support.py`, `tests/test_mcp_write_tools.py`, `tests/test_mcp_read_tools.py`, `tests/test_risks_service.py`, `tests/test_stakeholder_service.py`, `tests/test_migrations.py`.

## Production deployment (pending authorization)

Two migrations (`c8f2a4e6b0d3` context issues, party fields and the risk link; `e4a6c8b0d2f5` QMS scope) on top of `d2f4a6c8e1b3`: back up the database and the code, fast-forward, `flask db upgrade`, `flask db check`, then the user restarts the four services. Smoke tests: `/contexto/`, `/alcance/` and `/alcance/pdf` redirect to the login, 28 MCP modules.

## Next step

**Feature complete** once OC-4 merges. Next: the production deployment above, after explicit authorization. Follow-up: a test pinning the cap of the review's issue list (new issues first).
