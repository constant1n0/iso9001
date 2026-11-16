"""Draft release and verification writes lock/refresh before validating citations.

Decision columns are deliberately unwritable. MCP creates/reads verifications;
web writers edit/delete them. Services audit and flush, never commit or change orders.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import (ProductRelease, ReleaseVerification, ProductionOrder,
                      ProductionResult, ProductionState, ReleaseStatus, Person)
from . import audit, crud, design, fields, people, policy, production
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

READ_ONLY = 'La liberación decidida es de solo lectura.'
SPEC = crud.Spec(model=ProductRelease, resource=Resource.RELEASE,
    not_found='Liberación no encontrada.', fields=(
        crud.Field('order_id', partial(fields.integer, required=True), required=True),
        crud.Field('notes', fields.text)), order_by=(ProductRelease.id.desc(),),
    conflict='Ya existe una liberación no rechazada para esta orden.')
VERIFICATION_SPEC = crud.Spec(model=ReleaseVerification, resource=Resource.RELEASE,
    not_found='Verificación no encontrada.', fields=(
        *(crud.Field(k, partial(fields.integer, required=True), required=True)
          for k in ('release_id', 'person_id')),
        *(crud.Field(k, partial(fields.text, required=True), required=True)
          for k in ('criterion', 'evidence')),
        crud.Field('result', partial(fields.enum_member, enum_cls=ProductionResult), required=True),
        crud.Field('date', fields.required_date, required=True)),
    order_by=(ReleaseVerification.date, ReleaseVerification.id))


def _lock(session: Session, record_id: int) -> ProductRelease:
    row = session.scalar(select(ProductRelease).where(ProductRelease.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(SPEC.not_found)
    return row


def _editable(row: ProductRelease) -> None:
    if row.status is not ReleaseStatus.draft:
        raise ValidationError(READ_ONLY)


def _values(spec: crud.Spec, session: Session, data: Mapping, row: Any = None) -> dict:
    design._payload(data, spec.writable)
    if row is None:
        fields.require_keys(data, frozenset(f.name for f in spec.fields if f.required))
    return crud._clean(spec, session, data, row)


def _create(session: Session, who: Actor, data: Mapping) -> ProductRelease:
    policy.require(who, Action.CREATE, Resource.RELEASE)
    values = _values(SPEC, session, data)
    # All competing releases serialize on the same order, even before insertion.
    if not people.is_db_id(values['order_id']):
        raise ValidationError('El campo «order_id» no corresponde a ningún registro.')
    try:
        order = production._lock(session, values['order_id'])
    except NotFound as exc:
        raise ValidationError('El campo «order_id» no corresponde a ningún registro.') from exc
    if order.state is not ProductionState.finished:
        raise ValidationError('Elige una orden terminada.')
    if session.scalar(select(ProductRelease.id).where(ProductRelease.order_id == order.id,
            ProductRelease.status != ReleaseStatus.rejected).limit(1)) is not None:
        raise Conflict(SPEC.conflict)
    row = crud.create(SPEC, session, who, values)
    design._flush(session)
    return row


def _update(session: Session, who: Actor, record_id: int, data: Mapping) -> ProductRelease:
    policy.require(who, Action.UPDATE, Resource.RELEASE)
    row = _lock(session, record_id)
    _editable(row)
    values = _values(SPEC, session, data, row)
    if values.get('order_id', row.order_id) != row.order_id:
        raise ValidationError('Una liberación no puede cambiar de orden.')
    production._target(session, ProductionOrder, row.order_id, 'order_id')
    return crud.update(SPEC, session, who, row.id, values)


def _remove(session: Session, who: Actor, row: Any) -> None:
    audit.record(session, who, 'delete', row, before=audit.snapshot(row))
    session.delete(row)
    design._flush(session)


def _delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, Resource.RELEASE)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    for child in session.scalars(select(ReleaseVerification).where(
            ReleaseVerification.release_id == row.id).order_by(ReleaseVerification.id)
            .execution_options(populate_existing=True)):
        _remove(session, who, child)
    _remove(session, who, row)


def _check(session: Session, values: dict, row: ReleaseVerification | None, today: date) -> None:
    if values.get('date', getattr(row, 'date', today)) > today:
        raise ValidationError('La fecha de la verificación no puede ser futura.')
    person_id = values.get('person_id', getattr(row, 'person_id', None))
    production._target(session, Person, person_id, 'person_id')
    people.check_reference(session, 'person_id', person_id, getattr(row, 'person_id', None))


def _child(session: Session, who: Actor, data: Mapping, *, today: date) -> ReleaseVerification:
    policy.require(who, Action.CREATE, Resource.RELEASE)
    values = _values(VERIFICATION_SPEC, session, data)
    if not people.is_db_id(values['release_id']):
        raise ValidationError('El campo «release_id» no corresponde a ningún registro.')
    _editable(_lock(session, values['release_id']))
    _check(session, values, None, today)
    row = crud.create(VERIFICATION_SPEC, session, who, values)
    design._flush(session)
    return row


def _locked_child(session: Session, record_id: int) -> ReleaseVerification:
    row = crud._load(VERIFICATION_SPEC, session, record_id)
    _editable(_lock(session, row.release_id))
    row = session.scalar(select(ReleaseVerification).where(ReleaseVerification.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(VERIFICATION_SPEC.not_found)
    return row


def _child_update(session: Session, who: Actor, record_id: int, data: Mapping, *, today: date) -> ReleaseVerification:
    policy.require(who, Action.UPDATE, Resource.RELEASE)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _locked_child(session, record_id)
    values = _values(VERIFICATION_SPEC, session, data, row)
    if values.get('release_id', row.release_id) != row.release_id:
        raise ValidationError('Una verificación no puede cambiar de liberación.')
    _check(session, values, row, today)
    return crud.update(VERIFICATION_SPEC, session, who, row.id, values)


def _child_delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.UPDATE, Resource.RELEASE)
    if who.channel != 'web':
        raise PermissionDenied()
    _remove(session, who, _locked_child(session, record_id))


def _conditions(status: Any = None, order_id: int | None = None,
                customer_id: int | None = None) -> list:
    where = []
    if status is not None:
        where.append(ProductRelease.status == fields.enum_member({'status': status}, 'status', ReleaseStatus))
    if order_id is not None:
        where.append(ProductRelease.order_id == fields.integer({'order_id': order_id}, 'order_id', required=True))
    if customer_id is not None:
        where.append(ProductRelease.order.has(customer_id=fields.integer(
            {'customer_id': customer_id}, 'customer_id', required=True)))
    return where


def _child_conditions(release_id: int | None = None) -> list:
    return [] if release_id is None else [ReleaseVerification.release_id == fields.integer(
        {'release_id': release_id}, 'release_id', required=True)]


releases = Register(SPEC, _conditions, _create, _update, _delete)
verifications = Register(VERIFICATION_SPEC, _child_conditions, _child, _child_update, _child_delete)
