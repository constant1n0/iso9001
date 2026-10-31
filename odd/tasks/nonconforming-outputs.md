# Nonconforming Outputs (ISO 9001 clause 8.7)

Repository locator: `odd/tasks/nonconforming-outputs.md` · Engram mirror: `odd/nonconforming-outputs/tasks`

## Objective

Add the disposition of nonconforming outputs to `NoConformidad`: a `tratamiento`
enum, the concession record (deciding authority, date, justification) and the
`proceso_id` link to the process, closing the Phase-2 roadmap item #7 and the
cross-cutting "nonconformity → process" gap.

## Problem

`NoConformidad` records origin, severity, containment, root cause and corrective
actions, but (8.7, "parcial" in `docs/analisis-brechas-iso-9001.md`):

- no disposition (correction, segregation, return, concession, rework, scrap);
- no record of a concession or of the authority that decides it;
- no link to the process (or product).

## Scope (user-selected: "Completo")

In scope: `tratamiento` enum (6 values), the concession fields, the `proceso_id`
link and its display on the process page. Out of scope: product/lot identification
(no such register exists yet).

## Design decisions

- **DC-1** `TratamientoSalidaNoConforme` enum: `correccion='Corrección'`,
  `separacion='Separación'`, `devolucion='Devolución'`, `concesion='Concesión'`,
  `reproceso='Reproceso'`, `desecho='Desecho'`.
- **DC-2** `NoConformidad` gains `proceso_id` (FK `procesos_operacion.id_proceso`,
  RESTRICT, nullable, index `ix_no_conformidades_proceso_id`), `tratamiento`
  (text_enum → `ck_no_conformidades_tratamiento`, nullable), and the concession
  fields `concesion_autorizada_por_id` (person link, RESTRICT, nullable),
  `fecha_concesion` (date), `justificacion_concesion` (text).
- **DC-3** CHECK `ck_no_conformidades_concesion` (cross-DB, so SQLite tests also
  create it): `(tratamiento = 'concesion' AND concesion_autorizada_por_id IS NOT
  NULL AND fecha_concesion IS NOT NULL) OR ((tratamiento IS NULL OR tratamiento
  <> 'concesion') AND concesion_autorizada_por_id IS NULL AND fecha_concesion IS
  NULL)`.
- **DC-4** Service: add the five fields to `WRITABLE_FIELDS` and `_clean`; a
  friendly `_check_concesion` rule (mirror `_check_provider`); validate `proceso_id`
  against `process_operations`. `procesos` filter/list unchanged.
- **DC-5** Web: process picker, `tratamiento` enum and the concession fields on the
  nonconformity form; the detail page shows disposition + concession + process; the
  process page (`cited_by`) lists the nonconformities that cite it.
- **DC-6** MCP `no_conformidades` module exposes the five new fields; docs updated.

## Constraints

- Follow repo conventions (`text_enum`, `person_link`, RESTRICT FKs, `crud`/service
  style). The CHECK string must work on SQLite **and** PostgreSQL.
- No new tables: `tests/test_migrations.py` `LATER_AUDITED_TABLES` is untouched,
  but `test_migrations_at_head_match_the_models` must stay green.
- Artifacts in English; user docs in Spanish.

## Task checklist

- [ ] **T1 — Model and migration.** Enum + columns + FK/CHECK in `app/models.py`; one Alembic migration. Tests for the constraint.
- [ ] **T2 — Service.** `app/services/nonconformities.py` writable fields, `_clean`, `_check_concesion`, process reference; `cited_by` includes nonconformities. Tests.
- [ ] **T3 — Web.** form/routes/templates for the process picker, treatment and concession fields; detail + process pages. Tests.
- [ ] **T4 — MCP and docs.** registry `no_conformidades` fields; README/guia/analisis (row 8.7 → C, cross-cutting link). Tests.

## Acceptance criteria

- A nonconformity records a treatment and, when it is `concesion`, the deciding
  authority and date (with justification); a non-concession treatment nulls them.
- A nonconformity can link to one process; the process page lists its nonconformities.
- Full test suite passes; migration matches the models.

## Verification

- `python -m unittest discover -s tests -p 'test_*.py' -v` green (venv
  `/home/dcm/workspace/iso9001-worktrees/fixes-2026/venv/bin/python`).

## Delivery

- Route: **delegated direct** (one `gentle-ai-worker`). Forecast well under the
  1,500-line Wave 2 cap → single PR. Work-unit commits on `feat/nonconforming-outputs`.