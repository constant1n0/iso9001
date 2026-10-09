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

"""Competence each role requires and each person has demonstrated (ISO 9001 clause 7.2).

Two registers on the generic CRUD helper, exposed as ``requirements``
(``CompetenceRequirement``) and ``records`` (``CompetenceRecord``); each offers
``get``, ``list_``, ``list_page``, ``create``, ``update`` and ``delete`` with
the usual ``crud`` contract. Both share the policy resource ``COMPETENCE``
(decision Q1 of ``qms-people``): every role reads, administrators and auditors
create and update, administrators delete, and the ``mcp`` channel never
deletes.

``tipo`` and ``evaluacion_eficacia`` are given as member names (``formacion``,
``no_eficaz``); every id must name an existing role, person, requirement or
training, and a newly chosen person must be active (``people.check_reference``).
A record's dates and evaluation follow the rules in ``check_record``, checked
on the record as the write would leave it before anything changes. A
requirement that records still cite cannot be deleted.

``matrix`` compares the competence each role requires with what its active
holders have demonstrated (read grant of ``COMPETENCE``); see ``cell_status``
for how a status and its deciding record are chosen.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, false, select
from sqlalchemy.orm import Session

from ..models import (
    Capacitacion, CompetenceEvaluation, CompetenceRecord, CompetenceRequirement,
    CompetenceType, Person, RolResponsabilidad, persona_roles,
)
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

TEXT_MAX = 500  # mirrors ``descripcion`` and ``evidencia``

REQUIREMENT_NOT_FOUND = "Requisito de competencia no encontrado."
RECORD_NOT_FOUND = "Competencia acreditada no encontrada."
REQUIREMENT_IN_USE = (
    "No se puede eliminar un requisito de competencia que citan competencias acreditadas."
)
EXPIRY_BEFORE_OBTAINED = "La fecha de caducidad no puede ser anterior a la fecha de obtención."
EVALUATION_INCOMPLETE = (
    "Una evaluación de eficacia distinta de «pendiente» necesita "
    "la fecha de evaluación y el evaluador."
)
EVALUATION_BEFORE_OBTAINED = (
    "La fecha de evaluación no puede ser anterior a la fecha de obtención."
)


def _is_db_id(value: int) -> bool:
    return 1 <= value <= people.DB_INT_MAX


def _matches(column: Any, value: int) -> Any:
    """``column == value``, or nothing when no stored id can equal ``value``."""
    return column == value if _is_db_id(value) else false()


def _must_exist(model: type, what: str) -> Callable[[Session, str, Any, Any], None]:
    """A ``crud.Field.check`` refusing an id of ``model`` that does not exist."""

    def check(session: Session, key: str, value: int | None, current: int | None) -> None:
        if value is None or value == current:
            return
        if not _is_db_id(value) or session.get(model, value) is None:
            raise ValidationError(f"El campo «{key}» no corresponde a {what}.")

    return check


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A real ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


REQUIREMENT_SPEC = crud.Spec(
    model=CompetenceRequirement,
    resource=Resource.COMPETENCE,
    not_found=REQUIREMENT_NOT_FOUND,
    fields=(
        crud.Field("rol_id", partial(fields.integer, required=True), required=True,
                   check=_must_exist(RolResponsabilidad, "ningún rol")),
        crud.Field("tipo", partial(fields.enum_member, enum_cls=CompetenceType), required=True),
        crud.Field("descripcion", partial(fields.text, required=True, max_length=TEXT_MAX),
                   required=True),
        crud.Field("criterio", partial(fields.text, strip=False)),
    ),
    order_by=(CompetenceRequirement.rol_id, CompetenceRequirement.id),
)

RECORD_SPEC = crud.Spec(
    model=CompetenceRecord,
    resource=Resource.COMPETENCE,
    not_found=RECORD_NOT_FOUND,
    fields=(
        crud.Field("persona_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("requisito_id", fields.integer,
                   check=_must_exist(CompetenceRequirement, "ningún requisito de competencia")),
        crud.Field("evidencia", partial(fields.text, required=True, max_length=TEXT_MAX),
                   required=True),
        crud.Field("capacitacion_id", fields.integer,
                   check=_must_exist(Capacitacion, "ninguna capacitación")),
        crud.Field("fecha_obtencion", fields.required_date, required=True),
        crud.Field("fecha_caducidad", _optional_date),
        crud.Field("evaluacion_eficacia",
                   partial(fields.enum_member, enum_cls=CompetenceEvaluation)),
        crud.Field("fecha_evaluacion", _optional_date),
        crud.Field("evaluador_id", fields.integer, check=people.check_reference),
    ),
    order_by=(CompetenceRecord.fecha_obtencion.desc(), CompetenceRecord.id.desc()),
)

# What a new record holds for the fields ``check_record`` reads when they are absent.
_RECORD_DEFAULTS = {
    "fecha_caducidad": None,
    "evaluacion_eficacia": CompetenceEvaluation.pendiente,
    "fecha_evaluacion": None,
    "evaluador_id": None,
}


def check_record(state: Mapping[str, Any]) -> None:
    """Refuse a record whose dates or evaluation break a rule (decision Q5).

    ``state`` holds ``fecha_obtencion``, ``fecha_caducidad``,
    ``evaluacion_eficacia``, ``fecha_evaluacion`` and ``evaluador_id``.
    """
    obtained = state["fecha_obtencion"]
    if state["fecha_caducidad"] is not None and state["fecha_caducidad"] < obtained:
        raise ValidationError(EXPIRY_BEFORE_OBTAINED)
    evaluated_on = state["fecha_evaluacion"]
    if state["evaluacion_eficacia"] is not CompetenceEvaluation.pendiente and (
        evaluated_on is None or state["evaluador_id"] is None
    ):
        raise ValidationError(EVALUATION_INCOMPLETE)
    if evaluated_on is not None and evaluated_on < obtained:
        raise ValidationError(EVALUATION_BEFORE_OBTAINED)


def _check_record_write(data: Any, found: CompetenceRecord | None) -> None:
    """Run ``check_record`` on the record as ``data`` would leave it.

    It runs ahead of ``crud``, so a refused write changes nothing. Malformed
    data (not a mapping, unknown or missing keys) is left to ``crud``, which
    refuses it with its usual message; invalid values fail here as they would
    there.
    """
    if not isinstance(data, Mapping) or not set(data) <= RECORD_SPEC.writable:
        return
    required = {f.name for f in RECORD_SPEC.fields if f.required}
    if found is None and not required <= set(data):
        return
    values = {f.name: f.clean(data, f.name) for f in RECORD_SPEC.fields if f.name in data}
    if found is None:
        current = dict(_RECORD_DEFAULTS)
    else:
        current = {key: getattr(found, key) for key in (*_RECORD_DEFAULTS, "fecha_obtencion")}
    check_record(current | values)


def _create_record(session: Session, actor: Actor, data: Mapping[str, Any]) -> CompetenceRecord:
    """Create a record; its evaluation starts ``pendiente`` unless given."""
    policy.require(actor, Action.CREATE, Resource.COMPETENCE)
    _check_record_write(data, None)
    return crud.create(RECORD_SPEC, session, actor, data)


def _update_record(session: Session, actor: Actor, record_id: int,
                   data: Mapping[str, Any]) -> CompetenceRecord:
    """Apply the given fields; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.COMPETENCE)
    found = session.get(CompetenceRecord, record_id)
    if found is None:
        raise NotFound(RECORD_NOT_FOUND)
    _check_record_write(data, found)
    return crud.update(RECORD_SPEC, session, actor, record_id, data)


