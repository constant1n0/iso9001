"""Append-only communication evidence (ISO 9001 7.4, CM2); never sends.

Assumption: dates before the latest occurrence are refused; same-day entries
are allowed and ordered by id. The plan row serializes appends against other
appends, edits and retirement. All validation precedes mutation. Services flush
but never commit: the adapter commits the occurrence and the plan update together.
The chosen next date is also retained on the occurrence as historical evidence.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from ..models import CommunicationChannel, CommunicationLog, CommunicationPlan, Person
from . import communication, crud, fields, people, policy
from .actor import Actor
from .errors import ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.COMMUNICATION
IMMUTABLE = ('Las comunicaciones registradas no se modifican ni se eliminan; '
             'registra una nueva.')
BACKDATED = 'La comunicación no puede ser anterior a la última registrada.'
RETIRED = 'No se pueden registrar comunicaciones de una entrada retirada.'
NEXT_REQUIRED = 'Indica una nueva próxima fecha posterior a la comunicación.'
UNKNOWN_PLAN = 'El campo «plan_id» no corresponde a ninguna comunicación planificada.'

SPEC = crud.Spec(
    model=CommunicationLog, resource=RESOURCE,
    not_found='Comunicación registrada no encontrada.',
    fields=(
        crud.Field('plan_id', partial(fields.integer, required=True), required=True),
        crud.Field('date', fields.required_date, required=True),
        crud.Field('communicator_id', partial(fields.integer, required=True), required=True),
        crud.Field('channel', partial(fields.enum_member, enum_cls=CommunicationChannel),
                   required=True),
        crud.Field('channel_detail', fields.text),
        crud.Field('audience_text', fields.text),
        crud.Field('summary', fields.text),
        crud.Field('evidence', partial(fields.text, required=True), required=True),
        crud.Field('next_date', fields.required_date, required=True),
    ),
    order_by=(CommunicationLog.date.desc(), CommunicationLog.id.desc()),
)


def _lock(session: Session, plan_id: int) -> CommunicationPlan:
    """Lock and refresh the plan until the adapter commits or rolls back."""
    if not people.is_db_id(plan_id):
        raise ValidationError(UNKNOWN_PLAN)
    found = session.scalar(select(CommunicationPlan).where(
        CommunicationPlan.id == plan_id).with_for_update()
        .execution_options(populate_existing=True))
    if found is None:
        raise ValidationError(UNKNOWN_PLAN)
    return found


def latest(session: Session, plan_id: int) -> CommunicationLog | None:
    """Latest evidence by occurrence date, then insertion order for same-day ties."""
    return session.scalar(select(CommunicationLog).where(
        CommunicationLog.plan_id == plan_id).order_by(*SPEC.order_by).limit(1))


def _create(session: Session, actor: Actor, data: Mapping[str, Any], *,
            today: date) -> CommunicationLog:
    """Validate, lock and append evidence with an explicit audited plan-date update."""
    policy.require(actor, Action.CREATE, RESOURCE)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values = crud._clean(SPEC, session, data)
    if values['date'] > today:
        raise ValidationError('La fecha de comunicación no puede ser futura.')
    if values['next_date'] <= values['date']:
        raise ValidationError(NEXT_REQUIRED)
    plan = _lock(session, values['plan_id'])
    if plan.retired:
        raise ValidationError(RETIRED)
    previous = latest(session, plan.id)
    if previous is not None and values['date'] < previous.date:
        raise ValidationError(BACKDATED)
    person_id = values['communicator_id']
    if people.is_db_id(person_id):
        session.scalar(select(Person).where(Person.id == person_id)
                       .with_for_update(read=True).execution_options(populate_existing=True))
    people.check_reference(session, 'communicator_id', person_id)
    # Reuse the plan's update/audit path, including its no-op and attribution rules.
    communication.items.update(session, actor, plan.id, {'next_date': values['next_date']})
    return crud.create(SPEC, session, actor, values)


def _immutable_update(session: Session, actor: Actor, record_id: int,
                      data: Mapping[str, Any], *, today: date) -> CommunicationLog:
    """Refuse even no-op edits of existing evidence, like equipment checks."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    crud.get(SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _immutable_delete(session: Session, actor: Actor, record_id: int) -> None:
    """Refuse evidence deletion, including administrators outside MCP."""
    policy.require(actor, Action.DELETE, RESOURCE)
    crud.get(SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _conditions(plan_id: int | None = None, communicator_id: int | None = None,
                date_from: date | None = None, date_to: date | None = None) -> list[Any]:
    """Combine plan, communicator and inclusive date-range filters."""
    where = []
    for key, value in (('plan_id', plan_id), ('communicator_id', communicator_id)):
        if value is not None:
            where.append(getattr(CommunicationLog, key) == value
                         if people.is_db_id(value) else false())
    for key, value, column in (('date_from', date_from, CommunicationLog.date.__ge__),
                                ('date_to', date_to, CommunicationLog.date.__le__)):
        if value is not None:
            where.append(column(fields.required_date({key: value}, key)))
    return where


items = Register(SPEC, _conditions, _create, _immutable_update, _immutable_delete)
