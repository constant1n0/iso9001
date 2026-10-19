# External Providers

Repository locator: `odd/tasks/external-providers.md` · Engram mirror: `odd/external-providers/tasks`

## Objective

Control externally provided processes, products and services (ISO 9001:2026 clause 8.4): a register of providers with their selection criteria and approval state, periodic scored evaluations, nonconformities linked to the provider that caused them, and the providers' performance as an input to analysis (9.1.3 f) and to the management review (9.3.2).

## Problem and why it matters

- The only traces are the boolean `ProcesoOperacion.control_proveedor` and the nonconformity origin `proveedor`; there is no provider register, approval, evaluation or re-evaluation (`docs/analisis-brechas-iso-9001.md`, row 8.4), so 9.1.3 f (performance of external providers) cannot be analysed either.
- Gap analysis, Wave 2 item 3.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/external-providers` in worktree `iso9001-worktrees/user-management` from `main@5739c42` | User: "si" to the assistant's proposal (2026-10-18) |
| EP0 | Scope "Registro con evaluaciones puntuadas" | User choice (2026-10-18) |
| EP1 | A provider has a name (unique), tax id (NIF, unique when given), contact person, e-mail and phone, what it supplies (`producto`, `servicio`, `proceso_externalizado`) with a description, the affected processes (many-to-many), selection criteria, state (`pendiente`, `aprobado`, `condicionado`, `bloqueado`) with the date, the person who decided and a reason (required for `condicionado` and `bloqueado`), the next evaluation date and notes | User choice; fields by the assistant |
| EP2 | Approving, conditioning and blocking are ADMIN decisions and web-only (`mcp` never changes the state), like the other approvals; a new provider starts `pendiente` | Assistant design |
| EP3 | An evaluation has a date (not in the future), the evaluator (person), scores 1–5 for quality, delivery time, service and documentation, the average, a result from the average (`apto` ≥ 3.5, `condicionado` 2.5–3.49, `no_apto` < 2.5), a comment and the next evaluation date; an evaluation never changes the provider's state by itself (ADMIN decides), but the provider page and the dashboard show the latest result | User choice; bands and rule by the assistant |
| EP4 | A nonconformity whose origin is `proveedor` may name the provider (`proveedor_id`, nullable for existing records); naming a provider with another origin is refused; a provider's page counts its nonconformities | User choice |
| EP5 | Access: every role reads providers and evaluations (purchasing is often OPERATIVO); ADMIN and AUDITOR create and update and record evaluations; ADMIN deletes and decides the state; `mcp` never deletes or decides; a provider with evaluations or nonconformities cannot be deleted | Assistant design |
| EP6 | The dashboard flags overdue evaluations, blocked providers still linked to processes and approved providers whose latest result is `no_apto`; the management review inputs gain a providers section (by state, evaluations in the period with their average and results, nonconformities by provider) | User choice; contents by the assistant |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 2 `size:exception` (ask only above 1,500 lines); production deployment needs explicit authorization | User authorizations (2026-10-02, 2026-10-17) |
| Size EP-1 | One-off `size:exception` for the services pull request (about 2,110 lines) | User (2026-10-18) |
| Test adaptations | Standing Wave 2 approval: existing tests that only need adapting to an approved design may join a writer's surface, reported here and in the PR; application code outside the planned surface still needs approval | User (2026-10-17) |
| Docs | Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/mcp.md`, `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md`; `docs/architecture/services.md` in English | Documentation language decision (2026-10-16) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **EP-1 — Providers, evaluations and the nonconformity link (model and service).** Forecast 1,000-1,400.
  - Models, migration, policy resource, services with EP1–EP5 rules (state decisions, evaluation scores and result, nonconformity link), people and process delete protection, MCP modules.
- [ ] **EP-2 — Screens and dashboard.** Forecast 1,000-1,400.
  - Provider list with filters, detail with evaluations and nonconformities, forms, state decisions for ADMIN, evaluation form, the supplier picker on the nonconformity form, dashboard card.
- [ ] **EP-3 — PDF, review inputs and docs.** Forecast 500-900.
  - Approved-providers PDF, the management-review providers section (EP6), guides (Spanish first), `docs/mcp.md`, `docs/architecture/services.md`, gap analysis 8.4 and 9.1.3.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `5739c42`: 1,477 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`; run `docker start iso-pg-test` first).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| EP-1 | Done | `2e0a844` (models `proveedores`, `proveedor_procesos`, `evaluaciones_proveedor`, `NoConformidad.proveedor_id` with a CHECK tying it to the `proveedor` origin, migration `d2f4a6c8e1b3`, policy `EXTERNAL_PROVIDERS`, delete protection for deciders, evaluators and linked processes; `text_enum` gained a `length` parameter), `5bdd5a3` (`app/services/providers.py`: registers, ADMIN-only web decisions `approve`, `condition`, `block`, `may_decide`, evaluations with `puntuacion` and `resultado`, `latest_evaluation`, `average_score`, `performance`, `attention`, filters incl. `ultimo_resultado` in SQL; nonconformity service rule for `proveedor_id`; MCP modules `proveedores` and `evaluaciones_proveedor`, 26 modules) | RED: `cannot import name 'EstadoProveedor'`, `27 != 25`, `KeyError: 'EXTERNAL_PROVIDERS'`, `NoSuchTableError: proveedores`, no `proveedor_id`. GREEN: `2e0a844` alone 1,478 tests OK; both 1,528 tests OK incl. PostgreSQL | Pending |
| EP-2..EP-3 | Pending | — | — | — |

## Findings during implementation

- Choices: deleting a provider removes its process links (its delete audit row keeps `procesos`); no `reset_pending`, a wrong decision is corrected by another decision; `approve` takes an optional reason; an evaluation's `fecha_proxima` reschedules the provider only when it is the latest evaluation; an evaluation never moves to another provider.
- `text_enum` used a fixed length of 20, too short for `proceso_externalizado`; it now takes `length`.
- Looking up the decider after changing an audited row flushes it early and trips the audit guard, so the decider is looked up first.
- Test adaptations (standing approval): `tests/test_attribution.py` (25 → 27), `tests/test_policy.py`, `tests/test_access_characterization.py` (`NO_ROUTES`), `tests/test_mcp_modules.py`, `tests/test_mcp_http.py` (24 → 26), `tests/test_mcp_write_tools.py`, `tests/mcp_support.py`, `tests/test_migrations.py`.

## Next step

EP-2.
