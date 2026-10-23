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

"""Risks, opportunities and their actions (ISO 9001 clause 6.1, ``risks-opportunities``).

Decisions RO1 to RO4 and RO6. Three registers on the generic CRUD helper,
``risks`` (``Riesgo``), ``opportunities`` (``Oportunidad``) and ``actions``
(``AccionRiesgo``), each with ``get``, ``list_``, ``list_page``, ``create``,
``update`` and ``delete`` like ``quality_objectives``. They share the policy
resource ``RISKS_OPPORTUNITIES``: every role reads, administrators and
auditors create and update, administrators delete, and the ``mcp`` channel
never deletes. ``create`` and ``update`` take ``today``, the adapter's local
date (services never read the clock), and so do the lists whose
``revision_vencida`` or ``vencida`` filter is used.

Risks and opportunities:

- Choices are member names. An owner (``responsable_id``, a person, active when
  newly chosen) is required on create; rows migrated from the old register may
  lack one until someone sets it. ``proceso_id`` and ``parte_interesada_id``
  cite an existing process and interested party, and ``cuestion_id`` the
  context issue that gives rise to the record (OC1 of ``organization-context``),
  which must be ``vigente`` when newly linked: a link to an issue superseded
  later stays, and the record stays editable.
- Each 1-5 pair (likelihood and impact, the residual pair, benefit and
  feasibility) is given whole or left empty (RO4: "sin valorar"); a risk's
  residual pair needs its first pair. ``nivel`` and ``prioridad`` come from the
  products (``models.nivel``, ``models.prioridad``); the filters use the same
  bands as product ranges (``LEVEL_RANGES``, ``PRIORITY_RANGES``).
- ``fecha_identificacion`` defaults to ``today`` and is never after it;
  ``fecha_revision``, the next review, is never before it. A record is open
  unless it is a ``cerrado`` risk or an ``aprovechada`` or ``descartada``
  opportunity; only an open one is overdue for review (``revision_vencida``).
- A record with actions is never deleted (``Conflict``).
- Both filter by ``cuestion_id`` too. The screens (RO-3) also filter by
  ``abierto``, risks by one cell of the heat map (``probabilidad`` and
  ``impacto``, each alone too) and by ``alto_sin_accion``, the open high or
  critical risks ``attention`` flags; ``heat_map`` counts the open assessed
  risks by cell.

Actions belong to exactly one open risk or opportunity, which is read with a
shared lock, and never move to another one. A ``completada`` action, and only
one, has ``fecha_completada``, never after ``today``. Its effectiveness
(``eficacia`` with ``fecha_evaluacion``) is evaluated only once it is
completed, on or after the completion and never after ``today``. An action is
overdue (``is_overdue``, the ``vencida`` filter) when pending or in progress
after its deadline. ``attention`` gathers what the dashboard flags.

Every rule judges the record as the write would leave it. An unknown key is
refused before anything is looked up, and an edit that changes nothing writes
nothing. Like every service this module flushes and never commits and writes
an audit row next to each change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import and_, exists, false, func, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    ESCALA_MAX, ESCALA_MIN, AccionRiesgo, CuestionContexto, DecisionOportunidad,
    EficaciaAccion, EstadoAccionRiesgo, EstadoCuestion, EstadoOportunidad, EstadoRiesgo,
    NivelRiesgo, Oportunidad, OrigenRiesgo, ParteInteresada, PrioridadOportunidad,
    ProcesoOperacion, Riesgo, TratamientoRiesgo, nivel, prioridad,
)
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.RISKS_OPPORTUNITIES
TITLE_MAX = 200  # mirrors ``titulo``
OPEN_ACTION_STATES = (EstadoAccionRiesgo.pendiente, EstadoAccionRiesgo.en_curso)
CLOSED_RISK_STATES = (EstadoRiesgo.cerrado,)
CLOSED_OPPORTUNITY_STATES = (EstadoOportunidad.aprovechada, EstadoOportunidad.descartada)

RISK_NOT_FOUND = "Riesgo no encontrado."
OPPORTUNITY_NOT_FOUND = "Oportunidad no encontrada."
ACTION_NOT_FOUND = "Acción no encontrada."
HALF_ASSESSMENT = "Indica «{first}» y «{second}» a la vez, o deja los dos vacíos."
RESIDUAL_WITHOUT_ASSESSMENT = (
    "La valoración residual necesita antes la valoración del riesgo («probabilidad» e "
    "«impacto»).")
IDENTIFIED_IN_FUTURE = "La fecha de identificación no puede ser futura."
UNKNOWN_ISSUE = "El campo «cuestion_id» no corresponde a ninguna cuestión del contexto."
ISSUE_SUPERSEDED = "La cuestión del contexto está superada; elige una vigente."
REVIEW_BEFORE_IDENTIFICATION = (
    "La fecha de revisión no puede ser anterior a la fecha de identificación.")
RISK_HAS_ACTIONS = (
    "No se puede eliminar un riesgo que tiene acciones; elimina antes sus acciones.")
OPPORTUNITY_HAS_ACTIONS = (
    "No se puede eliminar una oportunidad que tiene acciones; elimina antes sus acciones.")
DELETE_CONFLICT = (
    "No se ha podido eliminar el registro porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo.")
ONE_PARENT = (
    "Indica el riesgo («riesgo_id») o la oportunidad («oportunidad_id») de la acción, "
    "uno de los dos.")
UNKNOWN_RISK = "El campo «riesgo_id» no corresponde a ningún riesgo."
UNKNOWN_OPPORTUNITY = "El campo «oportunidad_id» no corresponde a ninguna oportunidad."
RISK_CLOSED = "El riesgo está cerrado: no admite acciones nuevas."
OPPORTUNITY_CLOSED = "La oportunidad está aprovechada o descartada: no admite acciones nuevas."
PARENT_FIXED = "Una acción no puede cambiar de riesgo u oportunidad."
COMPLETION_REQUIRED = "Indica la fecha de finalización de una acción completada."
COMPLETION_ONLY_WHEN_DONE = "Solo una acción completada tiene fecha de finalización."
COMPLETION_IN_FUTURE = "La fecha de finalización no puede ser futura."
EVALUATION_PAIR = (
    "Indica a la vez el resultado («eficacia») y la fecha («fecha_evaluacion») de la "
    "evaluación de la eficacia.")
EVALUATION_NOT_DONE = "Solo se evalúa la eficacia de una acción completada."
EVALUATION_BEFORE_COMPLETION = (
    "La evaluación de la eficacia no puede ser anterior a la finalización de la acción.")
EVALUATION_IN_FUTURE = "La fecha de la evaluación de la eficacia no puede ser futura."
BAD_FILTER = "El filtro «{key}» no admite ese valor."
BAD_FLAG = "El filtro «{key}» debe ser verdadero o falso."


def _bands(band: Callable[[int, int], Any]) -> dict[Any, tuple[int, int]]:
    """The smallest and largest product of each band of ``band`` over the 1-5 scales."""
    ranges: dict[Any, tuple[int, int]] = {}
    for first in range(ESCALA_MIN, ESCALA_MAX + 1):
        for second in range(ESCALA_MIN, ESCALA_MAX + 1):
            product, member = first * second, band(first, second)
            low, high = ranges.get(member, (product, product))
            ranges[member] = (min(low, product), max(high, product))
    return ranges


LEVEL_RANGES = _bands(nivel)  # {NivelRiesgo: (lowest, highest product)}
PRIORITY_RANGES = _bands(prioridad)


# -- field validators ------------------------------------------------------------------


def _scale(data: Mapping[str, Any], key: str) -> int | None:
    """A value of a 1-5 scale, or ``None`` for a pending assessment."""
    value = data[key]
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, int)
            or not ESCALA_MIN <= value <= ESCALA_MAX):
        raise ValidationError(
            f"El campo «{key}» debe ser un número entero del {ESCALA_MIN} al {ESCALA_MAX}.")
    return value


def _optional_member(data: Mapping[str, Any], key: str, enum_cls: type) -> Any:
    """A member given by name, or ``None``."""
    return None if data[key] is None else fields.enum_member(data, key, enum_cls)


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


def _exists(model: type, nothing: str) -> Callable[[Session, str, Any, Any], None]:
    """A ``crud.Field.check`` refusing an id of ``model`` that does not exist.

    ``nothing`` ends the message, e.g. "ningún proceso".
    """

    def check(session: Session, key: str, value: Any, current: Any) -> None:
        if value is None or value == current:
            return
        if not people.is_db_id(value) or session.get(model, value) is None:
            raise ValidationError(f"El campo «{key}» no corresponde a {nothing}.")

    return check


def _current_issue(session: Session, key: str, value: Any, current: Any) -> None:
    """A ``crud.Field.check``: a newly linked context issue exists and is ``vigente``.

    The issue is read with ``FOR SHARE``, like the actions' parent, so superseding
    it meanwhile waits for this write.
    """
    if value is None or value == current:
        return
    found = _locked(session, CuestionContexto, value, read=True)
    if found is None:
        raise ValidationError(UNKNOWN_ISSUE)
    if found.estado is not EstadoCuestion.vigente:
        raise ValidationError(ISSUE_SUPERSEDED)


def _register_fields(estado: type, *assessment: str) -> tuple[crud.Field, ...]:
    """Fields a risk and an opportunity share, around their assessment scales."""
    return (
        crud.Field("titulo", partial(fields.text, required=True, max_length=TITLE_MAX),
                   required=True),
        crud.Field("descripcion", partial(fields.text, required=True, strip=False),
                   required=True),
        crud.Field("origen", partial(fields.enum_member, enum_cls=OrigenRiesgo),
                   required=True),
        crud.Field("parte_interesada_id", fields.integer,
                   check=_exists(ParteInteresada, "ninguna parte interesada")),
        crud.Field("proceso_id", fields.integer,
                   check=_exists(ProcesoOperacion, "ningún proceso")),
        crud.Field("cuestion_id", fields.integer, check=_current_issue),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        *(crud.Field(name, _scale) for name in assessment),
        crud.Field("estado", partial(fields.enum_member, enum_cls=estado)),
        crud.Field("fecha_identificacion", fields.required_date),
        crud.Field("fecha_revision", _optional_date),
        crud.Field("notas", partial(fields.text, strip=False)),
    )


RISK_SPEC = crud.Spec(
    model=Riesgo,
    resource=RESOURCE,
    not_found=RISK_NOT_FOUND,
    fields=(*_register_fields(EstadoRiesgo, "probabilidad", "impacto",
                              "probabilidad_residual", "impacto_residual"),
            crud.Field("tratamiento", partial(_optional_member, enum_cls=TratamientoRiesgo))),
    # The latest identified first.
    order_by=(Riesgo.fecha_identificacion.desc(), Riesgo.id.desc()),
)

OPPORTUNITY_SPEC = crud.Spec(
    model=Oportunidad,
    resource=RESOURCE,
    not_found=OPPORTUNITY_NOT_FOUND,
    fields=(*_register_fields(EstadoOportunidad, "beneficio", "viabilidad"),
            crud.Field("decision", partial(_optional_member, enum_cls=DecisionOportunidad))),
    order_by=(Oportunidad.fecha_identificacion.desc(), Oportunidad.id.desc()),
)

ACTION_SPEC = crud.Spec(
    model=AccionRiesgo,
    resource=RESOURCE,
    not_found=ACTION_NOT_FOUND,
    fields=(
        crud.Field("riesgo_id", fields.integer),
        crud.Field("oportunidad_id", fields.integer),
        crud.Field("descripcion", partial(fields.text, required=True, strip=False),
                   required=True),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("fecha_limite", fields.required_date, required=True),
        crud.Field("estado", partial(fields.enum_member, enum_cls=EstadoAccionRiesgo)),
        crud.Field("fecha_completada", _optional_date),
        crud.Field("eficacia", partial(_optional_member, enum_cls=EficaciaAccion)),
        crud.Field("fecha_evaluacion", _optional_date),
        crud.Field("comentario_eficacia", partial(fields.text, strip=False)),
    ),
    # The soonest deadline first.
    order_by=(AccionRiesgo.fecha_limite, AccionRiesgo.id),
)

_PAIRS = {Riesgo: (("probabilidad", "impacto"), ("probabilidad_residual", "impacto_residual")),
          Oportunidad: (("beneficio", "viabilidad"),)}


def _actions_foreign_key(target: type) -> str:
    """Name of the actions' foreign key to ``target``, as the model declares it."""
    (name,) = {fk.constraint.name for fk in AccionRiesgo.__table__.foreign_keys
               if fk.column.table is target.__table__}
    return name


