# Service layer

Every write to a quality record goes through a framework-free service in
`app/services/`. The Flask routes are thin adapters; the MCP server
(`app/mcp_server/`, see [MCP server](../mcp.md), in Spanish) is another
adapter over the same functions, so both share one validation, one
authorization rule and one audit trail.

## Anatomy of a service call

```python
from app.extensions import db
from app.services import nonconformities
from app.services.actor import Actor

actor = Actor.from_user(user, channel="web")        # who, with which role, through which channel
record = nonconformities.update(db.session, actor, 7, {"gravedad": "mayor"})
db.session.commit()                                  # the adapter owns the transaction
```

Every function takes an explicit SQLAlchemy `Session` and an `Actor`. Services
never import Flask, Flask-Login or `app.routes` (a test enforces this with an
AST check), never read `request` or `current_user`, and never commit.

Inside a write, in this order:

1. **Policy.** `policy.require(actor, action, resource)` runs first and raises
   `PermissionDenied`. Nothing is loaded or changed for a refused actor.
2. **Load.** `session.get` / `select`; a missing record raises `NotFound`.
3. **Field whitelist.** The payload is a mapping restricted to the writable
   fields of the resource. Unknown keys (including `id` and the attribution
   columns) raise `ValidationError`. Validators live in `app/services/fields.py`
   (text, dates, ISO datetimes, enums, integers, booleans); blank optional text
   is stored as `NULL`.
4. **Mutate and stamp.** `attribution.stamp_created` / `stamp_updated` set
   `created_at/by` and `updated_at/by` from the actor (nullable columns; legacy
   rows stay `NULL`).
5. **Audit.** `audit.record(session, actor, "create" | "update" | "delete", ...)`
   adds one append-only `AuditLog` row. Updates store only the changed fields
   (`before` and `after`); deletes store a full snapshot of the removed row;
   password hashes and anything named like a token or secret are never recorded.
   An update that changes nothing writes nothing, not even an audit row.
6. **Flush.** Services call `session.flush()`, never `commit()` or `rollback()`.
   An `IntegrityError` becomes `Conflict` (the session then needs a rollback,
   which the adapter performs).

Reads call `policy.require(..., Action.READ, ...)` too. Paged listings are
bounded: the page is clamped to `1..crud.MAX_PAGE` and the size to
`1..crud.MAX_PER_PAGE` (100), so an adapter can pass client input through.

### Modules

| Module | Resource |
|---|---|
| `nonconformities.py` | Nonconformities (the reference pattern) and their states (see below) |
| `corrective_actions.py` | Corrective actions of a nonconformity and their verification (see below) |
| `audits.py`, `documents.py` | Audits, documents (bespoke) |
| `audit_programmes.py`, `audit_findings.py` | Yearly audit programmes and their approval; audit findings and their nonconformities (see below) |
| `document_revisions.py`, `document_files.py` | Document revisions and their workflow; attachment storage (see below) |
| `risks.py` | Risks, opportunities and their actions: assessment bands, residual assessment, overdue reviews and actions, effectiveness, the heat map and the dashboard's flags (see below) |
| `quality_objectives.py` | Quality objectives and their measurements, target checks and overdue measurements (see below) |
| `management_reviews.py` | Management reviews, their inputs drawn from the registers, holding and closing, and their actions (see below) |
| `providers.py` | External providers and their evaluations: state decisions, scores and results, the next-evaluation rule, performance and the dashboard's flags (see below) |
| `context_issues.py`, `qms_scope.py`, `stakeholders.py` | Context issues and their link to risks; the versioned QMS scope with its applicability; interested parties with their context fields (see below) |
| `qms_changes.py` | Planned changes to the QMS and their tasks: origin links, editing by state, the approval, implementation, effectiveness and cancellation transitions, the dashboard's flags and the period's changes for the management review (see below) |
| `people.py`, `competence.py` | People (each linked to at most one user and holding QMS roles), and competence required per role and demonstrated per person, with the required-versus-demonstrated matrix |
| `crud.py` | Generic helper driven by a `Spec` (model, resource, fields, ordering) |
| `training.py`, `satisfaction.py`, `improvements.py` | Plain HTML registers on `crud` |
| `process_operations.py`, `roles_responsibilities.py` | Processes with their related processes and the records citing them; roles with their reporting line and holders (see below) |
| `training_resources.py`, `audit_indicators.py` | Legacy registers on `crud`, with web screens and JSON endpoints |
| `users.py` | User accounts: administration and self-service (see below) |
| `api_tokens.py` | Bearer tokens for non-browser adapters (see below) |
| `policy.py`, `actor.py`, `errors.py`, `audit.py`, `attribution.py`, `fields.py` | The shared kernel |

## Actor and channels

`Actor(user_id, label, role, channel, scopes, token_prefix)` is frozen.
`token_prefix` is the public prefix of the API token behind an `mcp` actor
(never the secret), used for its rate limit and the security log; it is
`None` otherwise. `channel` is one of
`web`, `mcp`, `cli`, `system` and is stored on every audit row. `scopes` is
`None` for unrestricted web sessions or a set drawn from `{"read", "write"}`:
a scope only narrows the role, never widens it (a read-only token cannot
write even for an administrator, and a `write` scope does not let an
`OPERATIVO` user into a resource the role cannot reach).

## Permission matrix

`policy.can(actor, action, resource)` is pure. The channel is checked first
(`mcp` never deletes, never touches API tokens and never writes documents),
then the scopes, then the role.

| Resource | Read | Create / update | Delete |
|---|---|---|---|
| Nonconformities, corrective actions, improvements, surveys, training, stakeholders (`INTERESTED_PARTIES`) | all roles | all roles | ADMIN |
| Audits, audit programmes, audit findings | ADMIN, AUDITOR | ADMIN, AUDITOR (approve a programme: ADMIN) | ADMIN |
| Documents | all roles (drafts: ADMIN, AUDITOR) | ADMIN, AUDITOR, never `mcp` (approve and withdraw: ADMIN) | nobody (withdrawn instead) |
| Roles, training resources, process operations, audit indicators | all roles | ADMIN, AUDITOR | ADMIN |
| Risks, opportunities and their actions (`RISKS_OPPORTUNITIES`) | all roles | ADMIN, AUDITOR | ADMIN |
| People; competence requirements and records | all roles | ADMIN, AUDITOR | ADMIN |
| Quality objectives and their measurements | all roles | ADMIN, AUDITOR | ADMIN |
| Management reviews and their actions | ADMIN, AUDITOR | ADMIN, AUDITOR (hold and close: ADMIN, never `mcp`) | ADMIN |
| External providers and their evaluations (`EXTERNAL_PROVIDERS`) | all roles | ADMIN, AUDITOR (approve, condition and block: ADMIN, never `mcp`) | ADMIN |
| Context issues and QMS scope (`ORGANIZATION_CONTEXT`) | all roles | ADMIN, AUDITOR (approve the scope: ADMIN, never `mcp`) | context issues: ADMIN; scope drafts: ADMIN, AUDITOR as an update, never `mcp`; approved scope versions: nobody |
| Changes to the QMS and their tasks (`QMS_CHANGES`) | all roles | ADMIN, AUDITOR (approve and cancel: ADMIN; implement and review the effectiveness: ADMIN, AUDITOR; none on `mcp`) | ADMIN (a proposed change, with its tasks; tasks of an open change) |
| Users (web screens; no MCP module); audit log (policy only, no screen or tool) | ADMIN, AUDITOR | ADMIN | nobody |
| API tokens (list, issue, revoke) | ADMIN | ADMIN | nobody |

