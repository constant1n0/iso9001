# Service layer

Every write to a quality record goes through a framework-free service in
`app/services/`. The Flask routes are thin adapters; a future MCP server is
another adapter over the same functions, so both share one validation, one
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
| `crud.py` | Generic helper driven by a `Spec` (model, resource, fields, ordering) |
| `training.py`, `satisfaction.py`, `stakeholders.py`, `improvements.py` | Plain HTML registers on `crud` |
| `roles_responsibilities.py`, `risks_opportunities.py`, `training_resources.py`, `process_operations.py`, `audit_indicators.py` | JSON registers on `crud` |
| `users.py` | User accounts: administration and self-service (see below) |
| `api_tokens.py` | Bearer tokens for non-browser adapters (see below) |
| `policy.py`, `actor.py`, `errors.py`, `audit.py`, `attribution.py`, `fields.py` | The shared kernel |

## Actor and channels

`Actor(user_id, label, role, channel, scopes)` is frozen. `channel` is one of
`web`, `mcp`, `cli`, `system` and is stored on every audit row. `scopes` is
`None` for unrestricted web sessions or a set drawn from `{"read", "write"}`:
a scope only narrows the role, never widens it (a read-only token cannot
write even for an administrator, and a `write` scope does not let an
`OPERATIVO` user into a resource the role cannot reach).

## Permission matrix

`policy.can(actor, action, resource)` is pure. The channel and the scopes are
checked first (`mcp` never deletes), then the role.

| Resource | Read | Create / update | Delete |
|---|---|---|---|
| Nonconformities, corrective actions, improvements, surveys, training, stakeholders | all roles | all roles | ADMIN |
| Audits | ADMIN, AUDITOR | ADMIN, AUDITOR | ADMIN |
| Documents | ADMIN | ADMIN | ADMIN |
| Roles, risks and opportunities, training resources, process operations, audit indicators | all roles | ADMIN, AUDITOR | ADMIN |
| Users; audit log (policy only, no routes yet) | ADMIN, AUDITOR | ADMIN | nobody |
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

## Writing a new adapter (for example the MCP server)

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
   `UNKNOWN_PREFIX`, `BAD_SECRET`, `REVOKED`, `EXPIRED`, `USER_MISSING`);
   it is for the log only. `api_tokens.status(token, now)` returns
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
  unknown, tampered, expired and revoked tokens, and tokens whose owner was
  deleted, all raise `AuthenticationFailed` with one generic message.
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