# -- risks and opportunities -----------------------------------------------------------


def _writes(spec: crud.Spec, has_actions: str) -> tuple[Callable, Callable, Callable]:
    """``create``, ``update`` and ``delete`` of the risks' or the opportunities' register."""
    model = spec.model
    foreign_key = _actions_foreign_key(model)

    def create(session: Session, actor: Actor, data: Mapping[str, Any], *, today: date) -> Any:
        """Create a record; ``fecha_identificacion`` defaults to ``today``."""
        policy.require(actor, Action.CREATE, RESOURCE)
        today = _today(today)
        _reject_unknown(spec, data)
        if isinstance(data, Mapping) and "fecha_identificacion" not in data:
            data = {**data, "fecha_identificacion": today}
        _check_record(spec, data, None, today)
        return crud.create(spec, session, actor, data)

    def update(session: Session, actor: Actor, record_id: int, data: Mapping[str, Any], *,
               today: date) -> Any:
        """Apply the given fields; the record is locked (``FOR UPDATE``) while it changes."""
        policy.require(actor, Action.UPDATE, RESOURCE)
        today = _today(today)
        _reject_unknown(spec, data)
        found = _locked(session, model, record_id, read=False)
        if found is None:
            raise NotFound(spec.not_found)
        if _changed(spec, found, data):  # a no-op is left to ``crud``, which writes nothing
            _check_record(spec, data, found, today)
        return crud.update(spec, session, actor, found.id, data)

    def delete(session: Session, actor: Actor, record_id: int) -> None:
        """Hard-delete a record without actions; an action added meanwhile meets the key."""
        policy.require(actor, Action.DELETE, RESOURCE)
        found = _locked(session, model, record_id, read=False)
        if found is None:
            raise NotFound(spec.not_found)
        if _has_actions(session, model, found.id):
            raise Conflict(has_actions)
        try:
            crud.delete(spec, session, actor, found.id)
        except Conflict as exc:
            cause = exc.__cause__
            mine = _violated_constraint(cause) == foreign_key
            raise Conflict(has_actions if mine else DELETE_CONFLICT) from cause

    return create, update, delete