Roles are `ADMINISTRADOR`, `AUDITOR` and `OPERATIVO`. Hard deletes leave an
`AuditLog` snapshot of the removed record.

Self-service is the one deliberate exception to the matrix: changing your own
e-mail or password (`users.change_own_*`) and listing or revoking your own API
tokens (`api_tokens.list_own`, `revoke_own`) are open to every role, replacing
the grant with an ownership check on `actor.user_id` (see "Users" and "API
tokens" below).

## Nonconformity states and corrective actions

Decisions N2-N5 of `nc-capa-loop` (ISO 9001 clause 10.2). `estado` is never
part of a write payload; only these functions move it, all through
`nonconformities._transition` (snapshot, state, stamp, audit, flush):

- `nonconformities.cancel(session, actor, nc_id, motivo, *, today)`:
  administrators and auditors cancel an open nonconformity; the reason is
  required and stored trimmed.
- `nonconformities.close(session, actor, nc_id, *, today)`: administrators and
  auditors close an open nonconformity once `close_blockers(actions)` is
  empty: at least one action, every action done and verified, and the latest
  one verified `eficaz`. Otherwise the `ValidationError` lists what is
  missing.
- `nonconformities.reopen(session, actor, nc_id)`: administrators reopen a
  closed or cancelled nonconformity; it lands on `expected_state(actions)`
  and loses `fecha_cierre` and `motivo_cancelacion`.
- `today` is the closing date to record. The adapter passes its local date
  (`audit_notifications.local_today()`, in `APP_TIMEZONE`); a terminal state
  without it is a programming error, never a server-clock fallback.
- The role checks run after `policy.require(UPDATE, NONCONFORMITIES)`, because
  the policy matrix only knows resources and actions. `may_cancel`,
  `may_close` and `may_reopen` answer the same question for the screens.
- While open, a nonconformity follows its actions: `expected_state` is
  `abierta` without actions, `accion_planificada` while an action is not done
  or the latest one was verified `no_eficaz`, and `en_verificacion` otherwise.
  `corrective_actions` calls `nonconformities.sync_state` after every write.
- A `cerrada` or `cancelada` nonconformity refuses `update` and every write of
  its actions. `update`, `cancel`, `close`, `reopen` and every action write
  take the nonconformity's row lock first (`nonconformities.lock`, `SELECT …
  FOR UPDATE`), so concurrent writes serialize on PostgreSQL.
- `list_` and `list_page` filter by `descripcion`, `estado`, `origen`,
  `gravedad` (enum members or their names; anything else is a Spanish
  `ValidationError`) and `fecha_detectada`.

`corrective_actions.verify(session, actor, action_id, *, resultado, fecha,
verificador_id, evidencia)` records the effectiveness check of a done action:
administrators and auditors only (`VERIFY_ROLES`, checked after
`policy.require(UPDATE, CORRECTIVE_ACTIONS)`); the result is `eficaz` or
`no_eficaz` (member or name), the date is not before the done date, the
verifier is an active person other than the action's owner, and the evidence
is required. A verified action is read-only (`update` refuses it), though an
administrator may still delete it while the nonconformity is open. The MCP
adapter exposes neither `verify` nor the state functions: they are web-only.

## Audit programmes and findings

Decisions AP1-AP7 of `audit-programme` (ISO 9001 clause 9.2). Programmes,
audits and findings share the `AUDITS` resource.

- `audit_programmes.approve(session, actor, programme_id, *, aprobado_por_id,
  today)` moves a `borrador` to `aprobado`. It is for ADMIN only and refused on
  the `mcp` channel (`PermissionDenied`, checked after the grant).
  - It locks the programme's row (`lock`, `SELECT … FOR UPDATE`) and requires
    an active approving person.
  - `today` is a `date` from the adapter (`local_today()`); a `datetime` is a
    `ValueError`.
  - A lost race is a `Conflict` with `APPROVE_CONFLICT`.
  - A CHECK constraint keeps a draft without approval data and an approved
    programme with both.
- `audit_programmes.update` never changes the year of an approved programme,
  nor of a draft that has audits; `delete` refuses a programme with audits.
  `audits` reads the programme's year with a shared lock (`FOR SHARE`) when an
  audit joins a programme or changes date, and checks independence: neither
  the lead auditor nor a team member may own the audited area.
- `audit_findings.create` and `update` keep a finding on its audit and refuse
  writes while the audit is `CANCELADA`. They read the audit row with a shared
  lock.
  - A `nc_mayor` or `nc_menor` finding creates its nonconformity in the same
    transaction through `nonconformities.create`: origin `auditoria`, the
    severity of the type, the audit's date, and the audited-area owner as
    responsible when active.
  - The finding keeps the link (`no_conformidad_id`, never written by callers).
    A finding of a nonconformity type without a link gets a new
    nonconformity.
  - While the finding is a nonconformity, its severity follows the type
    (`_follow_severity`) only while the nonconformity is `abierta`; otherwise
    the change is refused.
- Deletes: `audit_findings.delete` (ADMIN) never deletes the nonconformity,
  and `nonconformities.delete` clears its finding's link with an audit row.
  `audits.delete` refuses an audit with findings (`HAS_FINDINGS`). When the
  database refuses the delete instead, the message names findings only if the
  violated constraint is `FINDINGS_FK`, a name read from `HallazgoAuditoria`'s
  foreign key to `auditorias`; otherwise it is `DELETE_CONFLICT`.
- The MCP adapter exposes the three modules without `approve` and without
  deletes. The web adapter adds the audit and programme PDFs.

## Risks and opportunities

Decisions RO1-RO4 and RO6 of `risks-opportunities` (ISO 9001:2026 clause 6.1).
`risks` offers three registers on `crud` with the `Register` shape of
`quality_objectives`: `risks` (`Riesgo`), `opportunities` (`Oportunidad`) and
`actions` (`AccionRiesgo`), which share the `RISKS_OPPORTUNITIES` resource.
`create` and `update` take `today`, a `date` from the adapter
(`local_today()`), and so do the lists whose `revision_vencida` or `vencida`
filter is used.

- **Records**: choices are member names. An owner (`responsable_id`) is
  required on create; rows migrated from the old `riesgos_oportunidades` table
  may lack one until someone sets it. `proceso_id` and `parte_interesada_id`
  cite an existing process and interested party; `cuestion_id` cites the
  context issue the record arises from (see "Organization context"). `fecha_identificacion`
  defaults to `today` and is never after it (`IDENTIFIED_IN_FUTURE`);
  `fecha_revision`, the next review, is never before it
  (`REVIEW_BEFORE_IDENTIFICATION`).
- **Assessment**: each 1-5 pair (likelihood and impact, the residual pair,
  benefit and feasibility) is given whole or left empty (`HALF_ASSESSMENT`); a
  pending pair is "sin valorar" (RO4). A risk's residual pair needs its first
  pair (`RESIDUAL_WITHOUT_ASSESSMENT`).
  - `nivel` and `nivel_residual` (`models.nivel`): `bajo` 1-4, `medio` 5-9,
    `alto` 10-16, `critico` 20-25; `prioridad` (`models.prioridad`): `baja`
    1-6, `media` 8-12, `alta` 15-25.
  - The `nivel` and `prioridad` filters run in SQL on the same bands as
    product ranges (`LEVEL_RANGES`, `PRIORITY_RANGES`).
- **Open and overdue**: a record is open (`is_open`) unless it is a `cerrado`
  risk or an `aprovechada` or `descartada` opportunity. Only an open record is
  overdue for review (`revision_vencida`: `fecha_revision` before `today`).
- **Delete**: a record with actions is refused (`RISK_HAS_ACTIONS`,
  `OPPORTUNITY_HAS_ACTIONS`). The delete locks the row (`FOR UPDATE`); when an
  action inserted meanwhile makes the database refuse it, the message names
  the actions only if the violated constraint is the actions' foreign key to
  that register, and is `DELETE_CONFLICT` otherwise.
- **Actions** belong to exactly one risk or opportunity (`ONE_PARENT`), which
  must exist and be open (`RISK_CLOSED`, `OPPORTUNITY_CLOSED`) and is read with
  a shared lock (`FOR SHARE`); an action never moves (`PARENT_FIXED`).
  - A `completada` action, and only one, has `fecha_completada`, never after
    `today` (`COMPLETION_REQUIRED`, `COMPLETION_ONLY_WHEN_DONE`,
    `COMPLETION_IN_FUTURE`).
  - Its effectiveness (6.1.2 b: `eficacia` with `fecha_evaluacion`, given
    together, `EVALUATION_PAIR`) is evaluated only once it is completed
    (`EVALUATION_NOT_DONE`), on or after the completion and never after
    `today`.
  - An action is overdue (`is_overdue`, the `vencida` filter) when pending or
    in progress after its deadline.
- Every rule judges the record as the write would leave it. An unknown key is
  refused before anything is looked up, and an edit that changes nothing
  writes nothing.
- `attention(session, actor, *, today)` gathers the dashboard's flags among
  open records: high or critical risks without a pending or in-progress
  action, overdue reviews of both registers and unassessed records of both.
  `heat_map(session, actor)` counts the open assessed risks by (likelihood,
  impact) cell. The screens also filter by `abierto`, risks by one cell
  (`probabilidad`, `impacto`) and by `alto_sin_accion`, whose condition is
  never NULL (an unassessed risk is plainly not one), so its negation keeps
  the rest.
- Adapters: the web screens with the heat map and the register PDFs
  (`/riesgos/pdf`, `/oportunidades/pdf`), the dashboard card, the MCP modules
  `riesgos`, `oportunidades` and `acciones_riesgo` (no deletes), and the
  management review inputs, which count risks by level and opportunities by
  priority (reviews held before keep their frozen `riesgos_oportunidades`
  keys).

## Processes and roles

Decisions PR1-PR3 and PR5 of `process-role-registers` (ISO 9001:2026 clauses
4.4, 5.3 and 8.1).

- **Processes** (`process_operations`, `ProcesoOperacion`): a unique name
  (`Conflict` on a duplicate), a type (`TipoProceso` by member name; blank
  means none), an owner (`responsable_id`, checked by
  `people.check_reference`; a person owning a process cannot be deleted),
  inputs, outputs, resources, operating criteria (`criterio_calidad`),
  acceptance criteria, and the legacy supplier flag and nonconformity text.
  - `relacionados` replaces the related processes as a set: every id must
    exist and none may be the process itself (`RELATED_ITSELF`). The relation
    is mutual: each pair is stored once in `proceso_relaciones`, lower id
    first (a CHECK keeps the order), and `related_ids` and `related` read it
    both ways. The table has no audit model, so the process's create and
    update rows carry `relacionados`.
  - `list_` and `list_page` filter by `tipo` and `responsable_id`. `cited_by`
    gathers the quality objectives, risks and opportunities citing a process,
    and `counts` counts them and the related processes per process in grouped
    queries, for the list.
  - Delete is refused while the process is related (`HAS_RELATIONS`) or cited
    by objectives (`CITED_BY_OBJECTIVES`) or by risks or opportunities
    (`CITED_BY_RISKS`); the `ON DELETE RESTRICT` foreign keys refuse a
    relation or citation added after the check.
- **Roles** (`roles_responsibilities`, `RolResponsabilidad`): a unique name,
  responsibilities, authorities, the quality commitment flag and policy text,
  and the role it reports to (`reporta_a_id`).
  - A role never reports to itself (`REPORTS_TO_ITSELF`, also a CHECK) nor to
    a role below it in its line (`REPORTING_CYCLE`): the write walks the line
    upwards. The walk takes no locks, so two concurrent updates could still
    close a cycle; the role page stops its walk at a repeated role.
  - `holders` returns the people holding a role (inactive ones included) and
    its competence requirements.
  - Delete is refused while competence requirements cite the role (`IN_USE`)
    or other roles report to it (`HAS_SUBORDINATES`); the people holding a
    deleted role simply lose it.
- Writes flush and never commit, and an update that changes nothing writes
  nothing.
- Adapters: the web screens `/procesos/` and `/roles/`
  (`routes/process_role_routes.py`); the JSON endpoints kept for
  compatibility, `/proceso_operacion/` and `/rol_responsabilidad/`, whose
  schemas (`app/schemas.py`) also dump `responsable_id`, `relacionados` and
  `reporta_a_id` and whose writes go through these services; and the MCP
  modules `procesos` and `roles_responsabilidades` (no deletes). The legacy
  training-resource and audit-indicator registers have their screens in
  `routes/register_routes.py` and keep their JSON endpoints too.

## Quality objectives

Decisions QO1-QO4 of `quality-objectives` (ISO 9001:2026 clause 6.2).
`quality_objectives` offers two registers on `crud`, `objectives` and
`measurements`, which share the `QUALITY_OBJECTIVES` resource.

- **Measurements only while active**: `measurements.create`, `update` and
  `delete` read the objective with a shared lock (`FOR SHARE`) and refuse
  unless it is `activo` (`OBJECTIVE_NOT_ACTIVE`).
  - An update that changes nothing writes nothing, even on a closed
    objective: there is nothing to refuse.
  - A measurement never moves to another objective (`OBJECTIVE_FIXED`).
  - There is one measurement per objective and date (`DUPLICATE_DATE`).
  - Its date lies in the objective's period and never after `today`, a
    `date` from the adapter (`local_today()`); a `datetime` is a
    `ValueError`.
- **Locked target**: once an objective has measurements, `objectives.update`
  refuses a change of `meta` or `sentido` (`TARGET_FIXED`) and `delete`
  refuses the objective (`HAS_MEASUREMENTS`).
  - The update locks the objective's row (`FOR UPDATE`). The deadline is
    never before the start, and the period always keeps the measurements
    inside it.
  - When the database refuses the delete instead, the message names
    measurements only if the violated constraint is `MEASUREMENTS_FK`;
    otherwise it is `DELETE_CONFLICT`.
- **Finite numbers**: `meta`, `valor_inicial` and `valor` (`number`) take
  numbers or decimal text and round them half up to four decimals
  (`Numeric(12, 4)`).
  - Booleans, values of more than eight integer digits and non-finite values
    (`NaN`, `Infinity`; `NOT_FINITE`) are refused.
  - A measurement write refuses unknown keys before anything is looked up.
- **Overdue rule** (`summary(objective, measurements, *, today)`, pure):
  - the latest value and date, whether it meets the target (`meets`: at or
    above for `mayor_igual`, at or below for `menor_igual`) and the trend
    from the measurement before;
  - `proxima_medicion`: one frequency period after the latest measurement,
    or after `fecha_inicio` without one, capped at `fecha_limite`; `None`
    when the objective is not `activo` or was measured on its deadline;
  - `medicion_vencida`: `today` is after `proxima_medicion`.
- `measurements.by_objective` reads the measurements of several objectives
  in one query. `off_track(session, actor, *, today)` lists the active
  objectives that miss their target or have an overdue measurement, soonest
  deadline first, for the dashboard card.
- Adapters: the web screens, the dashboard card and the yearly PDF
  (`/objetivos-calidad/pdf`); the MCP modules `objetivos_calidad` and
  `mediciones_objetivo` (no deletes); and the monthly report, which judges the
  active objectives with `summary` on the month's last day, or on today when
  earlier.

## Management reviews

Decisions MR1-MR6 of `management-review` (ISO 9001 clause 9.3).
`management_reviews` offers two registers, `reviews` (bespoke writes) and
`actions` (on `crud`), which share the `MANAGEMENT_REVIEW` resource.

- **MR1, the review**: `fecha` (the meeting), `periodo_desde` and
  `periodo_hasta` are required, and the period starts on or before its end,
  which is on or before the meeting (`PERIOD_ORDER`, `PERIOD_AFTER_MEETING`;
  a CHECK constraint repeats it).
  - `asistente_ids` replaces the attendees: known people, active when newly
    chosen. The association table has no audit row of its own, so every audit
    row of a review carries `asistente_ids`.
  - One free-text comment per input (`COMMENT_FIELDS`) and `conclusiones`.
  - `create` and `update` never write `estado`, `datos_entrada`,
    `celebrada_por_id` or `fecha_cierre`. A draft takes every field, a held
    review only the comments and conclusions (`ONLY_COMMENTS`), a closed one
    nothing (`REVIEW_CLOSED`). An edit that changes nothing writes nothing.
- **MR2, the inputs**: `inputs(session, actor, desde, hasta, *, today,
  review=None)` draws the inputs of 9.3.2 from the registers as JSON-safe
  data, every list capped at `LIST_LIMIT` items.
  - `fecha_corte` is `hasta`, or `today` when earlier; each section counts
    the period up to it, and timestamps count by UTC day.
  - Earlier reviews' actions come from every review dated before this one,
    drafts included. Objectives overlapping the period, except cancelled
    ones, are judged with `quality_objectives.summary` on the cut-off.
  - The `proveedores` section (9.1.3 f, EP-3 of `external-providers`) counts
    the providers by their current state (no history is kept), those overdue
    for evaluation on the cut-off (`providers.overdue`), the evaluations dated
    in the period with their pooled mean (`providers.average_of`) and results,
    the providers rated `no_apto` in the period, and the nonconformities with
    the `proveedor` origin detected in the period, the most cited providers
    first. Reviews held before EP-3 froze no such section, and the screens
    (`input_sections`) show it only when it is there.
  - The `contexto` section (9.3.2 b) gains `cuestiones` (OC5 of
    `organization-context`, from `context_issues.changes`): the issues
    created, or else updated, in the period, those superseded in it, and the
    counts of the issues in force now (relevant, climate-related, both); and
    `alcance`, the version of the QMS scope in force and its approval date
    (`None` without one). Reviews held before OC-4 froze neither, and
    `input_sections` shows each only when it is there.
  - The `cambios` section (9.3.2 c, CP6 of `change-planning`, from
    `qms_changes.in_period` with the review's cut-off) counts the changes
    proposed, approved, implemented, reviewed (effective and not effective)
    and cancelled in the period by the date of each stage; it lists the
    changes approved and late at the cut-off (the most overdue first) and
    those implemented or reviewed in the period (the latest stage first),
    each list next to its full count so the screens can say when it was cut
    (`input_sections`, a block's `nota`). Reviews held before CP-4 froze no
    such section, and `input_sections` shows it only when it is there.
  - `preview` (read grant) and `preview_of` (no check, for adapters that
    already read the review) give the frozen inputs of a held review and the
    live ones of a draft. "No data" is SQL NULL, so the CHECK that a draft
    holds no frozen inputs (and a held review does) fires.
- **MR3, the actions**: `tipo` (`mejora`, `cambio_sgc`, `recursos`),
  `descripcion`, `responsable_id` (a person, active when newly chosen) and
  `fecha_limite` are required; `estado` starts `pendiente`.
  - Actions are added, edited and deleted while their review is a draft or
    held. Once it is closed only `estado`, `fecha_completada` and `resultado`
    change (`FOLLOW_UP_ONLY`) and none is added or deleted
    (`REVIEW_CLOSED_FOR_ACTIONS`). An action never moves to another review
    (`REVIEW_FIXED`).
  - `fecha_completada` is given exactly when the action is `completada`
    (a CHECK constraint repeats it), never after `today` nor before the
    review's date. Moving an action away from `completada` without clearing
    the date is refused, not cleared silently; moving the review's date past
    a completed action is refused too (`ACTIONS_COMPLETED_BEFORE`).
  - `is_overdue(action, *, today)`: `pendiente` or `en_curso` past
    `fecha_limite`. The same rule drives the `vencida` filter,
    `overdue_actions` (the dashboard card) and `action_counts` (the list).
- **MR4, holding and closing**: `hold(session, actor, review_id, *, today)`
  and `close(…)` are the only moves, `borrador` → `celebrada` → `cerrada`,
  with no reopening. Both need an administrator (`TRANSITION_ROLES`) and are
  refused on the `mcp` channel.
  - Holding needs a meeting not after `today` and an attendee; it freezes the
    inputs into `datos_entrada` and records the actor's person, if any, as
    `celebrada_por_id`.
  - Closing needs the conclusions and records `fecha_cierre` (`today`).
- **MR5, access**: ADMIN and AUDITOR read and write reviews and actions,
  ADMIN deletes, `OPERATIVO` has no access, and `mcp` never deletes, holds or
  closes.
- **MR6, deleting**: only a draft without actions is deleted (`HAS_ACTIONS`,
  `DELETE_NOT_DRAFT`); an action added after the check meets the foreign key
  at the flush, reported as `HAS_ACTIONS` when the violated constraint is
  `ACTIONS_FK`. People named by a review or an action are not deleted.
- **Locks**: a review row is locked before it changes (`FOR UPDATE`) and
  before its actions do (`FOR SHARE`), so its state holds during the write.
- Adapters: the web screens, the action register and the dashboard card, the
  minutes PDF (`/revisiones-direccion/<id>/pdf`, whose actions name the
  changes to the QMS they give rise to); the MCP modules
  `revisiones_direccion` and `acciones_revision` (no deletes, holds or
  closes).

## External providers

Decisions EP1-EP6 of `external-providers` (ISO 9001 clause 8.4).
`providers` offers two registers, `providers` (`Proveedor`) and `evaluations`
(`EvaluacionProveedor`), which share the `EXTERNAL_PROVIDERS` resource.
Services never read the clock: adapters pass `today`.

- **EP1, the provider**: `nombre` is unique (`NAME_TAKEN`) and `nif` unique
  when given (`NIF_TAKEN`), stored upper case without spaces or hyphens, 8 to
  10 letters or digits (`BAD_NIF`). `tipo_suministro` is `producto`,
  `servicio` or `proceso_externalizado`. `procesos` is a list of process ids
  that replaces the links (`proveedor_procesos`); that table has no audit row
  of its own, so every audit row of a provider carries `procesos`.
- **EP2, the state**: a new provider is `pendiente`. `create` and `update`
  never write `estado`, `fecha_estado`, `decidido_por_id` or
  `motivo_estado`. `approve` (optional reason), `condition` and `block`
  (reason required, `REASON_REQUIRED`) record `today`, the acting user's
  person (`people.of_user`, `None` without one) and the reason. They need an
  administrator (`may_decide`) and are refused on the `mcp` channel. A
  decision that keeps the state is refused (`ALREADY_IN_STATE`) and writes
  nothing. There is no way back to `pendiente`: a decision is corrected by
  another. A CHECK constraint (`ck_proveedores_decision`) ties the decision
  columns to the state.
- **EP3, evaluations**: `calidad`, `plazo`, `servicio` and `documentacion`
  score 1 to 5. `puntuacion` is their mean (two decimals, half up) and
  `resultado` its band: `apto` from 3.5, `condicionado` from 2.5, `no_apto`
  below; both are computed, never stored. `fecha` is never after `today`
  (`EVALUATED_IN_FUTURE`), `fecha_proxima` comes after it (`NEXT_NOT_AFTER`),
  and the evaluator is a person, active when newly chosen. An evaluation never
  moves to another provider (`PROVIDER_FIXED`) and never changes its state.
  - The latest evaluation is the one with the latest `fecha`, then the highest
    id; `latest_evaluation`, `performances` and the `ultimo_resultado` filter
    (in SQL) agree on it.
  - The next evaluation: recording the provider's latest evaluation with
    `fecha_proxima` sets the provider's `fecha_proxima_evaluacion` (an older
    one recorded later does not). Afterwards the provider follows the latest
    evaluation that has a `fecha_proxima`: when an update or a delete changes
    that date, the provider takes the new one, and keeps its own when none
    remains. A write that leaves that date alone keeps a date edited on the
    provider.
  - `evaluacion_vencida` (and the SQL `overdue`): not `bloqueado`, with
    `fecha_proxima_evaluacion` before `today`.
- **EP4, nonconformities**: a nonconformity whose origin is `proveedor` may
  name the provider (`proveedor_id`, nullable); naming one with another origin
  is refused, and a CHECK constraint repeats it.
- **EP5, access**: every role reads; ADMIN and AUDITOR create and update
  providers and record and edit evaluations; ADMIN deletes and decides; `mcp`
  never deletes or decides. A provider with evaluations (`HAS_EVALUATIONS`) or
  nonconformities (`HAS_NONCONFORMITIES`) is never deleted; deleting one
  removes its process links, which its delete audit row keeps. People who
  decided a state or evaluated, and processes linked to a provider, are not
  deleted.
- **EP6, performance and attention**: `performance` and `performances` (two
  queries for a list) give the number of evaluations, the mean of all of them
  (`average_of`, pooled), the latest score, result and date, and the open and
  total nonconformities. `attention` flags, for the dashboard, the overdue
  evaluations, the blocked providers still linked to processes and the
  approved providers whose latest result is `no_apto`. The management review
  inputs gain the `proveedores` section (see "Management reviews").
- **Locks**: a provider row is locked (`FOR UPDATE`) before a decision, an
  update, a delete and any evaluation write that may reschedule it.
- Adapters: the web screens (`/proveedores/`), the decisions, the evaluation
  forms, the provider picker on nonconformities, the dashboard card, the
  approved-providers PDF (`/proveedores/pdf`); the MCP modules `proveedores`
  and `evaluaciones_proveedor` (no deletes and no decisions).

## Organization context

Decisions OC1 to OC5 of `organization-context` (ISO 9001:2026 clauses 4.1 to
4.3). Context issues (`CuestionContexto`, the `Register` `context_issues.issues`
on `crud`) and the QMS scope (`AlcanceSGC`, `qms_scope`) share the
`ORGANIZATION_CONTEXT` resource; interested parties (`ParteInteresada`,
`stakeholders`) keep `INTERESTED_PARTIES`. Writes that judge a date take
`today` from the adapter (`local_today()`).

- **Context issues**: `tipo`, `categoria`, `titulo`, `descripcion`,
  `efecto`, `pertinente` and `fecha_identificacion` are required.
  - The category matches the type (`CATEGORIAS_POR_TIPO`,
    `CATEGORY_MISMATCH`). An issue that is not relevant says why
    (`justificacion_pertinencia`, `JUSTIFICATION_REQUIRED`). `cambio_climatico`
    marks the issues concerning climate change, relevant or not.
  - `fecha_identificacion` is never after `today` (`IDENTIFIED_IN_FUTURE`);
    `fecha_revision`, the next review, is never before it
    (`REVIEW_BEFORE_IDENTIFICATION`). A `vigente` issue whose next review is
    before `today` is overdue (`revision_vencida`); a `superada` one never is.
  - An issue cited by a risk or an opportunity is never deleted
    (`CITED_BY_RISKS`). The delete locks the row (`FOR UPDATE`); when the
    database refuses it, the message is `CITED_BY_RISKS` if a citation was
    inserted meanwhile and `DELETE_CONFLICT` otherwise.
- **The risk link**: risks and opportunities name the issue they arise from
  (`cuestion_id`). A newly linked issue must exist and be `vigente`
  (`risks.ISSUE_SUPERSEDED`) and is read with a shared lock (`FOR SHARE`); an
  edit that keeps an issue already cited is accepted after it is superseded.
  `linked` and `linked_ids` give the records citing an issue.
- **Interested parties**: OC2 adds `tipo`, `pertinente` (`None` is not
  evaluated), `responsable_id`, `fecha_revision` and `requisitos_climaticos`.
  A new or changed `fecha_revision` is never before `today`
  (`REVIEW_IN_PAST`). A party is overdue for review when its next review is
  before `today`, whatever its relevance.
- **QMS scope**: numbered versions with a statement, products and services,
  sites, notes and one applicability entry per requirement of the fixed
  `CATALOGUE` (38 entries, 4.1 to 10.3), stored in the plain table
  `aplicabilidad_alcance`. A requirement that does not apply needs a
  justification (`JUSTIFICATION_REQUIRED`; `applicability_errors` reports it
  row by row for a form).
  - States `borrador` -> `vigente` -> `obsoleto`. Two partial unique indexes
    keep one draft and one current version; the loser of a race gets
    `Conflict`.
  - `new_draft` starts the next version as a copy of the current one; the
    first needs a statement (`STATEMENT_REQUIRED`) and applies everything.
    There is at most one draft (`ONE_DRAFT`).
  - `update_draft` changes only a draft (`IMMUTABLE`). A catalogue code a
    version does not store reads as applying, and the next write stores it.
  - `approve` (ADMIN, never `mcp`) records the acting user's linked person
    (`APPROVER_REQUIRED`) and `today`, and makes the current version
    `obsoleto` from `today`. `delete_draft` deletes a draft only
    (`ONLY_DRAFTS_DELETED`; its writers, never `mcp`).
  - The applicability table has no audit row of its own: each version's audit
    rows carry `aplicabilidad`.
- `context_issues.attention(session, actor, *, today)` gathers the dashboard's
  flags: the `vigente` issues and the interested parties overdue for review
  (read grants of both resources). `context_issues.changes(session, desde,
  hasta)` (no policy check) feeds the management review inputs.
- Adapters: the web screens (`/contexto/`, `/partes_interesadas/`,
  `/alcance/`) with the scope PDF (`/alcance/pdf` for the current version,
  `/alcance/<id>/pdf` for any version), the dashboard card, the MCP modules
  `cuestiones_contexto`, `alcances_sgc` (draft writes only) and
  `partes_interesadas` (no deletes, no approval), and the management review
  inputs.

## Changes to the QMS

Decisions CP1 to CP7 of `change-planning` (ISO 9001:2026 clause 6.3).
`qms_changes` offers two registers on `crud`, `changes` (`CambioSGC`) and
`tasks` (`TareaCambio`), which share the `QMS_CHANGES` resource. Writes, and
the lists filtered by lateness, take `today` from the adapter
(`local_today()`).

- **CP1, the change**: `titulo`, `descripcion`, `origen`, `proposito`,
  `responsable_id` (a person, active when newly chosen) and `fecha_propuesta`
  (never after `today`, `PROPOSED_IN_FUTURE`) are required; `fecha_prevista`,
  the planned implementation, is never before the proposal
  (`PLANNED_BEFORE_PROPOSAL`). The 6.3 considerations are `consecuencias` (a),
  `integridad` (b), `recursos` (c) and `responsabilidades` (d), next to
  `proposito` (a) and `comunicacion`.
  - The origin may name the record behind the change through its one link
    (`models.CHANGE_LINKS`): a review action of type `cambio_sgc`
    (`REVIEW_ACTION_NOT_A_CHANGE`), a nonconformity, an audit, a risk, an
    opportunity or a `vigente` context issue (`ISSUE_SUPERSEDED`). A link is
    checked when newly set, with the action and the issue read `FOR SHARE`;
    one made earlier stays even if its record changed since. A link with
    another origin is refused (`LINK_NEEDS_ORIGIN`), and so is changing the
    origin while the old link stays (`CLEAR_LINK_FIRST`). Each link is
    `RESTRICT`: a cited record is not deleted, and its service says so
    (`*_CITED_BY_CHANGES`).
  - `procesos` and `documentos` replace the links in the plain tables
    `cambio_procesos` and `cambio_documentos`, which have no audit rows of
    their own: every audit row of a change carries both lists.
- **CP2, the lifecycle**, only through the service: `propuesto` ->
  `aprobado` -> `implantado` -> `eficacia_revisada`, and `cancelado` from
  `propuesto` or `aprobado`. Each transition has a `may_*` guard (role,
  scopes, channel and state) and refuses a failing one with a bare
  `PermissionDenied`; none runs on `mcp`.
  - `approve` (ADMIN) needs the four considerations, a planned date and a
    task that is not cancelled (one `APPROVAL_INCOMPLETE` naming what is
    missing), and records the acting user's person (`APPROVER_REQUIRED`) and
    `today`.
  - `implement` (ADMIN, AUDITOR) needs every task `completada` or
    `cancelada` (`TASKS_OPEN`) and one `completada` (`NO_COMPLETED_TASK`), and
    a date not after `today` nor before the approval.
  - `review_effectiveness` (ADMIN, AUDITOR) records `eficaz` or `no_eficaz`
    (a comment required for `no_eficaz`, `COMMENT_REQUIRED`), a date not
    before the implementation nor after `today`, and the acting user's person
    as reviewer, never the owner (`REVIEWER_IS_OWNER`; a CHECK constraint
    repeats it).
  - `cancel` (ADMIN) needs a reason and records `today`; the tasks and an
    approval stay as they were.
  - CHECK constraints keep each stage's columns to the states that carry
    them.
- **CP3, editing**: a `propuesto` change takes every field; an `aprobado` one
  only `APPROVED_WRITABLE` (owner, planned date, communication, processes and
  documents), its plan being fixed (`PLAN_FIXED`), and keeps a planned date
  (`PLANNED_DATE_KEPT`); `implantado`, `eficacia_revisada` and `cancelado`
  take nothing (`READ_ONLY`). An edit that changes nothing writes nothing,
  whatever the state. Only a `propuesto` change is deleted, its tasks first
  (`ONLY_PROPOSED_DELETED`).
- **CP4, tasks**: `cambio_id` (never changes, `TASK_CHANGE_FIXED`),
  `descripcion`, `responsable_id` and `fecha_limite` are required; `estado`
  starts `pendiente`. `fecha_completada` is given exactly when the task is
  `completada`, never after `today`. Tasks are created, changed and deleted
  only while their change is `propuesto` or `aprobado` (`TASKS_CLOSED`; the
  change read `FOR SHARE`, the task locked `FOR UPDATE`).
- **CP5, access**: every role reads; ADMIN and AUDITOR create and update
  changes and tasks; ADMIN deletes; the transitions narrow it as above.
- **Overdue rules**: `fuera_de_plazo(change, *, today)` is an `aprobado`
  change planned before `today`; `is_overdue(task, change, *, today)` a
  `pendiente` or `en_curso` task past its deadline while its change is
  `propuesto` or `aprobado`. The same rules drive the `fuera_de_plazo`,
  `tareas_vencidas` and `vencida` filters.
- **Readers** (CP6): `attention(session, actor, *, today)` gathers the
  dashboard's flags (changes awaiting approval, late approved changes,
  overdue tasks, implemented changes awaiting the effectiveness review; the
  card counts changes, so overdue tasks count the changes holding them).
  `in_period(session, desde, hasta, *, cut_off=None)` (no policy check)
  counts each stage by its date in the period and lists the changes late at
  `cut_off` (`hasta` by default) and those implemented or reviewed in the
  period, uncapped: the management review caps them. `for_origin(session,
  actor, origen, record_id)` gives the origin pages the changes a record
  gives rise to.
- Like every service it flushes and never commits and writes an audit row
  next to each change. An integrity failure while a change is created,
  updated or moved, inside its audit row included, is
  `Conflict(WRITE_CONFLICT)`.
- Adapters: the web screens (`/cambios/`) with the register PDF
  (`/cambios/pdf`) and each change's plan PDF (`/cambios/<id>/pdf`, CP7); the
  dashboard card; the origin pages (review actions with «Planificar cambio»,
  nonconformities, audits, risks, opportunities and context issues); the
  management review inputs; and the MCP modules `cambios_sgc` and
  `tareas_cambio` (create, update, list and get only).

## Document control

Decisions DC1-DC8 of `document-control` (ISO 9001 clause 7.5). A document's
text lives in numbered revisions; only these functions of
`document_revisions` move a revision's `estado`, each after locking the
document's row (`lock_document`, `SELECT … FOR UPDATE`) and through `_change`
(snapshot, values, stamp, audit, flush):

- `documents.create(session, actor, data)` creates the document and its draft
  revision 1 (`clean_first`, `add_first`); `start_draft(session, actor,
  document_id, data)` starts the next draft from the text in force (at most
  one revision in preparation); `edit_draft` changes a `borrador`.
- `submit` (`borrador` → `en_revision`, needs a change summary),
  `approve(…, approver_id, today)` (`en_revision` → `aprobado`; ADMIN only,
  never the author nor the acting user's own person), `reject(…, comment)`
  (back to `borrador`) and `publish(…, today)` (`aprobado` → `vigente`; the
  previous revision in force becomes `obsoleto` the same day).
- `withdraw(session, actor, document_id, reason, *, today)` (ADMIN only)
  obsoletes the revision in force and makes the document read-only. Nothing is
  ever deleted except a later draft (`discard_draft`).
- Dates are `date` values from the adapter (`local_today()`); a `datetime` is
  a `ValueError`, never a server-clock fallback. Two partial unique indexes
  back "one in preparation, one in force"; a lost race is a `Conflict`.
- `require_state` and `require_draft` are lock-free pre-checks for the
  adapter (refuse a page or an upload early); the write checks again under the
  lock. `ever_published` locks `documents.update` out of changing the code.
- Reading: `get`, `list_`, `effective`, `in_preparation`; non-ADMIN/AUDITOR
  actors only ever see the revision in force. `documents.list_` and
  `list_page` filter by `category`, `owner_id`, `status` (`vigente`,
  `sin_publicar`, `de_baja`) and `overdue_on`; `documents.due_for_review(…,
  today, within_days)` feeds the dashboard card and the weekly alert
  (`audit_notifications.send_document_review_alert`).

Attachments (DC7) are stored before they are recorded, and a file is deleted
only once nothing committed references it:

1. **Store**: `document_files.store(base_dir, stream, original_name, *, max_bytes)`
   checks the size, the extension and the content (magic bytes, ZIP
   structure), writes the file atomically under a random name (mode 0600) and
   returns a `StoredFile` (names, size, SHA-256, MIME type). It knows nothing
   of the database. The adapter calls `require_draft` first.
2. **Attach**: `document_revisions.attach(session, actor, revision_id,
   stored)` records it on a `borrador` under the lock and returns the stored
   name of the file it replaces. If attach refuses, the adapter rolls back and
   deletes the new file.
3. **Commit**: the adapter commits. A failed commit may still have succeeded,
   so it deletes nothing.
4. **Delete the old file**: only after a successful commit, the adapter
   removes the replaced file (`detach` and `discard_draft` return the name to
   delete the same way). Anything left behind is removed by `flask
   cleanup-document-files`.

## Error mapping

Services raise `DomainError` subclasses with a safe, user-facing message. The
web adapter (`app/utils/error_handlers.py`) maps them:

| Error | JSON clients | HTML clients |
|---|---|---|
| `NotFound` | 404 `{"error": ...}` | the route 404 page |
| `Conflict` | 409 (session rolled back) | flash, rollback, back to the form |
| `ValidationError` | 422 (session rolled back) | flash, rollback, back to the form |
| `PermissionDenied` | 403 | notice and redirect to the dashboard |

`AuthenticationFailed` (a bearer token was not accepted) has no web mapping
because the web adapter never raises it; a token adapter maps it to its
protocol's "unauthenticated" answer (see below).

A client is treated as JSON when the body is JSON or it prefers
`application/json`. Another adapter owns its own mapping.

## Web adapter

- `app/utils/web_actor.current_actor()` builds the `web` actor.
- `app/utils/permissions.require_permission(action, resource)` guards routes
  that render a form without calling a service; unknown names fail at import.
- `can(action, resource)` is a Jinja global that hides controls with the same
  rule the services enforce. The services remain the authority: hiding a
  button is a convenience, never the protection.
- `tests/test_route_write_guard.py` fails when a module in `app/routes/` calls
  `db.session.add/delete` (or the other session write methods), runs a bulk
  `query.delete/update`, or instantiates an audited model. Routes may still
  `commit` and `rollback`.

## Writing a new adapter

The MCP server follows these steps (`app/mcp_server/context.py`:
`authenticate` and `unit_of_work`); copy it for another adapter.

1. Run inside a Flask application context (the models and `db.session` need it)
   or provide your own `Session`.
2. Authenticate the caller from its `Authorization: Bearer` header. The
   service returns the actor itself (see "API tokens" below):

   ```python
   try:
       actor = api_tokens.authenticate(db.session, raw_token, secret_key=SECRET_KEY)
       db.session.commit()            # persists the throttled last_used_at
   except AuthenticationFailed as failure:
       db.session.rollback()
       security_logger.log_api_token_auth_failed(failure.reason, failure.token_prefix)
       ...                            # answer 401 with failure.message, nothing else
   except SQLAlchemyError:
       db.session.rollback()          # lookup, throttled flush or commit failed
       ...                            # answer 503/500; never reuse a broken session
   ```

   `failure.reason` is an `errors.AuthFailure` member (`MALFORMED`,
   `UNKNOWN_PREFIX`, `BAD_SECRET`, `REVOKED`, `EXPIRED`, `USER_MISSING`,
   `USER_INACTIVE`, or the default `INVALID`); it is for the log only. `api_tokens.status(token, now)` returns
   `active`, `expired` or `revoked` and is the single validity rule.

   The actor has channel `mcp`, the owner's current role and the token scopes.
   Never widen the scopes. The `mcp` channel cannot delete, whatever the role,
   and can never manage tokens.
3. Own the transaction around each tool call:

   ```python
   try:
       result = nonconformities.create(db.session, actor, payload)
       db.session.commit()
   except DomainError:
       db.session.rollback()
       raise                      # translate to your protocol's error shape
   ```

   Roll back on every failure: services flush but leave the session as is.
4. Serialize the result yourself (the marshmallow schemas in `app/schemas.py`
   are available) and map the domain errors to your protocol.
5. Never write audited models through the session directly; call a service, or
   add one (copy `nonconformities.py` or declare a `crud.Spec`) together with
   a policy entry for the new resource.

## API tokens

`app/services/api_tokens.py` issues and verifies the credentials an agent
adapter uses. It stays framework-free: the adapter passes `secret_key`
(the application's `SECRET_KEY`) explicitly, owns the commit and writes the
security log.

- **Format.** `iso_<8 hex prefix>_<43-char URL-safe secret>` (256 bits). The
  prefix identifies the row and is safe to show and log; the plaintext is
  returned once by `issue(...)` and cannot be recovered.
- **Storage.** Only `HMAC-SHA256(key, token)` is kept, where `key` is derived
  from `SECRET_KEY` with a fixed domain label; it is compared with
  `hmac.compare_digest`. Rotating `SECRET_KEY` therefore invalidates every
  token: issue new ones afterwards.
- **Scopes.** `read` and `write`, stored sorted (default `read`). They only
  narrow the owner's role through the policy.
- **Expiry and revocation.** A token expires after 90 days by default (1 to
  365 days; a token is rejected from `expires_at` onwards). `revoke` takes effect on the next
  call. `last_used_at` is updated at most once every five minutes.
- **Authentication.** `authenticate(session, raw, secret_key=...)` returns the
  `Actor(channel="mcp", ...)` with the owner's *current* role. Malformed,
  unknown, tampered, expired and revoked tokens, and tokens whose owner no
  longer exists or is inactive, all raise `AuthenticationFailed` with one
  generic message.
  `failure.reason` and `failure.token_prefix` exist only for the security log
  (`log_api_token_auth_failed`) and must never be sent to the client.
- **Management.** Only an administrator on a non-`mcp` channel may `issue`,
  `list_` and `revoke`; the Flask CLI (`create-api-token`, `list-api-tokens`,
  `revoke-api-token`) acts as `Actor(channel="cli")`. Issue and revoke write
  an audit row (never the secret or the hash) and a security-log event.
  `ApiToken` is deliberately outside `AUDITED_MODELS`, because
  `last_used_at` changes without an audit row.
- **Own tokens ("Mi perfil").** `list_own(session, actor)` and
  `revoke_own(session, actor, token_id)` bypass the administrator-only grant
  with an explicit ownership check: only a `web` actor with a user id and an
  active account, and only that user's tokens. Every other channel (`mcp`
  above all: a bearer token never manages tokens), or an actor without a user
  id, gets `PermissionDenied`. Another user's token and an unknown id both
  raise `NotFound` with the same message, so a token's existence is never
  revealed; an already revoked token is a `Conflict`. Revocation is audited
  like `revoke`, and the web adapter writes the same `API_TOKEN_REVOKED`
  security-log event. Issuing stays CLI-only.

## Users

`app/services/users.py` manages accounts. Users are never deleted, so their
audit attribution and tokens stay intact.

- **Administration** follows the `USERS` grant (ADMIN and AUDITOR read, ADMIN
  writes): `list_`, `get`, `create` (username, e-mail, role and an initial
  password of at least 8 characters), `update` (e-mail and role; usernames
  never change, to keep audit labels stable) and `set_active`. Deactivation
  also revokes the user's active API tokens, which needs the `API_TOKENS`
  grant too, so an `mcp` administrator can reactivate but never deactivate.
- **Guard rails.** Nobody changes their own role or deactivates their own
  account (`ValidationError`), and the last active administrator can never be
  demoted or deactivated (`Conflict`; the check locks the administrators' rows
  so concurrent requests serialize on PostgreSQL). E-mail addresses are stored
  trimmed and lower-cased and are unique regardless of case (`Conflict`).
- **Self-service.** `change_own_email` and `change_own_password` act only on
  `actor.user_id`, need an active account and the current password (a wrong
  one is a `ValidationError` and changes nothing), and are refused on the
  `mcp` channel. A new password must have at least 8 characters and differ
  from the current one.
- **Audit and security log.** `User` is outside `AUDITED_MODELS`, so every
  write records its audit row explicitly; a password change is recorded as
  `{"credential_changed": true}` and the hash never appears. The web adapter
  writes the security log after the commit (`USER_DEACTIVATED`,
  `USER_REACTIVATED`, `PASSWORD_RESET_LINK_SENT`, `PASSWORD_CHANGE_SUCCESS`,
  and `EMAIL_CHANGE_SUCCESS` with a masked address such as `a***@example.com`).
