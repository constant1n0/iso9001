# Improvement Register (ISO 9001 clauses 10.1, 10.3)

Repository locator: `odd/tasks/improvements.md` · Engram mirror: `odd/improvements/tasks`

## Objective

Turn the legacy free-text `Mejora` into a real improvement register: a description,
an owner, an origin linked to the record that gives rise to it, a lifecycle
(`propuesta → aprobada → implantada → eficacia_revisada`, plus `cancelada`) and a
real implementation date — unifying the improvement opportunities scattered across
audit findings and review actions (10.1) and linking to objectives (10.3).

## Problem

`Mejora` (table `mejoras`, empty in production) is a free-text list that requires
a `no_conformidad` text and stores `accion_correctiva`/`accion_preventiva`;
`fecha_implementacion` is auto-set to creation; no treatment flow, no origin link,
no objective/review-output link (10.1 and 10.3 stay "parcial").

## Scope (user-selected: "Completo")

Full lifecycle. Out of scope: migrating the other "opportunity" records into
`Mejora` (they are linked, not moved).

## Design decisions

- **DC-1** `OrigenMejora`: `no_conformidad='No conformidad'`,
  `auditoria='Auditoría'`, `revision_direccion='Revisión por la dirección'`,
  `oportunidad='Oportunidad'`, `objetivo='Objetivo de calidad'`, `otro='Otro'`.
- **DC-2** `EstadoMejora`: `propuesta`, `aprobada`, `implantada`,
  `eficacia_revisada`, `cancelada` (mirror 6.3 `EstadoCambio`).
- **DC-3** `EficaciaMejora`: `eficaz`, `no_eficaz`.
- **DC-4** `MEJORA_LINKS` (mirror `CHANGE_LINKS`): `no_conformidad_id`,
  `hallazgo_id` (`hallazgos_auditoria.id`), `accion_revision_id`,
  `oportunidad_id` (`oportunidades.id`), `objetivo_id` (`objetivos_calidad.id`) —
  each optional, RESTRICT, indexed, CHECK `col IS NULL OR origen = '<member>'`.
- **DC-5** `Mejora`: `descripcion` (Text, required), `responsable_id`
  (person_link), `origen` (required, default `otro`), `fecha_identificacion` (Date,
  required, not future), `beneficio` (Text), `estado` (default `propuesta`),
  `fecha_implementacion` (Date, nullable — set on implant), and the lifecycle
  columns mirroring 6.3 (`aprobado_por_id`, `fecha_aprobacion`, `eficacia`,
  `fecha_eficacia`, `revisor_eficacia_id`, `comentario_eficacia`,
  `motivo_cancelacion`, `fecha_cancelacion`) with cross-DB CHECKs. DROP the legacy
  `no_conformidad`, `accion_correctiva`, `accion_preventiva` (empty table).
- **DC-6** Service `app/services/improvements.py`: crud.Spec + `_check_link` +
  lifecycle transitions (mirror `qms_changes.py`/`nonconformities.py`); filters.
- **DC-7** Web + MCP `mejoras` + docs + tests (incl. migration test `no new table`,
  the improvements table already exists; `LATER_AUDITED_TABLES` unchanged).

## Constraints

- Follow repo conventions; CHECKs cross-DB; migration `down_revision = 'ab4c6e8f0a12'`;
  keep `test_migrations_at_head_match_the_models` green. Artifacts English, docs Spanish.

## Task checklist

- [x] **T1 — Model and migration.** Enums + links + restructured `Mejora` + migration. Tests.
- [x] **T2 — Service.** rewrite `improvements.py` + transitions + filters. Tests.
- [x] **T3 — Web.** form/routes/templates + origin/link pickers. Tests.
- [x] **T4 — MCP and docs.** `mejoras` module + docs. Tests.

## Acceptance criteria

- An improvement records description/owner/origin + one origin link when applicable,
  follows the full lifecycle with a real implementation date, and can reference a
  review action, audit finding, nonconformity, opportunity or objective.

## Verification

- `python -m unittest discover -s tests -p 'test_*.py' -v` green.

## Delivery

Work-unit commits on `feat/improvements` (Conventional Commit):
- `883949d` feat(improvements): define origin-linked lifecycle schema
- `ac92db1` feat(improvements): enforce audited transitions and reporting
- `ee22ef8` feat(improvements): expose register across web JSON and MCP
- `be053fe` docs(odd): track improvements

Audited-model count stays 34 (no new table; `mejoras` restructured in place).
`sql/deployment.sql` exempted (legacy, unused, documented as not matching models).
Verification: 1,872 tests, zero failures (local); PostgreSQL checks run in CI.
Single PR.