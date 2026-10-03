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
record = nonconformities.update(db.session, actor, 7, {"estado": "Cerrada"})
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
| `nonconformities.py` | Nonconformities (the reference pattern) |
| `audits.py`, `documents.py` | Audits, documents (bespoke) |
| `crud.py` | Generic helper driven by a `Spec` (model, resource, fields, ordering) |
| `training.py`, `satisfaction.py`, `stakeholders.py`, `improvements.py` | Plain HTML registers on `crud` |
| `roles_responsibilities.py`, `risks_opportunities.py`, `training_resources.py`, `process_operations.py`, `audit_indicators.py` | JSON registers on `crud` |
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
| Nonconformities, improvements, surveys, training, stakeholders | all roles | all roles | ADMIN |
| Audits | ADMIN, AUDITOR | ADMIN, AUDITOR | ADMIN |
| Documents | ADMIN | ADMIN | ADMIN |
| Roles, risks and opportunities, training resources, process operations, audit indicators | all roles | ADMIN, AUDITOR | ADMIN |
| Users, audit log (policy only, no routes yet) | ADMIN, AUDITOR | ADMIN | nobody |

Roles are `ADMINISTRADOR`, `AUDITOR` and `OPERATIVO`. Hard deletes leave an
`AuditLog` snapshot of the removed record.

## Error mapping

Services raise `DomainError` subclasses with a safe, user-facing message. The
web adapter (`app/utils/error_handlers.py`) maps them:

| Error | JSON clients | HTML clients |
|---|---|---|
| `NotFound` | 404 `{"error": ...}` | the route 404 page |
| `Conflict` | 409 (session rolled back) | flash, rollback, back to the form |
| `ValidationError` | 422 (session rolled back) | flash, rollback, back to the form |
| `PermissionDenied` | 403 | notice and redirect to the dashboard |

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
2. Authenticate the caller and load its `User`; then build the actor with the
   channel and the token scopes:

   ```python
   actor = Actor.from_user(user, channel="mcp", scopes={"read", "write"})
   ```

   Never pass broader scopes than the token grants. The `mcp` channel cannot
   delete, whatever the role.
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
