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

"""Quality objectives and their measurements (ISO 9001 clause 6.2, ``quality-objectives``).

Decisions QO1 to QO4. Two registers on the generic CRUD helper, exposed as
``objectives`` (``ObjetivoCalidad``) and ``measurements``
(``MedicionObjetivo``); each offers ``get``, ``list_``, ``list_page``,
``create``, ``update`` and ``delete`` with the usual ``crud`` contract. Both
share the policy resource ``QUALITY_OBJECTIVES``: every role reads,
administrators and auditors create and update, administrators delete, and the
``mcp`` channel never deletes.

Objectives:

- ``sentido``, ``frecuencia`` and ``estado`` are given as member names;
  ``meta`` and ``valor_inicial`` as finite numbers or decimal text, rounded to
  four decimals (``number``). ``estado`` starts ``activo``.
- ``anio`` defaults to the year of ``fecha_inicio``. ``fecha_limite`` is never
  before ``fecha_inicio``, and the period always keeps the objective's
  measurements inside it.
- ``responsable_id`` cites an existing person, active when newly chosen
  (``people.check_reference``); ``proceso_id`` an existing process.
- ``meta`` and ``sentido`` are fixed once the objective has measurements, and
  such an objective cannot be deleted (``Conflict``; the foreign key is
  ``ON DELETE RESTRICT``).

Measurements, one per objective and date, are added, edited or deleted only
while their objective is ``activo``, never move to another objective, and are
dated inside the objective's period and never after ``today``, the adapter's
local date (services never read the clock). An edit that changes nothing writes
nothing, even when the objective is closed: there is nothing to refuse. An
unknown key is refused before anything is looked up. ``measurements.by_objective``
reads the measurements of several objectives in one query.

``meets``, ``summary`` and ``off_track`` say whether a target is met, the trend
and whether a measurement is overdue (decision QO3; see ``summary``).

Like every service this module flushes and never commits, writes an audit row
next to each change, and an update that changes nothing writes nothing.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import partial
from typing import Any

from sqlalchemy import exists, false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    EstadoObjetivo, FrecuenciaMedicion, MedicionObjetivo, ObjetivoCalidad, ProcesoOperacion,
    SentidoObjetivo,
)
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

TEXT_MAX = 200  # mirrors ``titulo`` and ``indicador``
UNIT_MAX = 30  # mirrors ``unidad``
YEAR_MIN, YEAR_MAX = 2000, 2100  # mirrors ``ck_objetivos_calidad_anio``
NUMBER_LIMIT = Decimal(10) ** 8  # ``Numeric(12, 4)``: eight digits before the point
FOUR_PLACES = Decimal("0.0001")
PERIOD_MONTHS = {
    FrecuenciaMedicion.mensual: 1,
    FrecuenciaMedicion.trimestral: 3,
    FrecuenciaMedicion.semestral: 6,
    FrecuenciaMedicion.anual: 12,
}

OBJECTIVE_NOT_FOUND = "Objetivo de calidad no encontrado."
MEASUREMENT_NOT_FOUND = "Medición no encontrada."
DEADLINE_BEFORE_START = "La fecha límite no puede ser anterior a la fecha de inicio."
PERIOD_EXCLUDES_MEASUREMENTS = (
    "El periodo del objetivo debe incluir sus mediciones, registradas del {first} al {last}."
)
TARGET_FIXED = "La meta y su sentido no pueden cambiar porque el objetivo ya tiene mediciones."
HAS_MEASUREMENTS = (
    "No se puede eliminar un objetivo que tiene mediciones; elimina antes sus mediciones."
)
DELETE_CONFLICT = (
    "No se ha podido eliminar el objetivo porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo."
)
BAD_STATE = "El estado del objetivo no es válido."
UNKNOWN_OBJECTIVE = "El campo «objetivo_id» no corresponde a ningún objetivo de calidad."
OBJECTIVE_FIXED = "Una medición no puede cambiar de objetivo."
OBJECTIVE_NOT_ACTIVE = "Solo se pueden añadir o modificar mediciones de un objetivo activo."
OUTSIDE_PERIOD = (
    "La fecha de la medición debe estar entre el {start} y el {end}, el periodo del objetivo."
)
IN_THE_FUTURE = "La fecha de la medición no puede ser futura."
DUPLICATE_DATE = "El objetivo ya tiene una medición en esa fecha."
NOT_FINITE = "El campo «{key}» debe ser un número finito."


def number(data: Mapping[str, Any], key: str, *, required: bool = False) -> Decimal | None:
    """A number or decimal text, rounded half up to four decimals (``Numeric(12, 4)``).

    A blank optional one (``None`` or blank text) is ``None``. Booleans,
    non-finite values (``NOT_FINITE``: ``nan``, ``inf``, ``sNaN``...) and values
    of more than eight integer digits are refused.
    """
    value = data[key]
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"El campo «{key}» es obligatorio.")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValidationError(f"El campo «{key}» debe ser un número.")
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(f"El campo «{key}» debe ser un número.") from None
    if not parsed.is_finite():
        raise ValidationError(NOT_FINITE.format(key=key))
    if abs(parsed) < NUMBER_LIMIT:
        rounded = parsed.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        if abs(rounded) < NUMBER_LIMIT:
            return rounded
    raise ValidationError(
        f"El campo «{key}» debe ser un número de como máximo ocho cifras enteras.")


def _process_exists(session: Session, key: str, value: int | None, current: int | None) -> None:
    """A ``crud.Field.check`` refusing a process id that does not exist."""
    if value is None or value == current:
        return
    if not people.is_db_id(value) or session.get(ProcesoOperacion, value) is None:
        raise ValidationError(f"El campo «{key}» no corresponde a ningún proceso.")


def _default_year(session: Session, data: Any, found: ObjetivoCalidad | None) -> Any:
    """On create without ``anio``, the year of a valid ``fecha_inicio``."""
    if found is not None or not isinstance(data, Mapping) or "anio" in data:
        return data
    start = data.get("fecha_inicio")
    if isinstance(start, date) and not isinstance(start, datetime):
        return {**data, "anio": start.year}
    return data


OBJECTIVE_SPEC = crud.Spec(
    model=ObjetivoCalidad,
    resource=Resource.QUALITY_OBJECTIVES,
    not_found=OBJECTIVE_NOT_FOUND,
    fields=(
        crud.Field("titulo", partial(fields.text, required=True, max_length=TEXT_MAX),
                   required=True),
        crud.Field("descripcion", partial(fields.text, strip=False)),
        crud.Field("proceso_id", fields.integer, check=_process_exists),
        crud.Field("indicador", partial(fields.text, required=True, max_length=TEXT_MAX),
                   required=True),
        crud.Field("unidad", partial(fields.text, max_length=UNIT_MAX)),
        crud.Field("meta", partial(number, required=True), required=True),
        crud.Field("sentido", partial(fields.enum_member, enum_cls=SentidoObjetivo),
                   required=True),
        crud.Field("valor_inicial", number),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("anio", partial(fields.integer, required=True, minimum=YEAR_MIN,
                                   maximum=YEAR_MAX)),
        crud.Field("fecha_inicio", fields.required_date, required=True),
        crud.Field("fecha_limite", fields.required_date, required=True),
        crud.Field("frecuencia", partial(fields.enum_member, enum_cls=FrecuenciaMedicion),
                   required=True),
        crud.Field("recursos", partial(fields.text, strip=False)),
        crud.Field("evaluacion", partial(fields.text, strip=False)),
        crud.Field("estado", partial(fields.enum_member, enum_cls=EstadoObjetivo)),
    ),
    # Newest year first, then the soonest deadline.
    order_by=(ObjetivoCalidad.anio.desc(), ObjetivoCalidad.fecha_limite, ObjetivoCalidad.id),
    prepare=_default_year,
)

MEASUREMENT_SPEC = crud.Spec(
    model=MedicionObjetivo,
    resource=Resource.QUALITY_OBJECTIVES,
    not_found=MEASUREMENT_NOT_FOUND,
    fields=(
        crud.Field("objetivo_id", partial(fields.integer, required=True), required=True),
        crud.Field("fecha", fields.required_date, required=True),
        crud.Field("valor", partial(number, required=True), required=True),
        crud.Field("comentario", partial(fields.text, strip=False)),
        crud.Field("registrado_por_id", fields.integer, check=people.check_reference),
    ),
    # Each objective's measurements in date order.
    order_by=(MedicionObjetivo.objetivo_id, MedicionObjetivo.fecha, MedicionObjetivo.id),
    conflict=DUPLICATE_DATE,
)

_OBJECTIVE_FIELDS = {f.name: f for f in OBJECTIVE_SPEC.fields}
_MEASUREMENT_FIELDS = {f.name: f for f in MEASUREMENT_SPEC.fields}


def _measurements_foreign_key() -> str:
    """Name of the measurements' foreign key to objectives, as the model declares it."""
    (name,) = {fk.constraint.name for fk in MedicionObjetivo.__table__.foreign_keys
               if fk.column.table is ObjetivoCalidad.__table__}
    return name


