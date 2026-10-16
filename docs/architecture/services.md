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
| `quality_objectives.py` | Quality objectives and their measurements, target checks and overdue measurements (see below) |
| `management_reviews.py` | Management reviews, their inputs drawn from the registers, holding and closing, and their actions (see below) |
| `people.py`, `competence.py` | People (each linked to at most one user and holding QMS roles), and competence required per role and demonstrated per person, with the required-versus-demonstrated matrix |
| `crud.py` | Generic helper driven by a `Spec` (model, resource, fields, ordering) |
| `training.py`, `satisfaction.py`, `stakeholders.py`, `improvements.py` | Plain HTML registers on `crud` |
| `roles_responsibilities.py`, `risks_opportunities.py`, `training_resources.py`, `process_operations.py`, `audit_indicators.py` | JSON registers on `crud` |
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
| Nonconformities, corrective actions, improvements, surveys, training, stakeholders | all roles | all roles | ADMIN |
| Audits, audit programmes, audit findings | ADMIN, AUDITOR | ADMIN, AUDITOR (approve a programme: ADMIN) | ADMIN |
| Documents | all roles (drafts: ADMIN, AUDITOR) | ADMIN, AUDITOR, never `mcp` (approve and withdraw: ADMIN) | nobody (withdrawn instead) |
| Roles, risks and opportunities, training resources, process operations, audit indicators | all roles | ADMIN, AUDITOR | ADMIN |
| People; competence requirements and records | all roles | ADMIN, AUDITOR | ADMIN |
| Quality objectives and their measurements | all roles | ADMIN, AUDITOR | ADMIN |
| Management reviews and their actions | ADMIN, AUDITOR | ADMIN, AUDITOR (hold and close: ADMIN, never `mcp`) | ADMIN |
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
  minutes PDF (`/revisiones-direccion/<id>/pdf`); the MCP modules
  `revisiones_direccion` and `acciones_revision` (no deletes, holds or
  closes).

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
