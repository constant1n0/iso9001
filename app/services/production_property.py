"""PS4 custody and append-only notified incidents; never product release authority.

Terminal items are read-only, but accept incidents. Transitions and deletion are
web-only. Writes lock/refresh the item before refreshing all cited targets.
Creation owns its new row. Services flush and audit, never commit.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import (Cliente, Proveedor, Person, ProductionOrder, ProductionState,
    CustomerProperty, PropertyIncident, PropertyStatus, PropertyIncidentKind, Reclamacion,
    NoConformidad)
from . import audit, crud, design, fields, people, policy, production, production_records
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

READ_ONLY = 'La propiedad devuelta o dada de baja es de solo lectura.'


def _ids(data: Mapping, key: str) -> list[int]:
    values = data[key]
    if not isinstance(values, (list, tuple)) or any(not people.is_db_id(i) for i in values):
        raise ValidationError('Indica una lista de órdenes existentes.')
    return sorted(set(values))


SPEC = crud.Spec(model=CustomerProperty, resource=Resource.PRODUCTION,
    not_found='Propiedad no encontrada.', fields=(
        *(crud.Field(key, partial(fields.text, required=True), required=True)
          for key in ('identification', 'description', 'location', 'condition')),
        crud.Field('reference', fields.text),
        crud.Field('received_date', fields.required_date, required=True),
        crud.Field('responsible_id', partial(fields.integer, required=True), required=True),
        *(crud.Field(key, fields.integer) for key in ('customer_id', 'provider_id')),
        crud.Field('order_ids', _ids)), order_by=(CustomerProperty.id.desc(),))
INCIDENT_SPEC = crud.Spec(model=PropertyIncident, resource=Resource.PRODUCTION,
    not_found='Incidencia no encontrada.', fields=(
        crud.Field('item_id', partial(fields.integer, required=True), required=True),
        *(crud.Field(key, fields.required_date, required=True) for key in ('date', 'notification_date')),
        crud.Field('kind', partial(fields.enum_member, enum_cls=PropertyIncidentKind), required=True),
        *(crud.Field(key, partial(fields.text, required=True), required=True)
          for key in ('description', 'notification_means')),
        crud.Field('owner_notified', fields.boolean, required=True),
        *(crud.Field(key, fields.integer) for key in ('complaint_id', 'nonconformity_id'))),
    order_by=(PropertyIncident.date.desc(), PropertyIncident.id.desc()))


def _lock(session: Session, record_id: int) -> CustomerProperty:
    row = session.scalar(select(CustomerProperty).where(CustomerProperty.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(SPEC.not_found)
    session.expire(row, ['orders'])
    return row


def _editable(row: CustomerProperty) -> None:
    if row.status is not PropertyStatus.custody:
        raise ValidationError(READ_ONLY)


def _snapshot(row: CustomerProperty) -> dict:
    return audit.snapshot(row) | {'order_ids': row.order_ids}


def _citations(session: Session, values: dict, row: CustomerProperty | None) -> None:
    for key, model in (('customer_id', Cliente), ('provider_id', Proveedor), ('responsible_id', Person)):
        value = values.get(key, getattr(row, key, None))
        if value is not None:
            production._target(session, model, value, key)
            if key == 'responsible_id':
                people.check_reference(session, key, value, getattr(row, key, None))


def _clean(session: Session, data: Mapping, row: CustomerProperty | None, today: date) -> tuple:
    values = crud._clean(SPEC, session, data, row)
    customer = values.get('customer_id', getattr(row, 'customer_id', None))
    provider = values.get('provider_id', getattr(row, 'provider_id', None))
    if (customer is None) == (provider is None):
        raise ValidationError('Elige exactamente un dueño: cliente o proveedor.')
    if values.get('received_date', getattr(row, 'received_date', today)) > today:
        raise ValidationError('La fecha de recepción no puede ser futura.')
    _citations(session, values, row)
    ids = values.pop('order_ids', getattr(row, 'order_ids', []))
    orders = []
    for value in ids:
        target = production._target(session, ProductionOrder, value, 'order_ids')
        if value not in getattr(row, 'order_ids', []) and target.state is ProductionState.cancelled:
            raise ValidationError('Elige órdenes no canceladas para los nuevos vínculos.')
        orders.append(target)
    return values, orders


def _create(session: Session, who: Actor, data: Mapping, *, today: date) -> CustomerProperty:
    policy.require(who, Action.CREATE, Resource.PRODUCTION)
    design._payload(data, SPEC.writable)
    fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values, orders = _clean(session, data, None, today)
    row = CustomerProperty(**values, orders=orders)
    stamp_created(row, who)
    session.add(row)
    log = audit.record(session, who, 'create', row)
    log.after = _snapshot(row)
    design._flush(session)
    return _lock(session, row.id)


def _record(session: Session, who: Actor, row: CustomerProperty, before: dict) -> None:
    stamp_updated(row, who)
    audit.record(session, who, 'update', row, before=before, after=_snapshot(row))
    design._flush(session)


def _update(session: Session, who: Actor, record_id: int, data: Mapping, *, today: date) -> CustomerProperty:
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    row = _lock(session, record_id)
    _editable(row)
    values, orders = _clean(session, data, row, today)
    changes = {key: value for key, value in values.items() if getattr(row, key) != value}
    if row.order_ids != [o.id for o in orders]:
        changes['orders'] = orders
    if changes:
        before = _snapshot(row)
        for key, value in changes.items():
            setattr(row, key, value)
        _record(session, who, row, before)
    return row


def transition(session: Session, who: Actor, record_id: int, target: str, *, today: date,
               terminal_date: date | None = None, terminal_note: str | None = None) -> CustomerProperty:
    """Return or dispose on the web with date and reason, never release orders."""
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    state = fields.enum_member({'target': target}, 'target', PropertyStatus)
    day = fields.required_date({'terminal_date': terminal_date}, 'terminal_date')
    note = fields.text({'terminal_note': terminal_note}, 'terminal_note', required=True)
    if state is PropertyStatus.custody or day > today:
        raise ValidationError('Elige devolución o baja con fecha no futura.')
    _clean(session, {}, row, today)
    before = _snapshot(row)
    row.status, row.terminal_date, row.terminal_note = state, day, note
    _record(session, who, row, before)
    return row


def _delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, Resource.PRODUCTION)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    _citations(session, {}, row)
    if row.order_ids or session.scalar(select(PropertyIncident.id).where(PropertyIncident.item_id == row.id).limit(1)):
        raise ValidationError('Solo puedes eliminar propiedad en custodia sin incidencias ni órdenes vinculadas.')
    audit.record(session, who, 'delete', row, before=_snapshot(row))
    session.delete(row)
    design._flush(session)


def _incident(session: Session, who: Actor, data: Mapping, *, today: date) -> PropertyIncident:
    policy.require(who, Action.CREATE, Resource.PRODUCTION)
    design._payload(data, INCIDENT_SPEC.writable)
    fields.require_keys(data, frozenset(f.name for f in INCIDENT_SPEC.fields if f.required))
    values = crud._clean(INCIDENT_SPEC, session, data)
    _lock(session, values['item_id'])
    if values['date'] > today or not values['date'] <= values['notification_date'] <= today:
        raise ValidationError('La notificación debe ser posterior o igual a la incidencia; ninguna fecha puede ser futura.')
    if values['owner_notified'] is not True:
        raise ValidationError('Registra la notificación al dueño antes de guardar la incidencia.')
    for key, model in (('complaint_id', Reclamacion), ('nonconformity_id', NoConformidad)):
        if values.get(key) is not None:
            production._target(session, model, values[key], key)
    return crud.create(INCIDENT_SPEC, session, who, values)


def _conditions(status: Any = None, owner_type: str | None = None,
                customer_id: int | None = None, provider_id: int | None = None) -> list:
    where = []
    if status is not None:
        where.append(CustomerProperty.status == fields.enum_member({'status': status}, 'status', PropertyStatus))
    if owner_type is not None:
        if owner_type not in ('customer', 'provider'):
            raise ValidationError('Elige cliente o proveedor.')
        where.append(getattr(CustomerProperty, f'{owner_type}_id').is_not(None))
    for key, value in (('customer_id', customer_id), ('provider_id', provider_id)):
        if value is not None:
            where.append(getattr(CustomerProperty, key) == fields.integer({key: value}, key, required=True))
    return where


def _incident_conditions(item_id: int | None = None) -> list:
    return [] if item_id is None else [PropertyIncident.item_id == fields.integer({'item_id': item_id}, 'item_id', required=True)]


items = Register(SPEC, _conditions, _create, _update, _delete)
incidents = Register(INCIDENT_SPEC, _incident_conditions, _incident,
    partial(production_records._immutable, INCIDENT_SPEC, Action.UPDATE),
    partial(production_records._immutable, INCIDENT_SPEC, Action.DELETE))