def _requirement_in_use(session: Session, requirement_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(_matches(CompetenceRecord.requisito_id, requirement_id)))
    ))


def _delete_requirement(session: Session, actor: Actor, requirement_id: int) -> None:
    """Hard-delete a requirement; one that records still cite raises ``Conflict``.

    A record citing it after the check meets the foreign key at the flush.
    """
    policy.require(actor, Action.DELETE, Resource.COMPETENCE)
    if _requirement_in_use(session, requirement_id):
        raise Conflict(REQUIREMENT_IN_USE)
    try:
        crud.delete(REQUIREMENT_SPEC, session, actor, requirement_id)
    except Conflict as exc:
        raise Conflict(REQUIREMENT_IN_USE) from exc.__cause__


def _requirement_conditions(rol_id: int | None = None,
                            tipo: CompetenceType | None = None) -> list[Any]:
    """Requirement filters; they combine with AND."""
    where: list[Any] = []
    if rol_id is not None:
        where.append(_matches(CompetenceRequirement.rol_id, rol_id))
    if tipo is not None:
        where.append(CompetenceRequirement.tipo == tipo)
    return where


def _record_conditions(persona_id: int | None = None, requisito_id: int | None = None,
                       evaluacion_eficacia: CompetenceEvaluation | None = None) -> list[Any]:
    """Record filters; they combine with AND."""
    where: list[Any] = []
    if persona_id is not None:
        where.append(_matches(CompetenceRecord.persona_id, persona_id))
    if requisito_id is not None:
        where.append(_matches(CompetenceRecord.requisito_id, requisito_id))
    if evaluacion_eficacia is not None:
        where.append(CompetenceRecord.evaluacion_eficacia == evaluacion_eficacia)
    return where


