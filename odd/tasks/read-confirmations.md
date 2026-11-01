# Read Confirmations (ISO 9001 clauses 5.2, 7.3)

Repository locator: `odd/tasks/read-confirmations.md` · Engram mirror: `odd/read-confirmations/tasks`

## Objective

Record that a person has confirmed reading the current policy or the quality
objectives, closing the 7.3 (toma de conciencia) gap and part of 5.2, including
a "Política de Calidad" document category.

## Problem

- `DocumentCategory` has no "Política de Calidad" value, and it has drifted from
  the migrated database enum (see below).
- Nothing records that a person has read/confirmed the policy, the objectives or
  a procedure; 7.3 stays "parcial" and 5.2 has no read-confirmation evidence.

## Scope (user-selected: "Completo")

1. Add `POLITICA_CALIDAD = 'Política de Calidad'` to `DocumentCategory`.
2. New `ConfirmacionLectura` register (person + date + target: a document
   revision or the quality objectives).
3. Reconcile the `documentcategory` Postgres enum with the model (latent bug).

Out of scope: publishing the policy to external interested parties (5.2 residual).

## Design decisions

- **DC-1** `DocumentCategory` gains `POLITICA_CALIDAD = 'Política de Calidad'`.
- **DC-2** Reconcile `documentcategory` native enum: add the seven members the model
  declares but the migration never added (`PLAN_ACCION_CORRECTIVA`,
  `PLAN_ACCION_PREVENTIVA`, `REGISTRO_CALIDAD`, `INFORME_REVISION`,
  `POLITICA_SEGURIDAD`, `INDICADOR_DESEMPENO`, `PLAN_CAPACITACION`) plus
  `POLITICA_CALIDAD`, via `ALTER TYPE documentcategory ADD VALUE IF NOT EXISTS`.
  No rename of the stale `PLAN_ACCION` value (harmless; `documents` is empty).
- **DC-3** `TipoConfirmacion` enum: `documento='Documento'`,
  `objetivos='Objetivos de calidad'`.
- **DC-4** `ConfirmacionLectura` (table `confirmaciones_lectura`):
  `persona_id` (FK personas, RESTRICT, required, indexed), `fecha_confirmacion`
  (Date, required, not future), `tipo` (text_enum `TipoConfirmacion`,
  `ck_confirmaciones_lectura_tipo`, required), `document_revision_id` (FK
  document_revisions, RESTRICT, nullable, indexed; required iff `tipo =
  documento`), `notas` (Text). CHECK `ck_confirmaciones_lectura_objeto`:
  `(tipo = 'documento' AND document_revision_id IS NOT NULL) OR (tipo =
  'objetivos' AND document_revision_id IS NULL)`.
- **DC-5** Service `app/services/read_confirmations.py`: crud.Spec + validation
  (person/revision exist, tipo↔revision consistency, not-future date); delete
  protected by RESTRICT FKs. Filters: persona_id, tipo, document_revision_id.
- **DC-6** Policy: `Resource.READ_CONFIRMATIONS = "read_confirmations"`,
  `_OPEN_REGISTER` (self-service confirmation, administrators delete).
- **DC-7** Web blueprint `confirmacion_lectura` (`/confirmaciones`): list/create/
  edit/show, person + tipo + document-revision pickers; the person page lists
  their confirmations.
- **DC-8** MCP module `confirmaciones_lectura`; docs (README, guía, análisis
  5.2/7.3, mcp.md).

## Constraints

- Follow repo conventions. CHECKs cross-DB (SQLite + PostgreSQL). Migration must
  keep `test_migrations_at_head_match_the_models` green. Artifacts in English,
  user docs in Spanish.

## Task checklist

- [x] **T1 — Model, enum and migration.** Category + enum + `ConfirmacionLectura` + CHECKs; one migration (enum ALTER + new table). Tests.
- [x] **T2 — Service and policy.** `read_confirmations.py` + `READ_CONFIRMATIONS` grant + person-page integration. Tests.
- [x] **T3 — Web.** routes/forms/templates for the register + person page. Tests.
- [x] **T4 — MCP and docs.** `confirmaciones_lectura` module + docs. Tests.

## Acceptance criteria

- A confirmation records who, when and what (a document revision or the
  objectives); a `documento` target requires a revision, `objetivos` forbids one.
- The current policy can be a controlled "Política de Calidad" document.
- Full suite passes; migration matches the models and the `documentcategory`
  enum gains the missing values.

## Verification

- `python -m unittest discover -s tests -p 'test_*.py' -v` green.

## Delivery

Work-unit commits on `feat/read-confirmations` (Conventional Commit):
- `47449dd` feat(qms): add read confirmation schema and quality policy category
- `af5bc7c` feat(qms): validate and audit read confirmations
- `3a75124` feat(qms): expose read confirmations through web and MCP
- `b6ecd9d` docs(odd): track read-confirmations

The document-revision FK name was shortened to 63 chars for PostgreSQL
(`fk_confirmaciones_lectura_document_revision_id`). The migration also adds the
seven missing `documentcategory` members plus `POLITICA_CALIDAD`. Verification:
1,847 tests, 1,752 passed, 0 failed, 95 skipped (local); PostgreSQL enum/migration
verification runs in CI. Single PR.

## Production deployment (2026-10-31)

`main@885012c` runs on `vulcano`. Authorized over SSH to `192.168.101.46` as `dcm`, no `sudo` by the agent.

| Step | Result |
|---|---|
| Preflight | Was `51efca2`, clean, migration `9a2c4e6f8b10`; four services active |
| Backups | `~/work/backups/calidad-2026-10-31-pre-885012c.dump` (542 entries) and `iso9001-code-2026-10-31-pre-885012c.tar.gz`, mode 600 |
| Code | Incremental git bundle `51efca2..main`, fast-forward to `885012c`; no dependency changes |
| Database | `flask db upgrade` `9a2c4e6f8b10` → `ab4c6e8f0a12` (documentcategory enum reconciled to 13 values); `flask db check` clean |
| Restart (user, with sudo) | All four services |
| Smoke tests | Services active, no journal errors; `/login` 200; `/confirmaciones/`, `/clientes/`, `/no_conformidades/`, `/procesos/` redirect to login; `/mcp` 401 Bearer; 33 MCP modules including `confirmaciones_lectura` |