def _check_record(spec: crud.Spec, data: Any, found: Any, today: date) -> None:
    """The rules a risk or an opportunity keeps, on the values the write leaves.

    Data ``crud`` refuses anyway (not a mapping, unknown keys) is left to it.
    """
    if not _well_formed(spec, data):
        return
    final = partial(_final, spec, data, found)
    pairs = _PAIRS[spec.model]
    for first, second in pairs:
        if (first in data or second in data) and (final(first) is None) != (
                final(second) is None):
            raise ValidationError(HALF_ASSESSMENT.format(first=first, second=second))
    if len(pairs) > 1 and final(pairs[1][0]) is not None and final(pairs[0][0]) is None:
        raise ValidationError(RESIDUAL_WITHOUT_ASSESSMENT)
    if "fecha_identificacion" in data and final("fecha_identificacion") > today:
        raise ValidationError(IDENTIFIED_IN_FUTURE)
    identified, review = final("fecha_identificacion"), final("fecha_revision")
    if review is not None and identified is not None and review < identified:
        raise ValidationError(REVIEW_BEFORE_IDENTIFICATION)


def is_open(record: Riesgo | Oportunidad) -> bool:
    """Whether a risk is not ``cerrado``, or an opportunity neither used nor discarded."""
    if isinstance(record, Riesgo):
        return record.estado not in CLOSED_RISK_STATES
    return record.estado not in CLOSED_OPPORTUNITY_STATES