@dataclass
class Register:
    """One competence register, used like a service module (see the module docstring).

    ``SPEC`` is named like the service modules' constant so the MCP registry
    and its tests treat both alike. ``list_`` and ``list_page`` take the
    register's filters as keyword arguments.
    """

    SPEC: crud.Spec
    conditions: Callable[..., list[Any]]
    create: Callable[..., Any]
    update: Callable[..., Any]
    delete: Callable[..., None]

    def get(self, session: Session, actor: Actor, record_id: int) -> Any:
        """Return one record or raise ``NotFound``."""
        return crud.get(self.SPEC, session, actor, record_id)

    def list_(self, session: Session, actor: Actor, **filters: Any) -> list[Any]:
        """Every record matching ``filters``, in the spec's order."""
        return crud.list_(self.SPEC, session, actor, self.conditions(**filters))

    def list_page(self, session: Session, actor: Actor, *, page: int = 1,
                  per_page: int = crud.DEFAULT_PER_PAGE, **filters: Any) -> tuple[list[Any], int]:
        """One page in the ``list_`` order plus the total matching the same filters."""
        return crud.list_page(self.SPEC, session, actor, self.conditions(**filters),
                              page=page, per_page=per_page)


requirements = Register(
    SPEC=REQUIREMENT_SPEC,
    conditions=_requirement_conditions,
    create=partial(crud.create, REQUIREMENT_SPEC),
    update=partial(crud.update, REQUIREMENT_SPEC),
    delete=_delete_requirement,
)

records = Register(
    SPEC=RECORD_SPEC,
    conditions=_record_conditions,
    create=_create_record,
    update=_update_record,
    delete=partial(crud.delete, RECORD_SPEC),
)


# -- required versus demonstrated competence -----------------------------------------


class MatrixStatus(enum.Enum):
    """How far a person meets one requirement of a role, as shown in the matrix."""

    cumplida = "Cumplida"
    pendiente_evaluacion = "Pendiente de evaluación"
    no_eficaz = "No eficaz"
    caducada = "Caducada"
    falta = "Falta"


@dataclass(frozen=True)
class MatrixCell:
    """One requirement for one person; ``record`` decided the status (``None`` if missing)."""

    requirement: CompetenceRequirement
    status: MatrixStatus
    record: CompetenceRecord | None


@dataclass(frozen=True)
class MatrixRow:
    """An active person holding the role, with one cell per requirement of the role."""

    person: Person
    cells: list[MatrixCell]


@dataclass(frozen=True)
class RoleMatrix:
    """A role, its requirements (the columns) and its active holders (the rows)."""

    role: RolResponsabilidad
    requirements: list[CompetenceRequirement]
    rows: list[MatrixRow]


