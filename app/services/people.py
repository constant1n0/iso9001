# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""People (``Person``) who do work under the QMS and the roles they hold.

Policy resource ``PEOPLE`` (decision Q1 of ``qms-people``): every role reads,
administrators and auditors create and update, administrators delete, and the
``mcp`` channel never deletes. Reads use the ``crud`` helpers; writes are
bespoke because roles live in the ``persona_roles`` association table.

Roles are written as ``rol_ids``, a list of ``RolResponsabilidad`` ids that
replaces the current set; a record exposes ``roles`` and ``rol_ids``. The
association table has no audit row of its own, so every audit row of a person
carries ``rol_ids`` next to the columns. Like every service this module
flushes and never commits, and an update that changes nothing writes nothing.

Errors:

- ``PermissionDenied``: the policy refuses the actor.
- ``NotFound``: unknown person id.
- ``ValidationError``: unknown or missing keys, invalid values, unknown role
  ids or an unknown user.
- ``Conflict``: an e-mail already used by another person (ignoring case), a
  user already linked to another person, or deleting a person that other
  records still cite (deactivate it instead).
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

from sqlalchemy import false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Person, RolResponsabilidad, User
from . import audit, crud, fields, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

NOMBRE_MAX = 150  # mirrors Person.nombre
DB_INT_MAX = 2**31 - 1  # PostgreSQL ``integer``: larger ids cannot exist
WRITABLE_FIELDS = frozenset({"nombre", "email", "user_id", "activo", "notas", "rol_ids"})
REQUIRED_ON_CREATE = frozenset({"nombre"})

NOT_FOUND = "Persona no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
ROLE_IDS = "El campo «rol_ids» debe ser una lista de identificadores de rol."
ACTIVE_FLAG = "El campo «activo» debe ser verdadero o falso."
UNKNOWN_USER = "El usuario indicado no existe."
USER_TAKEN = "Ese usuario ya está vinculado a otra persona."
EMAIL_TAKEN = "Ya existe una persona con ese correo electrónico."
DUPLICATE = "Ya existe una persona con ese usuario o correo electrónico."
STILL_REFERENCED = (
    "No se puede eliminar una persona citada en otros registros; desactívala en su lugar."
)

_READS = crud.Spec(
    model=Person,
    resource=Resource.PEOPLE,
    not_found=NOT_FOUND,
    fields=(),  # writes are bespoke (see ``WRITABLE_FIELDS``)
    order_by=(Person.nombre, Person.id),
)

get = partial(crud.get, _READS)


def _conditions(nombre: str | None, activo: bool | None, rol_id: int | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if nombre:
        where.append(Person.nombre.ilike(f"%{nombre}%"))
    if activo is not None:
        where.append(Person.activo.is_(activo))
    if rol_id is not None:
        held = Person.roles.any(RolResponsabilidad.id_rol == rol_id)
        where.append(held if _is_db_id(rol_id) else false())
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    nombre: str | None = None,
    activo: bool | None = None,
    rol_id: int | None = None,
) -> list[Person]:
    """People ordered by name then id; ``nombre`` matches a substring, ignoring case."""
    return crud.list_(_READS, session, actor, _conditions(nombre, activo, rol_id))


def list_page(
    session: Session,
    actor: Actor,
    *,
    nombre: str | None = None,
    activo: bool | None = None,
    rol_id: int | None = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[Person], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(_READS, session, actor, _conditions(nombre, activo, rol_id),
                          page=page, per_page=per_page)


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> Person:
    """Create an active person; only ``nombre`` is required."""
    policy.require(actor, Action.CREATE, Resource.PEOPLE)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values, roles = _clean(session, data)
    _ensure_free(session, values)
    created = Person(**({"activo": True} | values), roles=roles or [])
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(DUPLICATE) from exc
    row.after = _snapshot(created)  # the row is still pending: add the roles
    return created


def update(
    session: Session, actor: Actor, person_id: int, data: Mapping[str, Any]
) -> Person:
    """Apply the given fields; ``rol_ids`` replaces the roles held."""
    policy.require(actor, Action.UPDATE, Resource.PEOPLE)
    found = _load(session, person_id)
    values, roles = _clean(session, data)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if roles is not None and {role.id_rol for role in roles} == set(found.rol_ids):
        roles = None
    if not changes and roles is None:
        return found
    _ensure_free(session, changes, own_id=found.id)  # queries before any change
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    if roles is not None:
        found.roles = roles
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, DUPLICATE)
    return found


