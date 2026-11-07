"""Equipment suitability from dated, append-only checks (7.1.5).

The equipment row serializes check creation and master edits. The latest check
(date, then id) determines suitability; backdated entries are refused so that
a failure cannot be concealed behind an existing successful check. Failed checks
require impact assessment and actions; only a later success can restore fitness.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy.orm import Session
from .actor import Actor

from sqlalchemy import false, select

from ..models import (EquipoMedicion, ComprobacionEquipo, TipoComprobacionEquipo,
                      ResultadoComprobacionEquipo)
from . import crud, fields, people, policy
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.MEASURING_EQUIPMENT
IMMUTABLE = 'Las comprobaciones no se modifican ni se eliminan; registra una nueva.'
BACKDATED = 'La comprobación no puede ser anterior a la última registrada.'
REPLAYED_SUCCESS = ('La comprobación apta repite una ya registrada; para recuperar el equipo '
                    'registra una nueva comprobación con otra referencia o certificado.')
IMPACT_REQUIRED = 'Un resultado no apto requiere evaluar las mediciones anteriores e indicar las acciones.'
NEXT_REQUIRED = 'Un resultado apto requiere una próxima fecha posterior a la comprobación.'
NO_NEXT = 'Un resultado no apto no admite próxima fecha; registra una nueva comprobación para recuperarlo.'


def _boolean(data: Mapping[str, Any], key: str) -> bool:
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError('El campo «retirado» debe ser verdadero o falso.')
    return value


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    return None if data[key] is None else fields.required_date(data, key)


EQUIPMENT_SPEC = crud.Spec(
    model=EquipoMedicion, resource=RESOURCE, not_found='Equipo no encontrado.',
    fields=(
        crud.Field('codigo', partial(fields.text, required=True, max_length=50), required=True),
        crud.Field('nombre', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('numero_serie', partial(fields.text, max_length=100)),
        crud.Field('ubicacion', partial(fields.text, max_length=200)),
        crud.Field('uso_previsto', fields.text),
        crud.Field('responsable_id', fields.integer, check=people.check_reference),
        crud.Field('retirado', _boolean),
    ),
    order_by=(EquipoMedicion.codigo, EquipoMedicion.id),
    conflict='Ya existe un equipo con ese código.',
)
CHECK_SPEC = crud.Spec(
    model=ComprobacionEquipo, resource=RESOURCE, not_found='Comprobación no encontrada.',
    fields=(
        crud.Field('equipo_id', partial(fields.integer, required=True), required=True),
        crud.Field('fecha', fields.required_date, required=True),
        crud.Field('tipo', partial(fields.enum_member, enum_cls=TipoComprobacionEquipo), required=True),
        crud.Field('resultado', partial(fields.enum_member, enum_cls=ResultadoComprobacionEquipo), required=True),
        crud.Field('realizado_por', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('referencia', partial(fields.text, required=True), required=True),
        crud.Field('certificado', partial(fields.text, max_length=200)),
        crud.Field('fecha_proxima', _optional_date),
        crud.Field('evaluacion_impacto', fields.text),
        crud.Field('acciones', fields.text),
    ),
    order_by=(ComprobacionEquipo.fecha.desc(), ComprobacionEquipo.id.desc()),
)


def _check_evidence(data: Mapping[str, Any]) -> tuple[Any, ...]:
    """Compare only user-supplied evidence, with the register's normalization."""
    return tuple(field.clean({field.name: data.get(field.name)}, field.name)
                 for field in CHECK_SPEC.fields if field.name != 'equipo_id')