MEASUREMENTS_FK = _measurements_foreign_key()


# -- objectives ------------------------------------------------------------------------


def _create_objective(session: Session, actor: Actor, data: Mapping[str, Any]) -> ObjetivoCalidad:
    """Create an objective; it starts ``activo`` unless ``estado`` is given."""
    policy.require(actor, Action.CREATE, Resource.QUALITY_OBJECTIVES)
    _check_period(session, data, None)
    return crud.create(OBJECTIVE_SPEC, session, actor, data)


def _update_objective(session: Session, actor: Actor, objective_id: int,
                      data: Mapping[str, Any]) -> ObjetivoCalidad:
    """Apply the given fields; the target and its direction stay once measured."""
    policy.require(actor, Action.UPDATE, Resource.QUALITY_OBJECTIVES)
    found = _lock_objective(session, objective_id)
    _check_period(session, data, found)
    if _well_formed(OBJECTIVE_SPEC, data):
        changed = [key for key in ("meta", "sentido") if key in data
                   and _OBJECTIVE_FIELDS[key].clean(data, key) != getattr(found, key)]
        if changed and _has_measurements(session, found.id):
            raise ValidationError(TARGET_FIXED)
    return crud.update(OBJECTIVE_SPEC, session, actor, found.id, data)


def _delete_objective(session: Session, actor: Actor, objective_id: int) -> None:
    """Hard-delete an objective without measurements; one with them raises ``Conflict``.

    A measurement recorded after the check meets the foreign key at the flush.
    """
    policy.require(actor, Action.DELETE, Resource.QUALITY_OBJECTIVES)
    found = _lock_objective(session, objective_id)
    if _has_measurements(session, found.id):
        raise Conflict(HAS_MEASUREMENTS)
    try:
        crud.delete(OBJECTIVE_SPEC, session, actor, found.id)
    except Conflict as exc:
        cause = exc.__cause__
        mine = _violated_constraint(cause) == MEASUREMENTS_FK
        raise Conflict(HAS_MEASUREMENTS if mine else DELETE_CONFLICT) from cause