def revision_vencida(record: Riesgo | Oportunidad, *, today: date) -> bool:
    """Whether an open record's next review (``fecha_revision``) is before ``today``."""
    return (is_open(record) and record.fecha_revision is not None
            and record.fecha_revision < today)


def _record_conditions(model: type, closed: tuple, estado: Any, responsable_id: Any,
                       proceso_id: Any, origen: Any, sin_valorar: Any, revision_vencida: Any,
                       today: Any, abierto: Any = None, cuestion_id: Any = None) -> list[Any]:
    """Filters a risk and an opportunity share (AND); members or their names."""
    where: list[Any] = []
    if abierto is not None:
        still_open = model.estado.not_in(closed)
        where.append(still_open if _flag(abierto, "abierto") else not_(still_open))
    for column, value in ((model.responsable_id, responsable_id),
                          (model.proceso_id, proceso_id), (model.cuestion_id, cuestion_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    if estado:
        where.append(model.estado == _member(estado, model.estado.type.enum_class, "estado"))
    if origen:
        where.append(model.origen == _member(origen, OrigenRiesgo, "origen"))
    if sin_valorar is not None:
        pending = getattr(model, _PAIRS[model][0][0]).is_(None)
        where.append(pending if _flag(sin_valorar, "sin_valorar") else not_(pending))
    if revision_vencida is not None:
        late = and_(model.estado.not_in(closed), model.fecha_revision.is_not(None),
                    model.fecha_revision < _today(today))
        where.append(late if _flag(revision_vencida, "revision_vencida") else not_(late))
    return where


def _band(model: type, value: Any, enum_cls: type, ranges: dict, key: str) -> list[Any]:
    """The products of the first pair inside the range of the band ``value``."""
    if not value:
        return []
    first, second = (getattr(model, name) for name in _PAIRS[model][0])
    low, high = ranges[_member(value, enum_cls, key)]
    return [(first * second).between(low, high)]


def _risk_conditions(estado: Any = None, nivel: Any = None, responsable_id: int | None = None,
                     proceso_id: int | None = None, origen: Any = None,
                     sin_valorar: bool | None = None, revision_vencida: bool | None = None,
                     today: date | None = None, abierto: bool | None = None,
                     probabilidad: int | None = None, impacto: int | None = None,
                     alto_sin_accion: bool | None = None,
                     cuestion_id: int | None = None) -> list[Any]:
    """Risk filters (AND); ``revision_vencida`` needs ``today``, the adapter's date."""
    where = [*_record_conditions(Riesgo, CLOSED_RISK_STATES, estado, responsable_id,
                                 proceso_id, origen, sin_valorar, revision_vencida, today,
                                 abierto, cuestion_id),
             *_band(Riesgo, nivel, NivelRiesgo, LEVEL_RANGES, "nivel")]
    for column, value, key in ((Riesgo.probabilidad, probabilidad, "probabilidad"),
                               (Riesgo.impacto, impacto, "impacto")):
        if value is None:
            continue
        if (isinstance(value, bool) or not isinstance(value, int)
                or not ESCALA_MIN <= value <= ESCALA_MAX):
            raise ValidationError(BAD_FILTER.format(key=key))
        where.append(column == value)
    if alto_sin_accion is not None:
        flagged = _high_without_action()
        where.append(flagged if _flag(alto_sin_accion, "alto_sin_accion") else not_(flagged))
    return where


def _high_without_action() -> Any:
    """An open ``alto`` or ``critico`` risk without a pending or in-progress action.

    Never NULL (an unassessed risk is plainly not one), so its negation holds the rest.
    """
    acting = exists().where(AccionRiesgo.riesgo_id == Riesgo.id,
                            AccionRiesgo.estado.in_(OPEN_ACTION_STATES))
    return and_(Riesgo.estado.not_in(CLOSED_RISK_STATES), Riesgo.probabilidad.is_not(None),
                Riesgo.impacto.is_not(None),
                Riesgo.probabilidad * Riesgo.impacto >= LEVEL_RANGES[NivelRiesgo.alto][0],
                not_(acting))


def _opportunity_conditions(estado: Any = None, prioridad: Any = None,
                            responsable_id: int | None = None, proceso_id: int | None = None,
                            origen: Any = None, sin_valorar: bool | None = None,
                            revision_vencida: bool | None = None,
                            today: date | None = None, abierto: bool | None = None,
                            cuestion_id: int | None = None) -> list[Any]:
    """Opportunity filters (AND); ``revision_vencida`` needs ``today``."""
    return [*_record_conditions(Oportunidad, CLOSED_OPPORTUNITY_STATES, estado, responsable_id,
                                proceso_id, origen, sin_valorar, revision_vencida, today,
                                abierto, cuestion_id),
            *_band(Oportunidad, prioridad, PrioridadOportunidad, PRIORITY_RANGES, "prioridad")]


# -- actions ---------------------------------------------------------------------------


def _create_action(session: Session, actor: Actor, data: Mapping[str, Any], *,
                   today: date) -> AccionRiesgo:
    """Add an action to an open risk or opportunity; it starts ``pendiente``."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(ACTION_SPEC, data)
    if _well_formed(ACTION_SPEC, data):
        _open_parent(session, data)
        _check_action(data, None, today)
    return crud.create(ACTION_SPEC, session, actor, data)


def _update_action(session: Session, actor: Actor, action_id: int, data: Mapping[str, Any], *,
                   today: date) -> AccionRiesgo:
    """Apply the given fields; the action keeps its risk or opportunity."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(ACTION_SPEC, data)
    found = session.get(AccionRiesgo, action_id) if people.is_db_id(action_id) else None
    if found is None:
        raise NotFound(ACTION_NOT_FOUND)
    if _well_formed(ACTION_SPEC, data):
        if any(key in data and _FIELDS[AccionRiesgo][key].clean(data, key) != getattr(found, key)
               for key in ("riesgo_id", "oportunidad_id")):
            raise ValidationError(PARENT_FIXED)
        if _changed(ACTION_SPEC, found, data):  # a no-op is left to ``crud``
            _check_action(data, found, today)
    return crud.update(ACTION_SPEC, session, actor, found.id, data)


def _delete_action(session: Session, actor: Actor, action_id: int) -> None:
    """Hard-delete an action, keeping its snapshot in the audit log."""
    crud.delete(ACTION_SPEC, session, actor, action_id)


def _open_parent(session: Session, data: Mapping[str, Any]) -> None:
    """The action's one risk or opportunity exists and is open, read with ``FOR SHARE``."""
    clean = _FIELDS[AccionRiesgo]
    risk_id, opportunity_id = (clean[key].clean(data, key) if key in data else None
                               for key in ("riesgo_id", "oportunidad_id"))
    if (risk_id is None) == (opportunity_id is None):
        raise ValidationError(ONE_PARENT)
    model, record_id = (Riesgo, risk_id) if risk_id is not None else (Oportunidad,
                                                                      opportunity_id)
    found = _locked(session, model, record_id, read=True)
    if found is None:
        raise ValidationError(UNKNOWN_RISK if model is Riesgo else UNKNOWN_OPPORTUNITY)
    if not is_open(found):
        raise ValidationError(RISK_CLOSED if model is Riesgo else OPPORTUNITY_CLOSED)


def _check_action(data: Mapping[str, Any], found: AccionRiesgo | None, today: date) -> None:
    """Completion and effectiveness rules (RO3) on the values the write leaves."""
    final = partial(_final, ACTION_SPEC, data, found)
    estado = final("estado") or EstadoAccionRiesgo.pendiente
    completed, evaluated = final("fecha_completada"), final("fecha_evaluacion")
    done = estado is EstadoAccionRiesgo.completada
    if done and completed is None:
        raise ValidationError(COMPLETION_REQUIRED)
    if not done and completed is not None:
        raise ValidationError(COMPLETION_ONLY_WHEN_DONE)
    if "fecha_completada" in data and completed is not None and completed > today:
        raise ValidationError(COMPLETION_IN_FUTURE)
    if (final("eficacia") is None) != (evaluated is None):
        raise ValidationError(EVALUATION_PAIR)
    if evaluated is None:
        return
    if not done:
        raise ValidationError(EVALUATION_NOT_DONE)
    if evaluated < completed:
        raise ValidationError(EVALUATION_BEFORE_COMPLETION)
    if "fecha_evaluacion" in data and evaluated > today:
        raise ValidationError(EVALUATION_IN_FUTURE)


def _overdue(today: date) -> Any:
    return and_(AccionRiesgo.estado.in_(OPEN_ACTION_STATES), AccionRiesgo.fecha_limite < today)


def _action_conditions(riesgo_id: int | None = None, oportunidad_id: int | None = None,
                       estado: Any = None, responsable_id: int | None = None,
                       vencida: bool | None = None, today: date | None = None) -> list[Any]:
    """Action filters (AND); ``vencida`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    for column, value in ((AccionRiesgo.riesgo_id, riesgo_id),
                          (AccionRiesgo.oportunidad_id, oportunidad_id),
                          (AccionRiesgo.responsable_id, responsable_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    if estado:
        where.append(AccionRiesgo.estado == _member(estado, EstadoAccionRiesgo, "estado"))
    if vencida is not None:
        overdue = _overdue(_today(today))
        where.append(overdue if _flag(vencida, "vencida") else not_(overdue))
    return where


def is_overdue(action: AccionRiesgo, *, today: date) -> bool:
    """Whether ``action`` is pending or in progress after its deadline."""
    return action.estado in OPEN_ACTION_STATES and action.fecha_limite < today


# -- the registers ---------------------------------------------------------------------

_create_risk, _update_risk, _delete_risk = _writes(RISK_SPEC, RISK_HAS_ACTIONS)
_create_opportunity, _update_opportunity, _delete_opportunity = _writes(
    OPPORTUNITY_SPEC, OPPORTUNITY_HAS_ACTIONS)

risks = Register(
    SPEC=RISK_SPEC,
    conditions=_risk_conditions,
    create=_create_risk,
    update=_update_risk,
    delete=_delete_risk,
)

opportunities = Register(
    SPEC=OPPORTUNITY_SPEC,
    conditions=_opportunity_conditions,
    create=_create_opportunity,
    update=_update_opportunity,
    delete=_delete_opportunity,
)

actions = Register(
    SPEC=ACTION_SPEC,
    conditions=_action_conditions,
    create=_create_action,
    update=_update_action,
    delete=_delete_action,
)


def attention(session: Session, actor: Actor, *, today: date) -> dict[str, list[Any]]:
    """What the dashboard flags on ``today``, among open records only (read grant).

    - ``riesgos_altos_sin_accion``: risks of level ``alto`` or ``critico`` without
      a pending or in-progress action, the highest product first.
    - ``riesgos_revision_vencida`` and ``oportunidades_revision_vencida``: their
      next review is before ``today``, the earliest first.
    - ``riesgos_sin_valorar`` and ``oportunidades_sin_valorar``: not assessed
      yet (RO4), the earliest identified first.
    """
    policy.require(actor, Action.READ, RESOURCE)
    today = _today(today)
    product = Riesgo.probabilidad * Riesgo.impacto
    open_risk = Riesgo.estado.not_in(CLOSED_RISK_STATES)
    open_opportunity = Oportunidad.estado.not_in(CLOSED_OPPORTUNITY_STATES)

    def found(model: type, *where: Any, order: tuple) -> list[Any]:
        return list(session.scalars(select(model).where(*where).order_by(*order)))

    def late(model: type, still_open: Any) -> list[Any]:
        return found(model, still_open, model.fecha_revision < today,
                     order=(model.fecha_revision, model.id))

    def pending(model: type, still_open: Any, column: Any) -> list[Any]:
        return found(model, still_open, column.is_(None),
                     order=(model.fecha_identificacion, model.id))

    return {
        "riesgos_altos_sin_accion": found(Riesgo, _high_without_action(),
                                          order=(product.desc(), Riesgo.id)),
        "riesgos_revision_vencida": late(Riesgo, open_risk),
        "oportunidades_revision_vencida": late(Oportunidad, open_opportunity),
        "riesgos_sin_valorar": pending(Riesgo, open_risk, Riesgo.probabilidad),
        "oportunidades_sin_valorar": pending(Oportunidad, open_opportunity,
                                             Oportunidad.beneficio),
    }


def heat_map(session: Session, actor: Actor) -> dict[tuple[int, int], int]:
    """How many open assessed risks fall in each (likelihood, impact) cell (read grant).

    Cells without risks are left out.
    """
    policy.require(actor, Action.READ, RESOURCE)
    rows = session.execute(
        select(Riesgo.probabilidad, Riesgo.impacto, func.count())
        .where(Riesgo.estado.not_in(CLOSED_RISK_STATES), Riesgo.probabilidad.is_not(None))
        .group_by(Riesgo.probabilidad, Riesgo.impacto))
    return {(likelihood, impact): count for likelihood, impact, count in rows}


# -- helpers ---------------------------------------------------------------------------

_FIELDS = {spec.model: {f.name: f for f in spec.fields}
           for spec in (RISK_SPEC, OPPORTUNITY_SPEC, ACTION_SPEC)}


def _final(spec: crud.Spec, data: Mapping[str, Any], found: Any, key: str) -> Any:
    """``key`` as the write leaves it: the clean given value, else the record's own."""
    if key in data:
        return _FIELDS[spec.model][key].clean(data, key)
    return None if found is None else getattr(found, key)


def _changed(spec: crud.Spec, found: Any, data: Any) -> bool:
    """Whether ``data`` changes ``found``; invalid data counts as a change."""
    if not _well_formed(spec, data):
        return True
    try:
        return any(_FIELDS[spec.model][key].clean(data, key) != getattr(found, key)
                   for key in data)
    except ValidationError:
        return True


def _has_actions(session: Session, model: type, record_id: int) -> bool:
    column = AccionRiesgo.riesgo_id if model is Riesgo else AccionRiesgo.oportunidad_id
    return bool(session.scalar(select(exists().where(column == record_id))))


def _locked(session: Session, model: type, record_id: Any, *, read: bool) -> Any:
    """The record loaded afresh with its row locked (shared when ``read``), or ``None``."""
    if not people.is_db_id(record_id):
        return None
    return session.scalars(
        select(model).where(model.id == record_id)
        .with_for_update(read=read).execution_options(populate_existing=True)
    ).one_or_none()


def _member(value: Any, enum_cls: type, key: str) -> Any:
    """A filter value given as the member or its name."""
    try:
        return fields.enum_member({key: value}, key, enum_cls)
    except ValidationError:
        raise ValidationError(BAD_FILTER.format(key=key)) from None


def _flag(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(BAD_FLAG.format(key=key))
    return value


def _well_formed(spec: crud.Spec, data: Any) -> bool:
    """Whether ``data`` is a mapping of writable keys (otherwise ``crud`` refuses it)."""
    return isinstance(data, Mapping) and set(data) <= spec.writable


def _reject_unknown(spec: crud.Spec, data: Any) -> None:
    """Refuse a key outside ``spec`` before any lookup (``crud`` refuses non-mappings)."""
    if isinstance(data, Mapping):
        fields.reject_unknown(data, spec.writable)


def _today(today: Any) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("today must be a date")  # a programming error, not user input
    return today


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
