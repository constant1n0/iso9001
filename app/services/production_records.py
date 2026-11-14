"""PS2 append-only evidence; lock the order before validating refreshed citations.

Equipment uses the latest check on/before the occurrence date, including same-day
id tie-breaking, and the current retirement flag (retirement has no dated history).
Services reuse CRUD attribution/audit and flush without committing. Completion is
not gated by evidence and never authorizes release (8.6).
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (ComprobacionEquipo, EquipoMedicion, NoConformidad, Person,
                      PreservationAspect, ProductionControlKind, ProductionControlRecord,
                      ProductionPreservationRecord, ProductionResult, ProductionState)
from . import crud, design, equipment, fields, people, policy, production
from .actor import Actor
from .errors import ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

IMMUTABLE = 'Los registros no se modifican ni se eliminan; registra uno nuevo.'
ACTIONS_REQUIRED = 'Indica una no conformidad o las acciones para un resultado no conforme.'


def _spec(model: type, selector: str, members: type, *, control: bool = False) -> crud.Spec:
    """Describe common evidence with type-specific fields, newest occurrences first."""
    return crud.Spec(model=model, resource=Resource.PRODUCTION,
        not_found='Registro no encontrado.', fields=(
            *(crud.Field(key, partial(fields.integer, required=True), required=True)
              for key in ('order_id', 'responsible_id')),
            crud.Field(selector, partial(fields.enum_member, enum_cls=members), required=True),
            crud.Field('date', fields.required_date, required=True),
            crud.Field('description', partial(fields.text, required=True), required=True),
            crud.Field('result', partial(fields.enum_member, enum_cls=ProductionResult), required=True),
            crud.Field('nonconformity_id', fields.integer),
            *((crud.Field('evidence', partial(fields.text, required=True), required=True),
               crud.Field('equipment_id', fields.integer), crud.Field('actions', fields.text))
              if control else ()),
        ), order_by=(model.date.desc(), model.id.desc()))


CONTROL_SPEC = _spec(ProductionControlRecord, 'kind', ProductionControlKind, control=True)
PRESERVATION_SPEC = _spec(ProductionPreservationRecord, 'aspect', PreservationAspect)


def allowed(spec: crud.Spec, state: ProductionState) -> bool:
    """Execution requires in-progress work; preservation also permits finished work."""
    return state is ProductionState.in_progress or (
        spec is PRESERVATION_SPEC and state is ProductionState.finished)


def _create(spec: crud.Spec, session: Session, who: Actor, data: Mapping[str, Any], *,
            today: date) -> Any:
    """Append validated evidence after acquiring the shared PS1 lifecycle lock."""
    policy.require(who, Action.CREATE, Resource.PRODUCTION)
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset({'order_id'}))
    order_id = fields.integer(data, 'order_id', required=True)
    if not people.is_db_id(order_id):
        raise ValidationError('El campo «order_id» no corresponde a ningún registro.')
    try:
        order = production._lock(session, order_id)
    except production.NotFound as exc:
        raise ValidationError('El campo «order_id» no corresponde a ningún registro.') from exc
    design._payload(data, spec.writable)
    fields.require_keys(data, frozenset(f.name for f in spec.fields if f.required))
    if not allowed(spec, order.state):
        raise ValidationError('El estado de la orden no admite este registro.')
    values = crud._clean(spec, session, data)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    if values['date'] > today:
        raise ValidationError('La fecha del registro no puede ser futura.')
    for key, model in (('responsible_id', Person), ('nonconformity_id', NoConformidad),
                       ('equipment_id', EquipoMedicion)):
        value = values.get(key)
        if value is None:
            continue
        target = production._target(session, model, value, key)
        if key == 'responsible_id':
            people.check_reference(session, key, value)
        if key == 'equipment_id':
            check = session.scalar(select(ComprobacionEquipo).where(
                ComprobacionEquipo.equipo_id == value,
                ComprobacionEquipo.fecha <= values['date']).order_by(
                    *equipment.CHECK_SPEC.order_by).limit(1)
                .execution_options(populate_existing=True))
            if equipment.status_from_check(target, check, today=values['date']) != 'apto':
                raise ValidationError('Elige un equipo apto en la fecha del registro.')
    if (spec is CONTROL_SPEC and values['result'] is ProductionResult.nonconforming
            and not values.get('nonconformity_id') and not values.get('actions')):
        raise ValidationError(ACTIONS_REQUIRED)
    return crud.create(spec, session, who, values)


def _immutable(spec: crud.Spec, action: Action, session: Session, who: Actor,
               record_id: int, data: Any = None, *, today: date | None = None) -> None:
    """Refuse even empty updates and administrator deletes on every channel."""
    policy.require(who, action, Resource.PRODUCTION)
    crud.get(spec, session, who, record_id)
    raise ValidationError(IMMUTABLE)


def _conditions(spec: crud.Spec, order_id: int | None = None, kind: Any = None,
                aspect: Any = None, result: Any = None) -> list[Any]:
    """Combine typed order, kind/aspect and result selectors with AND."""
    where = []
    for key, value, members in (('order_id', order_id, None),
        ('kind', kind, ProductionControlKind), ('aspect', aspect, PreservationAspect),
        ('result', result, ProductionResult)):
        if value is not None:
            if key not in spec.writable:
                raise ValidationError('El filtro no corresponde a este registro.')
            cleaned = (fields.integer({key: value}, key, required=True) if members is None
                       else fields.enum_member({key: value}, key, members))
            where.append(getattr(spec.model, key) == cleaned)
    return where


def _register(spec: crud.Spec) -> Register:
    """Bind shared immutable operations to each evidence type."""
    return Register(spec, partial(_conditions, spec), partial(_create, spec),
                    partial(_immutable, spec, Action.UPDATE),
                    partial(_immutable, spec, Action.DELETE))


controls = _register(CONTROL_SPEC)
preservation = _register(PRESERVATION_SPEC)