def _check_period(session: Session, data: Any, found: ObjetivoCalidad | None) -> None:
    """Refuse a deadline before the start, or a period leaving out a measurement.

    Runs on the period as the write would leave it, ahead of ``crud``; data it
    cannot judge (malformed, or a create missing a date) is left to ``crud``.
    """
    if not _well_formed(OBJECTIVE_SPEC, data):
        return
    start, end = (fields.required_date(data, key) if key in data else getattr(found, key, None)
                  for key in ("fecha_inicio", "fecha_limite"))
    if start is None or end is None:
        return
    if end < start:
        raise ValidationError(DEADLINE_BEFORE_START)
    if found is None:
        return
    first, last = session.execute(
        select(func.min(MedicionObjetivo.fecha), func.max(MedicionObjetivo.fecha))
        .where(MedicionObjetivo.objetivo_id == found.id)
    ).one()
    if first is not None and (first < start or last > end):
        raise ValidationError(PERIOD_EXCLUDES_MEASUREMENTS.format(first=_day(first),
                                                                  last=_day(last)))


def _objective_conditions(anio: int | None = None, estado: EstadoObjetivo | str | None = None,
                          responsable_id: int | None = None,
                          proceso_id: int | None = None) -> list[Any]:
    """Objective filters; they combine with AND. ``estado`` takes the member or its name."""
    where: list[Any] = []
    for column, value in ((ObjetivoCalidad.anio, anio),
                          (ObjetivoCalidad.responsable_id, responsable_id),
                          (ObjetivoCalidad.proceso_id, proceso_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    if estado:
        try:
            member = fields.enum_member({"estado": estado}, "estado", EstadoObjetivo)
        except ValidationError:
            raise ValidationError(BAD_STATE) from None
        where.append(ObjetivoCalidad.estado == member)
    return where


# -- measurements ----------------------------------------------------------------------


def _create_measurement(session: Session, actor: Actor, data: Mapping[str, Any], *,
                        today: date) -> MedicionObjetivo:
    """Record a measurement of an active objective; ``today`` is the adapter's date."""
    policy.require(actor, Action.CREATE, Resource.QUALITY_OBJECTIVES)
    today = _today(today)
    _reject_unknown(MEASUREMENT_SPEC, data)
    if _well_formed(MEASUREMENT_SPEC, data) and {"objetivo_id", "fecha"} <= set(data):
        objective = _active_objective(
            session, _MEASUREMENT_FIELDS["objetivo_id"].clean(data, "objetivo_id"))
        _check_date(objective, fields.required_date(data, "fecha"), today)
    return crud.create(MEASUREMENT_SPEC, session, actor, data)


def _update_measurement(session: Session, actor: Actor, measurement_id: int,
                        data: Mapping[str, Any], *, today: date) -> MedicionObjetivo:
    """Apply the given fields while the objective is active; the objective never changes."""
    policy.require(actor, Action.UPDATE, Resource.QUALITY_OBJECTIVES)
    today = _today(today)
    _reject_unknown(MEASUREMENT_SPEC, data)
    found = None
    if people.is_db_id(measurement_id):
        found = session.get(MedicionObjetivo, measurement_id)
    if found is None:
        raise NotFound(MEASUREMENT_NOT_FOUND)
    if _well_formed(MEASUREMENT_SPEC, data):
        if ("objetivo_id" in data and _MEASUREMENT_FIELDS["objetivo_id"].clean(
                data, "objetivo_id") != found.objetivo_id):
            raise ValidationError(OBJECTIVE_FIXED)
        if not _unchanged(found, data):  # a no-op is left to ``crud``, which writes nothing
            objective = _active_objective(session, found.objetivo_id)
            if "fecha" in data:
                _check_date(objective, fields.required_date(data, "fecha"), today)
    return crud.update(MEASUREMENT_SPEC, session, actor, found.id, data)


def _unchanged(found: MedicionObjetivo, data: Mapping[str, Any]) -> bool:
    """Whether ``data`` leaves the measurement as it is; invalid data counts as a change.

    Every key of ``data`` is writable: the callers refuse unknown keys first.
    """
    try:
        return all(_MEASUREMENT_FIELDS[key].clean(data, key) == getattr(found, key)
                   for key in data)
    except ValidationError:
        return False


def _delete_measurement(session: Session, actor: Actor, measurement_id: int) -> None:
    """Hard-delete a measurement while its objective is active, keeping an audit snapshot."""
    policy.require(actor, Action.DELETE, Resource.QUALITY_OBJECTIVES)
    found = crud.get(MEASUREMENT_SPEC, session, actor, measurement_id)
    _active_objective(session, found.objetivo_id)
    crud.delete(MEASUREMENT_SPEC, session, actor, found.id)


def _active_objective(session: Session, objective_id: int) -> ObjetivoCalidad:
    """The objective, read with a shared lock (``FOR SHARE``), that takes measurements."""
    found = _locked(session, objective_id, read=True)
    if found is None:
        raise ValidationError(UNKNOWN_OBJECTIVE)
    if found.estado is not EstadoObjetivo.activo:
        raise ValidationError(OBJECTIVE_NOT_ACTIVE)
    return found


def _check_date(objective: ObjetivoCalidad, fecha: date, today: date) -> None:
    """A measurement lies in the objective's period and never after ``today``."""
    if not objective.fecha_inicio <= fecha <= objective.fecha_limite:
        raise ValidationError(OUTSIDE_PERIOD.format(start=_day(objective.fecha_inicio),
                                                    end=_day(objective.fecha_limite)))
    if fecha > today:
        raise ValidationError(IN_THE_FUTURE)


def _measurement_conditions(objetivo_id: int | None = None) -> list[Any]:
    """Measurement filters; they combine with AND."""
    if objetivo_id is None:
        return []
    return [MedicionObjetivo.objetivo_id == objetivo_id
            if people.is_db_id(objetivo_id) else false()]


@dataclass(frozen=True)
class Register:
    """One register, used like a service module (see the module docstring).

    ``SPEC`` is named like the service modules' constant so the MCP registry
    and its tests treat both alike. ``list_`` and ``list_page`` take the
    register's filters as keyword arguments; the measurements' ``create`` and
    ``update`` also take ``today``.
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


@dataclass(frozen=True)
class MeasurementRegister(Register):
    """The measurements' register, which also reads them grouped by objective."""

    def by_objective(self, session: Session, actor: Actor,
                     objective_ids: Iterable[int]) -> dict[int, list[MedicionObjetivo]]:
        """The measurements of each of ``objective_ids`` in date order, in one query.

        Every id given is a key, with an empty list when nothing is measured.
        Read grant of ``QUALITY_OBJECTIVES``.
        """
        policy.require(actor, Action.READ, self.SPEC.resource)
        return _by_objective(session, objective_ids)


objectives = Register(
    SPEC=OBJECTIVE_SPEC,
    conditions=_objective_conditions,
    create=_create_objective,
    update=_update_objective,
    delete=_delete_objective,
)

measurements = MeasurementRegister(
    SPEC=MEASUREMENT_SPEC,
    conditions=_measurement_conditions,
    create=_create_measurement,
    update=_update_measurement,
    delete=_delete_measurement,
)


# -- target, trend and overdue measurements (decisions QO2 and QO3) ---------------------


def meets(value: Decimal | int, meta: Decimal | int, sentido: SentidoObjetivo) -> bool:
    """Whether ``value`` reaches ``meta``: at or above it, or at or below it."""
    return value >= meta if sentido is SentidoObjetivo.mayor_igual else value <= meta


def summary(objective: ObjetivoCalidad, found: Iterable[MedicionObjetivo], *,
            today: date) -> dict[str, Any]:
    """What the measurements ``found`` of ``objective`` say on ``today``.

    - ``ultimo_valor`` and ``ultima_fecha``: the latest measurement (``None``
      without one); ``cumple``: whether its value meets the target.
    - ``tendencia``: ``sube``, ``baja`` or ``estable`` from the measurement
      before the latest to the latest; ``None`` with fewer than two.
    - ``proxima_medicion``: one frequency period after the latest measurement,
      or after ``fecha_inicio`` without one, but never after ``fecha_limite``
      (no measurement can follow it); ``None`` when the objective is not
      ``activo`` or was measured on its deadline.
    - ``medicion_vencida``: ``today`` is after ``proxima_medicion``.
    """
    ordered = sorted(found, key=lambda measurement: measurement.fecha)
    latest = ordered[-1] if ordered else None
    trend = None
    if len(ordered) > 1:
        before, now = ordered[-2].valor, latest.valor
        trend = "sube" if now > before else "baja" if now < before else "estable"
    due = None
    if objective.estado is EstadoObjetivo.activo and (
            latest is None or latest.fecha < objective.fecha_limite):
        base = objective.fecha_inicio if latest is None else latest.fecha
        due = min(_add_months(base, PERIOD_MONTHS[objective.frecuencia]), objective.fecha_limite)
    return {
        "ultimo_valor": None if latest is None else latest.valor,
        "ultima_fecha": None if latest is None else latest.fecha,
        "cumple": None if latest is None else meets(latest.valor, objective.meta,
                                                    objective.sentido),
        "tendencia": trend,
        "medicion_vencida": due is not None and today > due,
        "proxima_medicion": due,
    }


def measurements_of(session: Session, objective_id: int) -> list[MedicionObjetivo]:
    """The measurements of one objective in date order.

    No policy check: for adapters that already read the objective (the MCP
    ``extras`` of an objective record).
    """
    return list(session.scalars(
        select(MedicionObjetivo).where(MedicionObjetivo.objetivo_id == objective_id)
        .order_by(MedicionObjetivo.fecha, MedicionObjetivo.id)))


def measurement_meets(session: Session, measurement: MedicionObjetivo) -> bool:
    """Whether one measurement meets its objective's target (decision QO2); no policy check."""
    objective = session.get(ObjetivoCalidad, measurement.objetivo_id)
    return meets(measurement.valor, objective.meta, objective.sentido)


def off_track(session: Session, actor: Actor, *,
              today: date) -> list[tuple[ObjetivoCalidad, dict[str, Any]]]:
    """Active objectives missing their target or with an overdue measurement (QO3).

    Each comes with its ``summary``, soonest deadline first; ``today`` is the
    adapter's date. Read grant of ``QUALITY_OBJECTIVES``.
    """
    policy.require(actor, Action.READ, Resource.QUALITY_OBJECTIVES)
    today = _today(today)
    active = list(session.scalars(
        select(ObjetivoCalidad).where(ObjetivoCalidad.estado == EstadoObjetivo.activo)
        .order_by(ObjetivoCalidad.fecha_limite, ObjetivoCalidad.id)))
    by_objective = _by_objective(session, [objective.id for objective in active])
    flagged = []
    for objective in active:
        state = summary(objective, by_objective[objective.id], today=today)
        if state["cumple"] is False or state["medicion_vencida"]:
            flagged.append((objective, state))
    return flagged


# -- helpers ---------------------------------------------------------------------------


def _add_months(base: date, months: int) -> date:
    """``base`` plus ``months``, on the month's last day when ``base.day`` does not exist."""
    index = base.month - 1 + months
    year, month = base.year + index // 12, index % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def _by_objective(session: Session,
                  objective_ids: Iterable[int]) -> dict[int, list[MedicionObjetivo]]:
    """The measurements of each objective in date order, from one query (none without ids)."""
    found: dict[int, list[MedicionObjetivo]] = {key: [] for key in objective_ids}
    ids = [key for key in found if people.is_db_id(key)]
    if ids:
        for measurement in session.scalars(
                select(MedicionObjetivo).where(MedicionObjetivo.objetivo_id.in_(ids))
                .order_by(*MEASUREMENT_SPEC.order_by)):
            found[measurement.objetivo_id].append(measurement)
    return found


def _has_measurements(session: Session, objective_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(MedicionObjetivo.objetivo_id == objective_id))))


def _locked(session: Session, objective_id: Any, *, read: bool) -> ObjetivoCalidad | None:
    """The objective loaded afresh with its row locked (shared when ``read``), or ``None``."""
    if not people.is_db_id(objective_id):
        return None
    return session.scalars(
        select(ObjetivoCalidad).where(ObjetivoCalidad.id == objective_id)
        .with_for_update(read=read).execution_options(populate_existing=True)
    ).one_or_none()


def _lock_objective(session: Session, objective_id: Any) -> ObjetivoCalidad:
    """The objective locked for a change (``FOR UPDATE``), or ``NotFound``."""
    found = _locked(session, objective_id, read=False)
    if found is None:
        raise NotFound(OBJECTIVE_NOT_FOUND)
    return found


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


def _day(value: date) -> str:
    return value.strftime("%d/%m/%Y")
