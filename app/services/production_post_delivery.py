"""PS5 post-delivery obligations: dated outcomes never change or release orders.

Creation owns its new row; every later write locks and refreshes the activity
before refreshing citations. Terminal records are read-only. Lifecycle and
unlinked-pending deletion are web-only. Services flush and audit, never commit.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import (Cliente, Person, ProductionOrder, ProductionState, Reclamacion,
    NoConformidad, PostDeliveryActivity, PostDeliveryKind, PostDeliveryStatus)
from . import audit, crud, design, fields, people, policy, production, production_property
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

READ_ONLY = 'La actividad realizada o anulada es de solo lectura.'
SPEC = crud.Spec(model=PostDeliveryActivity, resource=Resource.PRODUCTION,
    not_found='Actividad de posventa no encontrada.', fields=(
        crud.Field('description', partial(fields.text, required=True), required=True),
        crud.Field('kind', partial(fields.enum_member, enum_cls=PostDeliveryKind), required=True),
        crud.Field('source', fields.text),
        crud.Field('responsible_id', partial(fields.integer, required=True), required=True),
        crud.Field('due_date', fields.required_date, required=True),
        *(crud.Field(key, fields.integer) for key in ('customer_id', 'complaint_id', 'nonconformity_id')),
        crud.Field('order_ids', production_property._ids)),
    order_by=(PostDeliveryActivity.due_date, PostDeliveryActivity.id))


def _lock(session: Session, record_id: int) -> PostDeliveryActivity:
    row = session.scalar(select(PostDeliveryActivity).where(PostDeliveryActivity.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(SPEC.not_found)
    session.expire(row, ['orders'])
    return row


def _editable(row: PostDeliveryActivity) -> None:
    if row.status is not PostDeliveryStatus.pending:
        raise ValidationError(READ_ONLY)


def _snapshot(row: PostDeliveryActivity) -> dict:
    return audit.snapshot(row) | {'order_ids': row.order_ids}


def _clean(session: Session, data: Mapping, row: PostDeliveryActivity | None) -> tuple:
    design._payload(data, SPEC.writable)
    values = crud._clean(SPEC, session, data, row)
    for key, model in (('responsible_id', Person), ('customer_id', Cliente),
                       ('complaint_id', Reclamacion), ('nonconformity_id', NoConformidad)):
        value = values.get(key, getattr(row, key, None))
        if value is not None:
            production._target(session, model, value, key)
            if key == 'responsible_id':
                people.check_reference(session, key, value, getattr(row, key, None))
    ids = values.pop('order_ids', getattr(row, 'order_ids', []))
    orders = []
    for value in ids:
        target = production._target(session, ProductionOrder, value, 'order_ids')
        if value not in getattr(row, 'order_ids', []) and target.state is ProductionState.cancelled:
            raise ValidationError('Elige órdenes no canceladas para los nuevos vínculos.')
        orders.append(target)
    return values, orders


def _create(session: Session, who: Actor, data: Mapping, *, today: date) -> PostDeliveryActivity:
    policy.require(who, Action.CREATE, Resource.PRODUCTION)
    design._payload(data, SPEC.writable)
    fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values, orders = _clean(session, data, None)
    row = PostDeliveryActivity(**values, orders=orders)
    stamp_created(row, who)
    session.add(row)
    log = audit.record(session, who, 'create', row)
    log.after = _snapshot(row)
    design._flush(session)
    return _lock(session, row.id)


def _record(session: Session, who: Actor, row: PostDeliveryActivity, before: dict) -> None:
    stamp_updated(row, who)
    audit.record(session, who, 'update', row, before=before, after=_snapshot(row))
    design._flush(session)


def _update(session: Session, who: Actor, record_id: int, data: Mapping, *, today: date) -> PostDeliveryActivity:
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    row = _lock(session, record_id)
    _editable(row)
    values, orders = _clean(session, data, row)
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
               result_date: date | None = None, result: str | None = None,
               evidence: str | None = None, reason: str | None = None) -> PostDeliveryActivity:
    """Complete with evidence or cancel with reason, web-only; neither releases product."""
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    state = fields.enum_member({'target': target}, 'target', PostDeliveryStatus)
    day = fields.required_date({'result_date': result_date}, 'result_date')
    if state is PostDeliveryStatus.pending or day > today:
        raise ValidationError('Elige realizada o anulada con fecha no futura.')
    values = {'result_date': day, 'status': state}
    for key, value in (('result', result), ('evidence', evidence), ('reason', reason)):
        required = (state is PostDeliveryStatus.completed) == (key != 'reason')
        values[key] = fields.text({key: value}, key, required=required)
        if not required and values[key] is not None:
            raise ValidationError('Indica resultado y evidencia solo al realizar; motivo solo al anular.')
    _clean(session, {}, row)
    before = _snapshot(row)
    for key, value in values.items():
        setattr(row, key, value)
    _record(session, who, row, before)
    return row


def _delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, Resource.PRODUCTION)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    _clean(session, {}, row)
    if row.order_ids:
        raise ValidationError('Solo puedes eliminar actividades pendientes sin órdenes vinculadas.')
    audit.record(session, who, 'delete', row, before=_snapshot(row))
    session.delete(row)
    design._flush(session)


def is_overdue(row: PostDeliveryActivity, *, today: date) -> bool:
    """Pending and due strictly before the adapter's day; never stored."""
    return row.status is PostDeliveryStatus.pending and row.due_date < today


def _conditions(*, today: date, status: Any = None, kind: Any = None,
                customer_id: int | None = None, responsible_id: int | None = None,
                overdue: bool | None = None) -> list:
    where = []
    for key, value, members in (('status', status, PostDeliveryStatus), ('kind', kind, PostDeliveryKind)):
        if value is not None:
            where.append(getattr(PostDeliveryActivity, key) == fields.enum_member({key: value}, key, members))
    for key, value in (('customer_id', customer_id), ('responsible_id', responsible_id)):
        if value is not None:
            where.append(getattr(PostDeliveryActivity, key) == fields.integer({key: value}, key, required=True))
    if overdue is not None:
        flag = fields.boolean({'overdue': overdue}, 'overdue')
        condition = (PostDeliveryActivity.status == PostDeliveryStatus.pending) & (PostDeliveryActivity.due_date < today)
        where.append(condition if flag else ~condition)
    return where


activities = Register(SPEC, _conditions, _create, _update, _delete)