def _lock(session: Session, equipment_id: int) -> EquipoMedicion:
    if not people.is_db_id(equipment_id):
        raise ValidationError('El campo «equipo_id» no corresponde a ningún equipo.')
    found = session.scalar(select(EquipoMedicion).where(
        EquipoMedicion.id == equipment_id).with_for_update()
        .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(EQUIPMENT_SPEC.not_found)
    return found


def latest(session: Session, equipment_id: int) -> ComprobacionEquipo | None:
    """Most recent immutable check; equal dates use insertion order."""
    return session.scalar(select(ComprobacionEquipo).where(
        ComprobacionEquipo.equipo_id == equipment_id).order_by(
        *CHECK_SPEC.order_by).limit(1))


def status(session: Session, item: EquipoMedicion, *, today: date) -> str:
    """Read-only suitability; a due date is valid through that entire day."""
    check = None if item.retirado else latest(session, item.id)
    return status_from_check(item, check, today=today)


def status_from_check(item: EquipoMedicion, check: ComprobacionEquipo | None,
                      *, today: date) -> str:
    """Derive suitability without reading another check or changing stored data."""
    if item.retirado:
        return 'retirado'
    if check is None:
        return 'sin_comprobar'
    if check.resultado is ResultadoComprobacionEquipo.no_apto:
        return 'no_apto'
    return 'vencido' if check.fecha_proxima < today else 'apto'


def _update_equipment(session: Session, actor: Actor, equipment_id: int,
                      data: Mapping[str, Any]) -> EquipoMedicion:
    policy.require(actor, Action.UPDATE, RESOURCE)
    _lock(session, equipment_id)
    return crud.update(EQUIPMENT_SPEC, session, actor, equipment_id, data)


def _delete_equipment(session: Session, actor: Actor, equipment_id: int) -> None:
    policy.require(actor, Action.DELETE, RESOURCE)
    _lock(session, equipment_id)
    if latest(session, equipment_id) is not None:
        raise Conflict('No se puede eliminar un equipo con comprobaciones; retíralo en su lugar.')
    crud.delete(EQUIPMENT_SPEC, session, actor, equipment_id)


def _create_check(session: Session, actor: Actor, data: Mapping[str, Any],
                  *, today: date) -> ComprobacionEquipo:
    policy.require(actor, Action.CREATE, RESOURCE)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    # Validate every field before acquiring the lock or adding audited rows.
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset(f.name for f in CHECK_SPEC.fields if f.required))
    values = crud._clean(CHECK_SPEC, session, data)
    if values['fecha'] > today:
        raise ValidationError('La fecha de comprobación no puede ser futura.')
    failed = values['resultado'] is ResultadoComprobacionEquipo.no_apto
    if failed and not all(values.get(key) for key in ('evaluacion_impacto', 'acciones')):
        raise ValidationError(IMPACT_REQUIRED)
    following = values.get('fecha_proxima')
    if failed and following is not None:
        raise ValidationError(NO_NEXT)
    if not failed and (following is None or following <= values['fecha']):
        raise ValidationError(NEXT_REQUIRED)
    try:
        item = _lock(session, values['equipo_id'])
    except NotFound as exc:
        raise ValidationError('El campo «equipo_id» no corresponde a ningún equipo.') from exc
    previous = latest(session, item.id)
    if previous is not None and values['fecha'] < previous.fecha:
        raise ValidationError(BACKDATED)
    if previous is not None and previous.resultado is ResultadoComprobacionEquipo.no_apto and not failed:
        evidence = _check_evidence(values)
        # The equipment lock also serializes this search and the append below.
        candidates = session.scalars(select(ComprobacionEquipo).where(
            ComprobacionEquipo.equipo_id == item.id,
            ComprobacionEquipo.resultado == ResultadoComprobacionEquipo.apto,
            ComprobacionEquipo.fecha == values['fecha']))
        for candidate in candidates:
            recorded = {field.name: getattr(candidate, field.name) for field in CHECK_SPEC.fields}
            if _check_evidence(recorded) == evidence:
                raise ValidationError(REPLAYED_SUCCESS)
    return crud.create(CHECK_SPEC, session, actor, values)


def _immutable_update(session: Session, actor: Actor, record_id: int,
                      data: Mapping[str, Any], *, today: date) -> ComprobacionEquipo:
    policy.require(actor, Action.UPDATE, RESOURCE)
    crud.get(CHECK_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _immutable_delete(session: Session, actor: Actor, record_id: int) -> None:
    policy.require(actor, Action.DELETE, RESOURCE)
    crud.get(CHECK_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _equipment_conditions(codigo: str | None = None,
                          retirado: bool | None = None) -> list[Any]:
    where = []
    if codigo:
        where.append(EquipoMedicion.codigo.ilike(f'%{codigo}%'))
    if retirado is not None:
        if not isinstance(retirado, bool):
            raise ValidationError('El filtro «retirado» debe ser verdadero o falso.')
        where.append(EquipoMedicion.retirado.is_(retirado))
    return where


def _check_conditions(equipo_id: int | None = None,
                      resultado: str | ResultadoComprobacionEquipo | None = None) -> list[Any]:
    where = []
    if equipo_id is not None:
        where.append(ComprobacionEquipo.equipo_id == equipo_id if people.is_db_id(equipo_id) else false())
    if resultado is not None:
        value = fields.enum_member({'resultado': resultado}, 'resultado', ResultadoComprobacionEquipo)
        where.append(ComprobacionEquipo.resultado == value)
    return where


equipment = Register(EQUIPMENT_SPEC, _equipment_conditions,
                     partial(crud.create, EQUIPMENT_SPEC), _update_equipment, _delete_equipment)
checks = Register(CHECK_SPEC, _check_conditions,
                  _create_check, _immutable_update, _immutable_delete)