def _newest_first(record: CompetenceRecord) -> tuple[date, int]:
    return record.fecha_obtencion, record.id


def cell_status(found: Sequence[CompetenceRecord],
                today: date) -> tuple[MatrixStatus, CompetenceRecord | None]:
    """The status of one person against one requirement, and the record deciding it.

    ``found`` are that person's records for that requirement. A record is
    unexpired when it has no expiry date or expires today or later. In order:

    - ``cumplida``: the newest unexpired record evaluated ``eficaz``, even if
      newer records exist (an effective, valid competence is still held);
    - ``pendiente_evaluacion`` or ``no_eficaz``: otherwise the newest
      unexpired record decides, by its evaluation;
    - ``caducada``: every record has expired; the newest one is kept;
    - ``falta``: there is no record.

    "Newest" means the latest ``fecha_obtencion``, then the highest id.
    """
    ordered = sorted(found, key=_newest_first, reverse=True)
    if not ordered:
        return MatrixStatus.falta, None
    valid = [r for r in ordered if r.fecha_caducidad is None or r.fecha_caducidad >= today]
    if not valid:
        return MatrixStatus.caducada, ordered[0]
    for record in valid:
        if record.evaluacion_eficacia is CompetenceEvaluation.eficaz:
            return MatrixStatus.cumplida, record
    latest = valid[0]
    if latest.evaluacion_eficacia is CompetenceEvaluation.pendiente:
        return MatrixStatus.pendiente_evaluacion, latest
    return MatrixStatus.no_eficaz, latest


def matrix(session: Session, actor: Actor, *, rol_id: int | None = None,
           today: date) -> list[RoleMatrix]:
    """Required versus demonstrated competence for every role with requirements.

    Roles are ordered by name ignoring case, then id; requirements by id; rows
    are the active people holding the role, by name then id. ``rol_id`` keeps
    one role (an unknown id gives an empty list). Only records citing one of
    the shown requirements count (see ``cell_status``). It runs a fixed number
    of queries, whatever the number of roles, people or records.
    """
    policy.require(actor, Action.READ, Resource.COMPETENCE)
    shown = select(CompetenceRequirement).where(*_requirement_conditions(rol_id=rol_id))
    requirements = list(session.scalars(shown.order_by(CompetenceRequirement.id)))
    if not requirements:
        return []
    role_ids = {requirement.rol_id for requirement in requirements}
    roles = session.scalars(
        select(RolResponsabilidad).where(RolResponsabilidad.id_rol.in_(role_ids))
    ).all()
    holders = session.execute(
        select(persona_roles.c.rol_id, Person)
        .join(Person, Person.id == persona_roles.c.persona_id)
        .where(Person.activo.is_(True), persona_roles.c.rol_id.in_(role_ids))
        .order_by(Person.nombre, Person.id)
    ).all()
    records = session.scalars(
        select(CompetenceRecord)
        .join(Person, Person.id == CompetenceRecord.persona_id)
        .where(Person.activo.is_(True),
               CompetenceRecord.requisito_id.in_(shown.with_only_columns(
                   CompetenceRequirement.id)))
    ).all()

    by_role: dict[int, list[CompetenceRequirement]] = defaultdict(list)
    for requirement in requirements:
        by_role[requirement.rol_id].append(requirement)
    people_by_role: dict[int, list[Person]] = defaultdict(list)
    for role_id, person in holders:
        people_by_role[role_id].append(person)
    found: dict[tuple[int, int], list[CompetenceRecord]] = defaultdict(list)
    for record in records:
        found[record.persona_id, record.requisito_id].append(record)

    def row(person: Person, columns: list[CompetenceRequirement]) -> MatrixRow:
        return MatrixRow(person, [
            MatrixCell(requirement, *cell_status(found[person.id, requirement.id], today))
            for requirement in columns
        ])

    return [
        RoleMatrix(role, by_role[role.id_rol],
                   [row(person, by_role[role.id_rol]) for person in people_by_role[role.id_rol]])
        for role in sorted(roles, key=lambda role: (role.rol.casefold(), role.id_rol))
    ]
