"""Release drafts and independent web decisions (8.6), never order transitions.

Writers lock/refresh the release; decisions then lock/refresh its order. Decision
columns are never ordinary writable fields. Services audit and flush, never commit.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import (ProductRelease, ReleaseVerification, ProductionOrder,
                      ProductionResult, ProductionState, ReleaseStatus, Person, RoleEnum)
from . import audit, crud, design, design_changes, fields, people, policy, production
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


def _decision(session: Session, who: Actor, record_id: int) -> tuple[ProductRelease, ProductionOrder, int]:
    """Resolve independent active authority after release→order locks and refreshes."""
    policy.require(who, Action.UPDATE, Resource.RELEASE)
    if who.channel != 'web' or who.role is not RoleEnum.ADMINISTRADOR:
        raise PermissionDenied()
    row = _lock(session, record_id)
    _editable(row)
    order = production._lock(session, row.order_id)
    try:
        person_id = design_changes.approval_person(session, who, order)
    except ValidationError as exc:
        if str(exc) == design_changes.SEPARATION:
            raise ValidationError('La decisión requiere una persona distinta del responsable de la orden.') from exc
        raise
    people.check_reference(session, 'decided_by_id', person_id)
    return row, order, person_id


def _record_decision(session: Session, who: Actor, row: ProductRelease,
                     person_id: int, status: ReleaseStatus, today: date,
                     values: dict[str, Any]) -> ProductRelease:
    """Use shared attribution/audit machinery for one immutable decision."""
    day = fields.required_date({'today': today}, 'today')
    before = audit.snapshot(row)
    row.status, row.decision_date = status, day
    row.decided_by_id, row.decided_by_user_id = person_id, who.user_id
    for key, value in values.items():
        setattr(row, key, value)
    design._record_update(session, who, row, before)
    design._flush(session)
    return row


def authorize(session: Session, who: Actor, record_id: int, *, today: date,
              concession: bool = False, concession_reason: str | None = None,
              concession_approver_id: int | None = None,
              customer_approval_date: date | None = None,
              customer_approval_means: str | None = None) -> ProductRelease:
    """Authorize conforming evidence or an explicit, fully recorded concession.

    ``today`` comes from the adapter clock, never the submitted payload. The
    concession approver may also be the deciding person (the pertinent authority).
    """
    row, order, person_id = _decision(session, who, record_id)
    if order.state is not ProductionState.finished:
        raise ValidationError('Elige una orden terminada.')
    checks = list(session.scalars(select(ReleaseVerification).where(
        ReleaseVerification.release_id == row.id)
        .execution_options(populate_existing=True)))
    if not checks:
        raise ValidationError('Añade al menos una verificación antes de autorizar.')
    if not isinstance(concession, bool):
        raise ValidationError('El campo «concession» debe ser verdadero o falso.')
    if not concession and any(c.result is ProductionResult.nonconforming for c in checks):
        raise ValidationError('Hay verificaciones no conformes; registra una concesión para autorizar.')
    values = dict(concession=concession, concession_reason=concession_reason,
        concession_approver_id=concession_approver_id,
        customer_approval_date=customer_approval_date,
        customer_approval_means=customer_approval_means)
    if concession:
        values['concession_reason'] = fields.text(values, 'concession_reason', required=True)
        approver = fields.integer(values, 'concession_approver_id', required=True)
        production._target(session, Person, approver, 'concession_approver_id')
        people.check_reference(session, 'concession_approver_id', approver)
        values['concession_approver_id'] = approver
        means = fields.text(values, 'customer_approval_means')
        if (customer_approval_date is None) != (means is None):
            raise ValidationError('Indica la fecha y el medio de aprobación del cliente, o deja ambos vacíos.')
        if customer_approval_date is not None:
            day = fields.required_date(values, 'customer_approval_date')
            if day > today:
                raise ValidationError('La fecha de aprobación del cliente no puede ser futura.')
        values['customer_approval_means'] = means
    elif any(value is not None and value != '' for key, value in values.items() if key != 'concession'):
        raise ValidationError('Marca «Con concesión» para registrar sus datos.')
    else:
        values = dict.fromkeys(values) | {'concession': False}
    return _record_decision(session, who, row, person_id, ReleaseStatus.released, today, values)


def reject(session: Session, who: Actor, record_id: int, *, reason: str | None,
           today: date) -> ProductRelease:
    """Retain rejection, its reason and independent authority; free the order slot."""
    row, _, person_id = _decision(session, who, record_id)
    reason = fields.text({'reason': reason}, 'reason')
    if not reason:
        raise ValidationError('Indica el motivo del rechazo.')
    return _record_decision(session, who, row, person_id, ReleaseStatus.rejected,
                            today, {'decision_note': reason, 'concession': False})


def _conditions(status: Any = None, order_id: int | None = None,
                customer_id: int | None = None, concession: bool | None = None) -> list:
    where = []
    if concession is not None:
        where.append(ProductRelease.concession.is_(fields.boolean({'concession': concession}, 'concession')))
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