def delete(session: Session, actor: Actor, person_id: int) -> None:
    """Hard-delete a person and its role assignments, keeping a full audit snapshot."""
    policy.require(actor, Action.DELETE, Resource.PEOPLE)
    found = _load(session, person_id)
    audit.record(session, actor, "delete", found, before=_snapshot(found))
    session.delete(found)
    _flush(session, STILL_REFERENCED)


def _load(session: Session, person_id: int) -> Person:
    found = session.get(Person, person_id)
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _snapshot(person: Person) -> dict[str, Any]:
    """Column values plus ``rol_ids``, as stored on the person's audit rows."""
    return audit.snapshot(person) | {"rol_ids": person.rol_ids}


def _clean(
    session: Session, data: Mapping[str, Any]
) -> tuple[dict[str, Any], list[RolResponsabilidad] | None]:
    """Validated column values and the roles (``None`` when ``rol_ids`` is absent)."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)
    values: dict[str, Any] = {}
    if "nombre" in data:
        values["nombre"] = fields.text(data, "nombre", required=True, max_length=NOMBRE_MAX)
    if "email" in data:
        values["email"] = _optional_email(data)
    if "user_id" in data:
        values["user_id"] = _user_id(session, data)
    if "activo" in data:
        if not isinstance(data["activo"], bool):
            raise ValidationError(ACTIVE_FLAG)
        values["activo"] = data["activo"]
    if "notas" in data:
        values["notas"] = fields.text(data, "notas", strip=False)
    roles = _roles(session, data["rol_ids"]) if "rol_ids" in data else None
    return values, roles


def _optional_email(data: Mapping[str, Any]) -> str | None:
    """Blank means no e-mail; otherwise a valid address, trimmed and lower-cased."""
    value = data["email"]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return fields.email(data, "email")


def _user_id(session: Session, data: Mapping[str, Any]) -> int | None:
    value = fields.integer(data, "user_id")
    if value is not None and (not _is_db_id(value) or session.get(User, value) is None):
        raise ValidationError(UNKNOWN_USER)
    return value


def _roles(session: Session, value: Any) -> list[RolResponsabilidad]:
    """The roles named by ``value``; every id must exist (duplicates are ignored)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(_is_int(item) for item in value):
        raise ValidationError(ROLE_IDS)
    wanted = set(value)
    searchable = [item for item in wanted if _is_db_id(item)]
    found = list(session.scalars(
        select(RolResponsabilidad).where(RolResponsabilidad.id_rol.in_(searchable))
    )) if searchable else []
    missing = sorted(wanted - {role.id_rol for role in found})
    if missing:
        raise ValidationError(f"Roles desconocidos: {', '.join(map(str, missing))}.")
    return found


def _ensure_free(session: Session, values: Mapping[str, Any], own_id: int | None = None) -> None:
    """Raise ``Conflict`` when another person holds the user link or the e-mail."""
    checks = (
        ("user_id", Person.user_id, USER_TAKEN),
        ("email", func.lower(Person.email), EMAIL_TAKEN),
    )
    for key, column, message in checks:
        if values.get(key) is None:
            continue
        query = select(Person.id).where(column == values[key])
        if own_id is not None:
            query = query.where(Person.id != own_id)
        if session.scalar(query) is not None:
            raise Conflict(message)


def _flush(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_db_id(value: int) -> bool:
    return 1 <= value <= DB_INT_MAX
