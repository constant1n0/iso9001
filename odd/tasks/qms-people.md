# People and Competence (qms-people)

Repository locator: `odd/tasks/qms-people.md` · Engram mirror: `odd/qms-people/tasks`

## Objective

Record the people who do work under the QMS, the roles they hold, the competence each role requires and the competence each person has demonstrated, and link people to trainings, nonconformities and audits instead of free-text names (ISO 9001:2026 clauses 5.3, 7.2 and 7.3).

## Problem and why it matters

- People are free text: `Capacitacion.personal`, `NoConformidad.responsable` and `Auditoria.auditor` are strings, so nobody can list what a person is responsible for or prove their competence.
- `RolResponsabilidad` lists roles but nobody is assigned to them (5.3).
- There is no record of required versus demonstrated competence, evidence, validity or effectiveness of actions taken (7.2); `Capacitacion.evaluacion_final` is free text.
- Gap analysis: row #7 "People as free text, no competence matrix" (`docs/iso-9001-gap-analysis.md`); decisions D4/D5 of `qms-foundations` deferred the `Person` entity to this change.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/qms-people` in worktree `iso9001-worktrees/user-management` from `main@69bcfa7` | User: "todas, elige tú el orden" and scope choice (2026-10-06) |
| Q0 | Scope "Personas + competencias": people (separate from login users, optionally linked to one), their roles, links from trainings, nonconformities and audits (legacy text kept), competence required per role, competence demonstrated per person (evidence as text or reference, date, expiry, effectiveness evaluation) and a required-versus-actual matrix. No file uploads | User choice (2026-10-06) |
| Q1 | Access: every role reads; ADMIN and AUDITOR create and update; only ADMIN deletes; the `mcp` channel never deletes (matrix D1) | User choice (2026-10-06) |
| Q2 | A person may hold several roles (`RolResponsabilidad`), unrelated to the application's login roles (`RoleEnum`) | Assistant design |
| Q3 | A person is deactivated rather than deleted once referenced; at most one person per user account | Assistant design |
| Q4 | Existing free-text names stay as they are; new and edited records pick a person, and the text field remains as the legacy value (no automatic matching) | Assistant design |
| Q5 | A demonstrated competence may cite a training as evidence; its effectiveness evaluation is `pendiente`, `eficaz` or `no eficaz` with date and evaluator (a person) | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; production deployment needs migrations and explicit authorization | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct** (two or more non-trivial files each).

- [x] **QP-1 — People and their roles.** Forecast 300-400.
  - `Person` (`personas`): full name, optional e-mail, optional unique link to a user, active flag, notes, record metadata; many-to-many with `RolResponsabilidad`; migration; `people` service (`crud`-style, policy resource `PEOPLE`); MCP module `personas`.
  - Acceptance: CRUD with policy Q1; duplicate user link refused; roles assigned and listed; a referenced person cannot be deleted (deactivate instead); migration upgrades and downgrades on PostgreSQL.
- [ ] **QP-2 — Links from trainings, nonconformities and audits.** Forecast 250-350.
  - Nullable `persona_id` on `capacitaciones`, `responsable_id` on `no_conformidades`, `auditor_id` on `auditorias`; services validate an existing active person; MCP fields; legacy text kept (Q4).
  - Acceptance: records accept and return the person; an unknown or inactive person is refused; old records keep their text; deleting a referenced person is refused with a real foreign key (not only a mocked flush), as the QP-1 review asked.
- [ ] **QP-3 — Competence requirements and records.** Forecast 300-400.
  - `CompetenceRequirement` per role (type: education, training, skill, experience; description); `CompetenceRecord` per person (requirement, evidence, optional training, obtained date, expiry, effectiveness evaluation Q5); services, policy resource `COMPETENCE`, MCP modules.
  - Acceptance: CRUD with policy Q1; expiry before obtained date refused; evaluation needs a date and an evaluator; the training link must exist.
- [ ] **QP-4 — Screens.** Forecast 350-450.
  - People list, create and edit with roles; a person's competence page; requirements per role; person pickers in the training, nonconformity and audit forms.
  - Acceptance: role-based access per Q1; forms keep input on errors; CSP-clean templates.
- [ ] **QP-5 — Competence matrix and docs.** Forecast 200-300.
  - Required-versus-actual matrix per role and person (met, expired, missing, pending evaluation); README and `docs/mcp.md` updates.
  - Acceptance: the matrix reflects expiry against today; access per Q1.

**Total forecast:** about 1,400-1,900 authored changed lines, so delivery is chained.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `69bcfa7`: 734 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| QP-1 | Done | `46588e6` (model, migration `a3c5e7f9b2d4`, service, policy `PEOPLE`), `a015c58` (MCP module `personas`, `rol_ids` in output, boolean filters), review follow-up in the next commit | RED: `ImportError` (`people`, `Person`), `KeyError: 'PEOPLE'`, `NoSuchTableError: personas`, `13 != 12`; MCP: `KeyError: 'rol_ids'`, `Filtros no admitidos: activo`; follow-up: an `IntegrityError` while auditing an update escaped. GREEN: 760 tests incl. PostgreSQL; `46588e6` alone passed 755 | Range `fd7ccfa..a015c58`: **medium**, `slice_budget_reached`; consent granted; reliability review `review-dd395cbc85c7341e` **approved** and acknowledged; its warning fixed (update now turns that race into `Conflict`); its suggestion (a real foreign-key delete test) moved into QP-2's acceptance |
| QP-2..QP-5 | Pending | — | — | — |

## Findings during implementation

- `AUDITED_MODELS` is built from every mapper, so a new model is audited automatically; `tests/test_attribution.py` pins the count (13 now).
- `Person.roles` has no backref: a backref would mark the roles as changed when a person's roles change, and the audit guard would fail.
- The audit snapshot only covers columns, so the people service adds `rol_ids` to every audit row itself.
- MCP output and filters: modules can name an optional extra-values function (used for `rol_ids`), and filters accept JSON booleans (`activo`).
- QP-2 references a person through `personas.id` with `ON DELETE RESTRICT`; SQLite tests do not enforce foreign keys, so `people.delete` also needs an explicit "still referenced" check.
- The edit surface was widened for QP-1 with the user's approval (2026-10-08): `tests/test_attribution.py`, `tests/test_mcp_http.py`, `app/mcp_server/operations.py`.

## Next step

QP-2.
