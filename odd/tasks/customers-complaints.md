# Customers and Complaints (ISO 9001 clauses 8.2, 9.1.2)

Repository locator: `odd/tasks/customers-complaints.md` · Engram mirror: `odd/customers-complaints/tasks`

## Objective

Replace the free-text customer name with a `Cliente` register, add a first-class
`Reclamacion` register (complaints with a response to the customer and an optional
link to a nonconformity), and enrich `SatisfaccionCliente` with its customer, survey
type and channel. Closes the Phase-2 roadmap item #6 and the clause gaps listed under
8.2 and 9.1.2 in `docs/analisis-brechas-iso-9001.md`.

## Problem

- `SatisfaccionCliente.cliente` is free text (100 chars); the same customer appears
  with several spellings, so satisfaction cannot be grouped reliably (9.1.2).
- Complaints only exist as nonconformities with `origen = cliente`; there is no record
  of the complaint itself, the response given to the customer, or the closure.
- No customer master data; no survey type or channel (9.1.2 stays "partial").

## Scope (user-selected: "Núcleo")

In scope:

1. `Cliente` master register (replaces free-text customer).
2. `Reclamacion` register with state machine and optional `no_conformidad_id` link.
3. `SatisfaccionCliente` gains `cliente_id`, `tipo_encuesta`, `canal`.
4. Web screens, MCP modules and docs for the above.

Out of scope (not this feature):

- Review of customer requirements and communications (8.2.1–8.2.4 full register):
  deferred once `Cliente` exists.
- A dedicated satisfaction target register (9.1.2 "meta"): still expressed through
  the existing quality objectives.
- Calibration, design, production, release, communication plans (Phase 3).

## Design decisions

Recorded for the writer; see the delegated implementation prompt for the full spec.

- **DC-1** New entity `Cliente` (`clientes`): unique `nombre`, optional unique `nif`
  (mirror `Proveedor` NIF validation), `contacto`, `email`, `telefono`, `notas`.
- **DC-2** New entity `Reclamacion` (`reclamaciones`): `cliente_id` (FK, RESTRICT),
  `fecha_recepcion`, `descripcion`, `canal` (`CanalContacto`), `estado`
  (`EstadoReclamacion`), `respuesta`, `fecha_respuesta`, `fecha_cierre`,
  `motivo_cancelacion`, optional `no_conformidad_id` (FK, RESTRICT). State flow
  `recibida → respondida → cerrada`, plus `cancelada` and admin reopen, mirroring
  the `NoConformidad`/`qms_changes` pattern with CHECK backstops.
- **DC-3** `SatisfaccionCliente` gains `cliente_id` (FK, NOT NULL after backfill),
  `tipo_encuesta` (`TipoEncuesta`), `canal` (`CanalContacto`); drops the free-text
  `cliente` column. Migration backfills one `Cliente` per distinct existing name.
- **DC-4** Shared `CanalContacto` enum reused by reclamaciones and satisfaction.
- **DC-5** Policy: `Resource.CUSTOMERS` (`_ALL`/`_ADMIN_AUDITOR`/`_ADMIN`, like
  `EXTERNAL_PROVIDERS`) and `Resource.COMPLAINTS` (`_OPEN_REGISTER`, like
  `NONCONFORMITIES`).
- **DC-6** MCP modules `clientes` and `reclamaciones`; `satisfaccion_clientes` switches
  to `cliente_id` + `tipo_encuesta` + `canal`.

## Constraints

- Follow the repo conventions exactly: `RecordMetadataMixin` + append-only audit,
  `text_enum`, `person_link`, `crud.Spec` services, `Resource` grants and the
  `Module(...)` MCP registry. Cross-check the NIF/field validation against
  `app/services/providers.py`.
- State rules are enforced by the service and backed by DB CHECK constraints.
- Migration must be `upgrade`/`downgrade`-safe and backfill existing satisfaction rows.
- Artifacts (code, tests, commits, tracker) in English; user-facing docs
  (`README.md`, `docs/*.md`) in Spanish per existing convention.
- Never push or create a pull request; delivery is a human decision.

## Task checklist

