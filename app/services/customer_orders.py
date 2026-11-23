"""Customer requirements (8.2): parent→revision locks, immutable web reviews.

Acceptance requires five cumple/no aplica checks and resolved differences. Only
web administrators review/cancel; drafts are maintained by administrators/auditors.
No service commits. Status is derived, so a new draft removes prior commitment.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import (CustomerOrder, CustomerOrderRevision, CustomerOrderKind,
    CustomerOrderStatus, RequirementCheck, OrderReviewResult, Person, RoleEnum,
    CommunicationLog, Reclamacion, ProductionOrder)
from . import audit, clientes, crud, design, fields, people, policy, product_release
from .actor import Actor
from .policy import Action, Resource
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .attribution import stamp_updated
from .quality_objectives import Register

RESOURCE = Resource.CUSTOMER_ORDER
LINKS = {'communication_ids': ('communications', CommunicationLog),
         'complaint_ids': ('complaints', Reclamacion)}
READ_ONLY = 'Solo puedes editar la última revisión sin revisar de un pedido no cancelado.'
CONTENT = ('requirements', 'delivery_requirements', 'post_delivery_requirements',
           'change_reason', 'communicated_to')
PARENT_SPEC = crud.Spec(model=CustomerOrder, resource=RESOURCE, not_found='Pedido no encontrado.',
    fields=(crud.Field('code', partial(fields.text, required=True, max_length=50), required=True),
        crud.Field('customer_id', partial(fields.integer, required=True), required=True,
                   check=clientes.check_reference),
        crud.Field('kind', partial(fields.enum_member, enum_cls=CustomerOrderKind), required=True),
        crud.Field('customer_reference', fields.text),
        crud.Field('received_date', fields.required_date, required=True)),
    order_by=(CustomerOrder.id.desc(),), conflict='Ya existe un pedido con ese código.')
REVISION_SPEC = crud.Spec(model=CustomerOrderRevision, resource=RESOURCE,
    not_found='Revisión del pedido no encontrada.', fields=(
        crud.Field('order_id', partial(fields.integer, required=True), required=True),
        *(crud.Field(k, partial(fields.text, required=k == 'requirements'),
                     required=k == 'requirements') for k in CONTENT)),
    order_by=(CustomerOrderRevision.number.desc(), CustomerOrderRevision.id.desc()))
# Combined creation/edit contract; revision columns are always written on the child.
SPEC = crud.Spec(model=CustomerOrder, resource=RESOURCE, not_found=PARENT_SPEC.not_found,
    fields=PARENT_SPEC.fields + REVISION_SPEC.fields[1:], order_by=PARENT_SPEC.order_by,
    conflict=PARENT_SPEC.conflict)
REVIEW_SPEC = crud.Spec(model=CustomerOrderRevision, resource=RESOURCE,
    not_found=REVISION_SPEC.not_found, fields=(
        *(crud.Field(f'check_{c}', partial(fields.enum_member, enum_cls=RequirementCheck), required=True)
          for c in 'abcde'),
        *(crud.Field(f'note_{c}', fields.text) for c in 'abcde'),
        *(crud.Field(k, design._boolean, required=True) for k in ('differences_resolved', 'undocumented')),
        crud.Field('confirmation_date', design._optional_date),
        crud.Field('confirmation_means', fields.text),
        crud.Field('result', partial(fields.enum_member, enum_cls=OrderReviewResult), required=True)),
    order_by=REVISION_SPEC.order_by)


def _lock(session: Session, record_id: int) -> CustomerOrder:
    row = session.scalar(select(CustomerOrder).where(CustomerOrder.id == record_id)
        .with_for_update().execution_options(populate_existing=True)) if people.is_db_id(record_id) else None
    if row is None:
        raise NotFound(PARENT_SPEC.not_found)
    return row


def link_ids(row: CustomerOrder) -> dict[str, list[int]]:
    """Ordered evidence identifiers shared by audit and MCP reads."""
    return {key: getattr(row, key) for key in LINKS}


def _links(session: Session, data: Mapping, customer_id: int,
           order_id: int | None = None) -> dict:
    """Validate every proposed evidence set before any audited mutation."""
    from .production import _target
    linked = {}
    for key, (relation, model) in LINKS.items():
        if key not in data:
            continue
        ids = data[key]
        if not isinstance(ids, (list, tuple)) or any(not people.is_db_id(i) for i in ids):
            raise ValidationError(f'El campo «{key}» debe ser una lista de identificadores.')
        rows = [_target(session, model, value, key) for value in sorted(set(ids))]
        if key == 'complaint_ids' and any(row.cliente_id != customer_id for row in rows):
            raise ValidationError('La reclamación debe pertenecer al mismo cliente que el pedido.')
        linked[relation] = rows
    # Customer edits must also preserve consistency of already retained evidence.
    if order_id is not None and 'complaint_ids' not in data:
        from ..models import CUSTOMER_ORDER_ASSOCIATIONS as associations
        if session.scalar(select(Reclamacion.id).join(associations['complaint_ids'],
                associations['complaint_ids'].c.target_id == Reclamacion.id).where(
                associations['complaint_ids'].c.order_id == order_id,
                Reclamacion.cliente_id != customer_id).limit(1)) is not None:
            raise ValidationError('La reclamación debe pertenecer al mismo cliente que el pedido.')
    return linked


def _set_links(session: Session, who: Actor, row: CustomerOrder, linked: dict) -> None:
    """Replace evidence sets with one parent audit; identical sets write nothing."""
    changes = {key: value for key, value in linked.items() if getattr(row, key) != value}
    if changes:
        before = audit.snapshot(row) | link_ids(row)
        for key, value in changes.items():
            setattr(row, key, value)
        stamp_updated(row, who)
        audit.record(session, who, 'update', row, before=before,
                     after=audit.snapshot(row) | link_ids(row))
        design._flush(session)


def latest(session: Session, order_id: int) -> CustomerOrderRevision:
    """Refresh the latest revision after the caller has locked/read its parent."""
    return session.scalar(select(CustomerOrderRevision).where(CustomerOrderRevision.order_id == order_id)
        .order_by(CustomerOrderRevision.number.desc()).limit(1).execution_options(populate_existing=True))


def _editable(order: CustomerOrder, revision: CustomerOrderRevision) -> None:
    if order.cancelled or revision.result is not None:
        raise ValidationError(READ_ONLY)


def _locked_revision(session: Session, record_id: int) -> tuple[CustomerOrder, CustomerOrderRevision]:
    row = crud._load(REVISION_SPEC, session, record_id)
    order = _lock(session, row.order_id)
    row = session.scalar(select(CustomerOrderRevision).where(CustomerOrderRevision.id == record_id)
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(REVISION_SPEC.not_found)
    if latest(session, order.id).id != row.id:
        raise ValidationError(READ_ONLY)
    _editable(order, row)
    return order, row


def _change_values(values: dict, number: int, row: Any = None) -> None:
    if number > 1:
        for key in ('change_reason', 'communicated_to'):
            fields.text({key: values.get(key, getattr(row, key, None))}, key, required=True)


def _date(values: dict, row: Any, today: date) -> None:
    if values.get('received_date', getattr(row, 'received_date', today)) > today:
        raise ValidationError('La fecha de recepción no puede ser futura.')


def _create(session: Session, who: Actor, data: Mapping, *, today: date) -> CustomerOrder:
    policy.require(who, Action.CREATE, RESOURCE)
    design._payload(data, SPEC.writable | LINKS.keys())
    values = product_release._values(SPEC, session, {k: v for k, v in data.items() if k not in LINKS})
    linked = _links(session, data, values['customer_id'])
    _date(values, None, today)
    content = {k: values.pop(k) for k in CONTENT if k in values}
    row = crud.create(PARENT_SPEC, session, who, values)
    _add_revision(session, who, row, 1, content)
    _set_links(session, who, row, linked)
    return row


def _add_revision(session: Session, who: Actor, order: CustomerOrder, number: int,
                  content: dict) -> CustomerOrderRevision:
    """One shared insertion path for first and subsequent numbered revisions."""
    spec = crud.Spec(model=CustomerOrderRevision, resource=RESOURCE,
        not_found=REVISION_SPEC.not_found, fields=REVISION_SPEC.fields + (
            crud.Field('number', partial(fields.integer, required=True), required=True),),
        order_by=REVISION_SPEC.order_by)
    row = crud.create(spec, session, who, content | {'order_id': order.id, 'number': number})
    design._flush(session)
    return row


def _update(session: Session, who: Actor, record_id: int, data: Mapping, *, today: date) -> CustomerOrder:
    policy.require(who, Action.UPDATE, RESOURCE)
    order = _lock(session, record_id)
    session.expire(order, [relation for relation, _ in LINKS.values()])
    revision = latest(session, order.id)
    design._payload(data, SPEC.writable | LINKS.keys())
    core = {k: v for k, v in data.items() if k not in LINKS}
    if order.cancelled and data and not core:
        raise ValidationError('Un pedido cancelado es de solo lectura.')
    if core or not data:
        _editable(order, revision)
    # No revision row carries parent columns; validate each part against its own row.
    values = crud._clean(PARENT_SPEC, session,
        {k: v for k, v in data.items() if k in PARENT_SPEC.writable}, order)
    content = crud._clean(REVISION_SPEC, session,
        {k: v for k, v in data.items() if k in CONTENT}, revision)
    linked = _links(session, data, values.get('customer_id', order.customer_id), order.id)
    _date(values, order, today)
    _change_values(content, revision.number, revision)
    if revision.number > 1 and any(getattr(order, k) != v for k, v in values.items()):
        raise ValidationError('La identidad del pedido queda fija tras la primera revisión.')
    if core:
        crud.update(PARENT_SPEC, session, who, order.id, values)
        _revision_update(session, who, revision.id, content)
    _set_links(session, who, order, linked)
    return order


def _new_revision(session: Session, who: Actor, data: Mapping) -> CustomerOrderRevision:
    policy.require(who, Action.CREATE, RESOURCE)
    values = product_release._values(REVISION_SPEC, session, data)
    order = _lock(session, values.pop('order_id'))
    previous = latest(session, order.id)
    if order.cancelled or previous.result is None:
        raise ValidationError('Revisa la última revisión antes de crear otra; el pedido no puede estar cancelado.')
    _change_values(values, previous.number + 1)
    return _add_revision(session, who, order, previous.number + 1, values)


def _revision_update(session: Session, who: Actor, record_id: int, data: Mapping) -> CustomerOrderRevision:
    policy.require(who, Action.UPDATE, RESOURCE)
    order, row = _locked_revision(session, record_id)
    values = product_release._values(REVISION_SPEC, session, data, row)
    if values.get('order_id', order.id) != order.id:
        raise ValidationError('Una revisión no puede cambiar de pedido.')
    _change_values(values, row.number, row)
    return crud.update(REVISION_SPEC, session, who, row.id, values)


def _web_admin(who: Actor) -> None:
    policy.require(who, Action.UPDATE, RESOURCE)
    if who.channel != 'web' or who.role is not RoleEnum.ADMINISTRADOR:
        raise PermissionDenied()


def review(session: Session, who: Actor, record_id: int, data: Mapping, *, today: date) -> CustomerOrderRevision:
    """Record one immutable web review with server day and active linked person."""
    _web_admin(who)
    order, row = _locked_revision(session, record_id)
    values = product_release._values(REVIEW_SPEC, session, data)
    if values['result'] is OrderReviewResult.accepted and (
            not values['differences_resolved'] or any(values[f'check_{c}'] is
                RequirementCheck.nonconforming for c in 'abcde')):
        raise ValidationError('Para aceptar, todas las comprobaciones deben cumplir o no aplicar y las diferencias estar resueltas.')
    day, means = values.get('confirmation_date'), values.get('confirmation_means')
    if values['undocumented']:
        if day is None or means is None or day > today:
            raise ValidationError('Registra fecha no futura y medio de confirmación del cliente sin requisitos documentados.')
    elif day is not None or means is not None:
        raise ValidationError('La confirmación solo corresponde a requisitos no documentados.')
    person_id = people.of_user(session, who.user_id)
    person = None if person_id is None else session.scalar(select(Person).where(Person.id == person_id)
        .with_for_update(read=True).execution_options(populate_existing=True))
    if person is None or person.user_id != who.user_id:
        raise ValidationError('Tu usuario debe tener una persona activa vinculada para revisar.')
    people.check_reference(session, 'reviewer_id', person.id)
    before = audit.snapshot(row)
    for key, value in values.items():
        setattr(row, key, value)
    row.reviewer_id, row.review_date = person.id, fields.required_date({'today': today}, 'today')
    design._record_update(session, who, row, before)
    design._flush(session)
    return row


def cancel(session: Session, who: Actor, record_id: int, *, reason: str | None) -> CustomerOrder:
    """Cancel once with a nonblank reason, retaining every revision and review."""
    _web_admin(who)
    row = _lock(session, record_id)
    if row.cancelled:
        raise ValidationError('El pedido ya está cancelado.')
    reason = fields.text({'reason': reason}, 'reason', required=True)
    before = audit.snapshot(row)
    row.cancelled, row.cancellation_reason = True, reason
    design._record_update(session, who, row, before)
    design._flush(session)
    return row


def _delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, RESOURCE)
    _web_admin(who)
    order = _lock(session, record_id)
    if session.scalar(select(ProductionOrder.id).where(
            ProductionOrder.customer_order_id == order.id).limit(1)) is not None:
        raise Conflict('No puedes eliminar un pedido citado por producción.')
    revision = latest(session, order.id)
    _editable(order, revision)
    if revision.number != 1:
        raise ValidationError('Solo puedes eliminar un pedido con una única revisión sin revisar.')
    _set_links(session, who, order, {relation: [] for relation, _ in LINKS.values()})
    product_release._remove(session, who, revision)
    product_release._remove(session, who, order)


def _no_delete(session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.DELETE, RESOURCE)
    raise ValidationError('Las revisiones del pedido no se eliminan por separado.')


def _conditions(status: Any = None, kind: Any = None, customer_id: int | None = None) -> list:
    where = []
    for key, value, members in (('kind', kind, CustomerOrderKind), ('status', status, CustomerOrderStatus)):
        if value is not None:
            member = fields.enum_member({key: value}, key, members)
            if key == 'kind':
                where.append(CustomerOrder.kind == member)
            elif member is CustomerOrderStatus.cancelled:
                where.append(CustomerOrder.cancelled.is_(True))
            else:
                result = select(CustomerOrderRevision.result).where(CustomerOrderRevision.order_id == CustomerOrder.id).order_by(
                    CustomerOrderRevision.number.desc()).limit(1).scalar_subquery()
                where.extend((CustomerOrder.cancelled.is_(False), result.is_(None) if member is CustomerOrderStatus.pending else result == member.name))
    if customer_id is not None:
        where.append(CustomerOrder.customer_id == fields.integer({'customer_id': customer_id}, 'customer_id', required=True))
    return where


def _revision_conditions(order_id: int | None = None) -> list:
    return [] if order_id is None else [CustomerOrderRevision.order_id == fields.integer({'order_id': order_id}, 'order_id', required=True)]


orders = Register(SPEC, _conditions, _create, _update, _delete)
revisions = Register(REVISION_SPEC, _revision_conditions, _new_revision, _revision_update, _no_delete)
