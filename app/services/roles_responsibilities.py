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

"""Roles and responsibilities (``RolResponsabilidad``, ISO 9001 clause 5.3) on the CRUD helper.

Serves the JSON register and the role pages. ``rol`` is unique: a duplicate
raises ``Conflict``. A role records its responsibilities, its authorities and
the role it reports to (``reporta_a_id``, decision PR3 of
``process-role-registers``): that role must exist and may be neither the role
itself nor one that already reports to it, directly or not, so the reporting
line never forms a cycle (``ValidationError``). ``holders`` gathers the people
holding a role and its competence requirements.

A role that competence requirements cite cannot be deleted (``Conflict`` with
``IN_USE``), nor one that other roles report to (``HAS_SUBORDINATES``); the
people holding a deleted role simply lose it. The foreign keys are ``ON DELETE
RESTRICT``, which SQLite only enforces on request, hence the explicit checks;
one added after the check meets the foreign key at the flush, with the same
message when the database names it (PostgreSQL) and ``IN_USE`` otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import CompetenceRequirement, Person, RolResponsabilidad
from . import competence, crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

ROL_MAX = 50  # mirrors RolResponsabilidad.rol
IN_USE = (
    "No se puede eliminar un rol que citan requisitos de competencia; "
    "elimínalos o asígnalos a otro rol antes."
)
HAS_SUBORDINATES = (
    "No se puede eliminar un rol al que reportan otros roles; "
    "cambia antes a quién reportan."
)
REPORTS_TO_ITSELF = "Un rol no puede reportar a sí mismo."
REPORTING_CYCLE = (
    "La línea de reporte no puede formar un ciclo: ese rol ya reporta, "
    "directa o indirectamente, a este."
)


def _role_exists(session: Session, key: str, value: int | None, current: int | None) -> None:
    """A ``crud.Field.check`` refusing a role id that does not exist."""
    if value is None or value == current:
        return
    if not people.is_db_id(value) or session.get(RolResponsabilidad, value) is None:
        raise ValidationError(f"El campo «{key}» no corresponde a ningún rol.")


_TEXT = partial(fields.text, strip=False)

SPEC = crud.Spec(
    model=RolResponsabilidad,
    resource=Resource.ROLES_RESPONSIBILITIES,
    not_found="Rol y responsabilidad no encontrado.",
    fields=(
        crud.Field("rol", partial(fields.text, required=True, max_length=ROL_MAX), required=True),
        crud.Field("compromiso_calidad", fields.boolean),
        crud.Field("descripcion_politica_calidad", _TEXT),
        crud.Field("responsabilidades", _TEXT),
        crud.Field("autoridades", _TEXT),
        crud.Field("reporta_a_id", fields.integer, check=_role_exists),
    ),
    order_by=(RolResponsabilidad.id_rol,),
    conflict="Ya existe un rol con ese nombre.",
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)  # a new role has nobody below it: no cycle possible
list_page = partial(crud.list_page, SPEC)


@dataclass(frozen=True)
class Holders:
    """Who holds a role and what it requires (clauses 5.3 and 7.2)."""

    personas: list[Person]  # by name then id, inactive people included
    requisitos: list[CompetenceRequirement]  # in the competence register's order


def holders(session: Session, actor: Actor, rol_id: int) -> Holders:
    """The people holding a role and its competence requirements (PR3).

    ``NotFound`` for an unknown role; needs the read grant of roles, people
    and competence.
    """
    found = get(session, actor, rol_id)
    return Holders(
        personas=people.list_(session, actor, rol_id=found.id_rol),
        requisitos=competence.requirements.list_(session, actor, rol_id=found.id_rol),
    )


def update(session: Session, actor: Actor, rol_id: int,
           data: Mapping[str, Any]) -> RolResponsabilidad:
    """Apply the given fields; a new ``reporta_a_id`` may not close a cycle."""
    policy.require(actor, Action.UPDATE, SPEC.resource)
    found = session.get(RolResponsabilidad, rol_id) if people.is_db_id(rol_id) else None
    if found is not None and _well_formed(data) and "reporta_a_id" in data:
        _check_line(session, found, fields.integer(data, "reporta_a_id"))
    return crud.update(SPEC, session, actor, rol_id, data)


def _check_line(session: Session, found: RolResponsabilidad, manager: int | None) -> None:
    """Refuse a manager that is the role itself or already reports to it.

    Walks up from ``manager`` to the top of its line; an unknown manager ends
    the walk and is refused by the field check.
    """
    if manager is None or manager == found.reporta_a_id:
        return
    if manager == found.id_rol:
        raise ValidationError(REPORTS_TO_ITSELF)
    seen: set[int] = set()
    step: int | None = manager
    while step is not None and step not in seen and people.is_db_id(step):
        if step == found.id_rol:
            raise ValidationError(REPORTING_CYCLE)
        seen.add(step)
        step = session.scalar(select(RolResponsabilidad.reporta_a_id)
                              .where(RolResponsabilidad.id_rol == step))


def _well_formed(data: Any) -> bool:
    """Whether ``data`` is a mapping of writable keys (otherwise ``crud`` refuses it)."""
    return isinstance(data, Mapping) and set(data) <= SPEC.writable


def _in_use(session: Session, rol_id: int) -> bool:
    """Whether a competence requirement cites the role."""
    if not people.is_db_id(rol_id):  # no stored id can match
        return False
    return bool(session.scalar(
        select(exists().where(CompetenceRequirement.rol_id == rol_id))
    ))


def _has_subordinates(session: Session, rol_id: int) -> bool:
    """Whether another role reports to the role."""
    return bool(session.scalar(
        select(exists().where(RolResponsabilidad.reporta_a_id == rol_id))
    ))


def delete(session: Session, actor: Actor, rol_id: int) -> None:
    """Hard-delete a role nothing cites; ``Conflict`` with ``IN_USE`` or ``HAS_SUBORDINATES``.

    A requirement or a subordinate added after the checks meets its foreign
    key at the flush: ``HAS_SUBORDINATES`` when the database names the
    reporting line's key, ``IN_USE`` otherwise.
    """
    policy.require(actor, Action.DELETE, Resource.ROLES_RESPONSIBILITIES)
    if not people.is_db_id(rol_id):  # same answer as an id nothing stores
        raise NotFound(SPEC.not_found)
    if _in_use(session, rol_id):
        raise Conflict(IN_USE)
    if _has_subordinates(session, rol_id):
        raise Conflict(HAS_SUBORDINATES)
    try:
        crud.delete(SPEC, session, actor, rol_id)
    except Conflict as exc:
        cause = exc.__cause__
        line = _violated_constraint(cause) == REPORTS_TO_FK
        raise Conflict(HAS_SUBORDINATES if line else IN_USE) from cause


# The reporting line's foreign key, as the model declares it.
(REPORTS_TO_FK,) = {fk.constraint.name for fk in RolResponsabilidad.__table__.foreign_keys
                    if fk.column.table is RolResponsabilidad.__table__}


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