- [x] **T1 — Models and migration.** `Cliente`, `Reclamacion` (+ enums) in
  `app/models.py`; enrich `SatisfaccionCliente`; one Alembic migration with the
  customer backfill. Tests for the new models/constraints.
- [x] **T2 — Services and policy.** `app/services/clientes.py`,
  `app/services/reclamaciones.py` (state machine + delete protection); update
  `app/services/satisfaction.py`; add `Resource.CUSTOMERS`/`COMPLAINTS`. Unit tests.
- [x] **T3 — Web screens.** `app/routes/cliente_routes.py`,
  `app/routes/reclamacion_routes.py`; update `satisfaccion_cliente_routes.py` +
  `forms.py` + templates + navigation + blueprint registration. Screen tests.
- [x] **T4 — MCP modules and docs.** `clientes`, `reclamaciones` modules and the
  `satisfaccion_clientes` update in `app/mcp_server/registry.py`; update
  `README.md`/`README.en.md`, `docs/analisis-brechas-iso-9001.md` (rows 8.2, 9.1.2,
  roadmap #6, cross-cutting reply), `docs/guia-de-usuario.md`. MCP tests.

## Acceptance criteria

- A customer can be created, listed, edited; deleting a referenced customer is refused
  with a clear message.
- A complaint follows `recibida → respondida → cerrada` (and cancel/reopen), records
  the response to the customer, and may link to one nonconformity.
- Every satisfaction survey references a customer and carries a survey type and channel;
  existing surveys migrated without data loss.
- New registers appear as MCP modules and on the web; the full test suite passes.

## Progress

- Route: **delegated direct** (one `gentle-ai-worker`); parent-owned edit surfaces were
  expanded four times to cover the existing survey/policy/MCP/attribution test fixtures
  the schema change legitimately required (all approved by the parent).
- Risk tier: **high** (schema change + data backfill migration + new FK/CHECK
  constraints); writer self-verification (suite green) **plus** an independent verifier
  (`gentle-ai-verify`), which caught a PostgreSQL defect I then fixed.
- Independent verification: 1820 tests, 1728 passed, 0 failed, 92 skipped. Verifier
  flagged `Reclamacion.customer`/`SatisfaccionCliente.customer` using `lazy='joined'`
  (LEFT OUTER JOIN) which PostgreSQL rejects under `with_for_update()`; fixed to
  `lazy='selectin'` (repo pattern), full suite re-run green.
- Outstanding (documented, non-blocking): `TEST_POSTGRES_URI` is unset, so the
  PostgreSQL test cases and live migration execution are not verified locally; the
  destructive downgrade (drops complaint/customer/survey-type data) is standard and
  documented.

## Verification

- `python -m unittest discover -s tests -p 'test_*.py' -v` green:
  **1820 tests, 1728 passed, 0 failed, 92 skipped** (venv:
  `/home/dcm/workspace/iso9001-worktrees/fixes-2026/venv/bin/python`).
- `git diff --check` clean; secret-scan pre-commit hook reported no leaks.

## Delivery

Work-unit commits on `feat/customers-complaints` (Conventional Commit):
- `75df05e` feat(models): add customers and complaints with survey backfill
- `ab39cb2` feat(services): manage customers and complaint lifecycle
- `6e8badd` feat(web): add customer and complaint screens
- `4a51fd5` feat(mcp): expose customers and complaints and document workflows

Authored change vs `main`: **50 files, ~2,082 lines** (1,950 code/docs/tests +
132-line tracker), which exceeds the Wave 2 1,500-line PR cap.

**Delivery plan (user-selected `chained`; strategy `stacked-to-main`, repo
precedent external-providers #143–147, change-planning #159–166):**

| PR | Base | Commits | Changed lines | Scope |
|---|---|---|---|---|
| #1 | main | `75df05e` + `ab39cb2` | 964 | models, migration backfill, services, policy + data-layer tests |
| #2 | main (after #1) | `6e8badd` + `4a51fd5` + `977ee65` | 1,118 | screens, forms, templates, nav, MCP modules, docs + their tests |

Each PR stays under the 1,500-line cap and is independently verifiable (PR #1 runs
the model/service tests; PR #2 builds on #1's merged state). Push and pull-request
creation remain the user's decision.