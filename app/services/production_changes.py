"""PS3: reviewed changes, not order gates or release authority (8.5.6, not 8.6).

Decisions serialize on the change, then every affected order in id order. New
citations and authorization re-read targets after those locks. Finished orders
are eligible, cancelled ones are not. No change is deleted. Services never commit.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (CambioSGC, DesignChange, DesignChangeState as State,
                      ProductionChange, ProductionOrder, ProductionState, RoleEnum)
from . import audit, crud, design, design_changes, fields, people, policy, production
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

INVALID_ORDERS = 'Elige al menos una orden existente que no esté cancelada.'
INVALID_DESIGN = 'Elige un cambio del diseño autorizado.'
SEPARATION = 'La autorización requiere una persona distinta de todos los responsables de las órdenes.'
NO_DELETE = 'Los cambios de producción no se eliminan; recházalos con un motivo.'
CITATIONS = {'design_change_id': DesignChange, 'qms_change_id': CambioSGC}


def _ids(data: Mapping[str, Any], key: str) -> list[int]:
    values = data[key]
    if (not isinstance(values, (list, tuple)) or not values
            or any(not people.is_db_id(i) for i in values)):
        raise ValidationError(INVALID_ORDERS)
    return sorted(set(values))


SPEC = crud.Spec(model=ProductionChange, resource=Resource.PRODUCTION,
    not_found='Cambio de producción no encontrado.', fields=(
        *(crud.Field(key, partial(fields.text, required=True), required=True)
          for key in ('reason', 'description', 'actions')),
        crud.Field('review', fields.text),
        *(crud.Field(key, fields.integer) for key in CITATIONS),
        crud.Field('order_ids', _ids, required=True),
    ), order_by=(ProductionChange.id.desc(),))


def _snapshot(row: ProductionChange) -> dict:
    return audit.snapshot(row) | {'order_ids': row.order_ids}


def _proposed(row: ProductionChange) -> None:
    if row.state is not State.proposed:
        raise ValidationError(design_changes.IMMUTABLE)


def _locked(session: Session, record_id: int) -> ProductionChange:
    row = session.scalar(select(ProductionChange).where(ProductionChange.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(SPEC.not_found)
    session.expire(row, ['orders'])
    _proposed(row)
    return row


def _orders(session: Session, ids: list[int], *,
            eligible: list[int] | None = None) -> list[ProductionOrder]:
    """Lock every id in order; only ``eligible`` ones (all by default) must not be cancelled."""
    # Reuse PS1's lock/refresh path and its association-expiry invariants.
    rows = []
    for record_id in sorted(set(ids)):
        try:
            rows.append(production._lock(session, record_id))
        except NotFound as exc:
            raise ValidationError(INVALID_ORDERS) from exc
    checked = rows if eligible is None else [row for row in rows if row.id in set(eligible)]
    if not rows or any(row.state is ProductionState.cancelled for row in checked):
        raise ValidationError(INVALID_ORDERS)
    return rows


def _citations(session: Session, values: dict, row: ProductionChange | None) -> None:
    for key, model in CITATIONS.items():
        value = values.get(key, getattr(row, key, None))
        if value is not None:
            target = production._target(session, model, value, key)
            if key == 'design_change_id' and target.state is not State.authorized:
                raise ValidationError(INVALID_DESIGN)


def _record(session: Session, who: Actor, row: ProductionChange, before: dict) -> None:
    stamp_updated(row, who)
    audit.record(session, who, 'update', row, before=before, after=_snapshot(row))
    design._flush(session)


def _create(session: Session, who: Actor, data: Mapping[str, Any]) -> ProductionChange:
    policy.require(who, Action.CREATE, Resource.PRODUCTION)
    design._payload(data, SPEC.writable)
    fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values = crud._clean(SPEC, session, data)
    orders = _orders(session, values.pop('order_ids'))
    _citations(session, values, None)
    row = ProductionChange(**values, orders=orders)
    stamp_created(row, who)
    session.add(row)
    log = audit.record(session, who, 'create', row)
    log.after = _snapshot(row)
    design._flush(session)
    return row


def _update(session: Session, who: Actor, record_id: int,
            data: Mapping[str, Any]) -> ProductionChange:
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    row = _locked(session, record_id)
    values = crud._clean(SPEC, session, data, row)
    ids = values.pop('order_ids', row.order_ids)
    if not ids:
        raise ValidationError(INVALID_ORDERS)
    # Lock removed and retained orders alike, but a cancelled order may be removed.
    locked = _orders(session, sorted(set(ids) | set(row.order_ids)), eligible=ids)
    _citations(session, values, row)
    linked = [order for order in locked if order.id in ids]
    changes = {key: value for key, value in values.items() if getattr(row, key) != value}
    if ids != row.order_ids:
        changes['orders'] = linked
    if changes:
        before = _snapshot(row)
        for key, value in changes.items():
            setattr(row, key, value)
        _record(session, who, row, before)
    return row


def _web_admin(who: Actor) -> None:
    policy.require(who, Action.UPDATE, Resource.PRODUCTION)
    if who.channel != 'web' or who.role is not RoleEnum.ADMINISTRADOR:
        raise PermissionDenied()


def may_decide(session: Session, who: Actor, row: ProductionChange) -> bool:
    """UI hint only; decisions recheck identity under the sorted order locks."""
    person_id = people.of_user(session, who.user_id)
    return (who.channel == 'web' and who.role is RoleEnum.ADMINISTRADOR
        and row.state is State.proposed and person_id is not None
        and all(order.owner_id != person_id for order in row.orders))


def _decision(session: Session, who: Actor, record_id: int, *,
              rejecting: bool = False) -> tuple[ProductionChange, int]:
    _web_admin(who)
    row = _locked(session, record_id)
    # Rejecting a proposal whose order was cancelled must stay possible.
    orders = _orders(session, row.order_ids, eligible=[] if rejecting else None)
    _citations(session, {}, row)
    try:
        # Reuse linked-user resolution, person share lock and post-lock refresh.
        for order in orders:
            person_id = design_changes.approval_person(session, who, order)
    except ValidationError as exc:
        if str(exc) == design_changes.SEPARATION:
            raise ValidationError(SEPARATION) from exc
        raise
    return row, person_id


def authorize(session: Session, who: Actor, record_id: int, *, today: date) -> ProductionChange:
    """Record independent web authorization after reviewing results, never release."""
    row, person_id = _decision(session, who, record_id)
    if not row.review or not row.review.strip():
        raise ValidationError('Revisa el cambio antes de autorizarlo.')
    day = fields.required_date({'today': today}, 'today')
    before = _snapshot(row)
    row.state, row.authorized_by_id, row.authorized_at = State.authorized, person_id, day
    _record(session, who, row, before)
    return row


def reject(session: Session, who: Actor, record_id: int, *, reason: str | None) -> ProductionChange:
    """Retain rejected proposals with reason and the same independent web authority."""
    row, _ = _decision(session, who, record_id, rejecting=True)
    reason = fields.text({'reason': reason}, 'reason')
    if not reason:
        raise ValidationError('Indica el motivo del rechazo.')
    before = _snapshot(row)
    row.state, row.rejection_reason = State.rejected, reason
    _record(session, who, row, before)
    return row


def _delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, Resource.PRODUCTION)
    crud._load(SPEC, session, record_id)
    raise ValidationError(NO_DELETE)


def _conditions(state: Any = None, order_id: int | None = None) -> list[Any]:
    where = []
    if state is not None:
        where.append(ProductionChange.state == fields.enum_member({'state': state}, 'state', State))
    if order_id is not None:
        value = fields.integer({'order_id': order_id}, 'order_id', required=True)
        where.append(ProductionChange.orders.any(ProductionOrder.id == value))
    return where


# Decided changes stay readable everywhere (retained evidence); only proposals are
# writable, which _update enforces for every channel.
changes = Register(SPEC, _conditions, _create, _update, _delete)
