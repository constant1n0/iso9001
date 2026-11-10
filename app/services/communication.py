"""Communication plan register (ISO 9001 7.4, CM1); never sends anything.

All roles read, administrators and auditors maintain and retire entries, and only
administrators outside MCP delete uncited entries. Occurrences protect their
plan through REFERENCES. Services flush but never commit.

Assumption: at least one linked audience or nonblank audience description is
required. Each supplied id list replaces its set. New people must be active and
new providers must not be blocked; existing links survive state changes. Parties
and customers have no active/current lifecycle. Association changes appear only
in the parent's audit snapshot. Rejected validation and no-ops write nothing.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Cliente, CommunicationChannel, CommunicationLog, CommunicationPlan, CommunicationScope,
    EstadoProveedor, ParteInteresada, Person, Proveedor,
)
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

REFERENCES: tuple[Any, ...] = (CommunicationLog.plan_id,)
LINKS = {
    'stakeholder_ids': ('stakeholders', ParteInteresada, ParteInteresada.id_interesado),
    'customer_ids': ('customers', Cliente, Cliente.id),
    'provider_ids': ('providers', Proveedor, Proveedor.id),
    'person_ids': ('people', Person, Person.id),
}
STILL_REFERENCED = 'No se puede eliminar una comunicación citada; retírala en su lugar.'
AUDIENCE_REQUIRED = 'Indica al menos un destinatario o una descripción de destinatarios.'
BLOCKED_PROVIDER = (
    'El proveedor del campo «provider_ids» está bloqueado; elige otro proveedor.')


def _retired(data: Mapping[str, Any], key: str) -> bool:
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError('El campo «retired» debe ser verdadero o falso.')
    return value


SPEC = crud.Spec(
    model=CommunicationPlan, resource=Resource.COMMUNICATION,
    not_found='Comunicación planificada no encontrada.',
    fields=(
        crud.Field('title', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('content', partial(fields.text, required=True, strip=False), required=True),
        crud.Field('scope', partial(fields.enum_member, enum_cls=CommunicationScope), required=True),
        crud.Field('channel', partial(fields.enum_member, enum_cls=CommunicationChannel), required=True),
        crud.Field('channel_detail', fields.text),
        crud.Field('audience_text', fields.text),
        crud.Field('owner_id', partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field('frequency', partial(fields.text, required=True), required=True),
        crud.Field('next_date', fields.required_date, required=True),
        crud.Field('retired', _retired),
    ),
    order_by=(CommunicationPlan.title, CommunicationPlan.id),
)
WRITABLE = SPEC.writable | LINKS.keys()


def is_overdue(item: CommunicationPlan, *, today: date) -> bool:
    """An unretired plan is overdue only after its next date has ended."""
    return not item.retired and item.next_date < today


def _conditions(scope: Any = None, channel: Any = None, *,
                retired: bool | None = False, overdue: bool | None = None,
                today: date | None = None) -> list[Any]:
    """Combine filters; retired plans are hidden unless explicitly requested."""
    where = []
    for key, value, enum_cls in (('scope', scope, CommunicationScope),
                                  ('channel', channel, CommunicationChannel)):
        if value is not None:
            member = fields.enum_member({key: value}, key, enum_cls)
            where.append(getattr(CommunicationPlan, key) == member)
    if retired is not None:
        where.append(CommunicationPlan.retired.is_(_retired({'retired': retired}, 'retired')))
    if overdue is not None:
        value = fields.boolean({'overdue': overdue}, 'overdue')
        if not isinstance(today, date) or isinstance(today, datetime):
            raise ValueError('today must be a date when filtering overdue communications')
        late = CommunicationPlan.retired.is_(False) & (CommunicationPlan.next_date < today)
        where.append(late if value else not_(late))
    return where


def link_ids(item: CommunicationPlan) -> dict[str, list[int]]:
    """Public, ordered audience ids, shared by audit and MCP reads."""
    return {key: getattr(item, key) for key in LINKS}


def _snapshot(item: CommunicationPlan) -> dict[str, Any]:
    return audit.snapshot(item) | link_ids(item)


def _clean(session: Session, data: Mapping[str, Any],
           found: CommunicationPlan | None = None
           ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Validate the complete proposed record before any mutation or audit write."""
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, WRITABLE)
    values = {field.name: field.clean(data, field.name)
              for field in SPEC.fields if field.name in data}
    # Lock targets before validating active/current state until the adapter commits.
    if 'owner_id' in values and people.is_db_id(values['owner_id']):
        session.scalar(select(Person).where(Person.id == values['owner_id'])
                       .with_for_update(read=True).execution_options(populate_existing=True))
    for field in SPEC.fields:
        if field.check is not None and field.name in values:
            field.check(session, field.name, values[field.name],
                        getattr(found, field.name, None))
    linked = {}
    for key, (relation, model, column) in LINKS.items():
        if key not in data:
            continue
        ids = data[key]
        if not isinstance(ids, (list, tuple)) or any(type(value) is not int for value in ids):
            raise ValidationError(f'El campo «{key}» debe ser una lista de identificadores.')
        wanted = set(ids)
        searchable = [value for value in wanted if people.is_db_id(value)]
        rows = list(session.scalars(select(model).where(column.in_(searchable))
                                    .order_by(column).with_for_update(read=True)
                                    .execution_options(populate_existing=True))) if searchable else []
        missing = sorted(wanted - {getattr(row, column.key) for row in rows})
        if missing:
            raise ValidationError(f'El campo «{key}» contiene identificadores desconocidos: '
                                  f'{", ".join(map(str, missing))}.')
        current = set(getattr(found, key, []))
        for row in rows:
            row_id = getattr(row, column.key)
            if key == 'person_ids':
                people.check_reference(session, key, row_id,
                                       row_id if row_id in current else None)
            if key == 'provider_ids' and row_id not in current and row.estado is EstadoProveedor.bloqueado:
                raise ValidationError(BLOCKED_PROVIDER)
        linked[relation] = rows
    audience_text = values.get('audience_text', getattr(found, 'audience_text', None))
    if not audience_text and not any(
            linked.get(relation, getattr(found, relation, []))
            for relation, _, _ in LINKS.values()):
        raise ValidationError(AUDIENCE_REQUIRED)
    return values, linked


def _create(session: Session, actor: Actor, data: Mapping[str, Any]) -> CommunicationPlan:
    """Create one plan and its audience with a single full parent audit snapshot."""
    policy.require(actor, Action.CREATE, Resource.COMMUNICATION)
    if isinstance(data, Mapping):
        fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values, linked = _clean(session, data)
    lists = {relation: [] for relation, _, _ in LINKS.values()}
    created = CommunicationPlan(**values, **(lists | linked))
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, 'create', created)
        row.after = _snapshot(created)
    except IntegrityError as exc:
        raise Conflict() from exc
    return created


def _update(session: Session, actor: Actor, record_id: int,
            data: Mapping[str, Any]) -> CommunicationPlan:
    """Replace supplied fields/sets, serializing edits and omitting no-op audits."""
    policy.require(actor, Action.UPDATE, Resource.COMMUNICATION)
    found = session.scalars(select(CommunicationPlan).where(CommunicationPlan.id == record_id)
                            .with_for_update().execution_options(populate_existing=True)).one_or_none()
    if found is None:
        return crud.get(SPEC, session, actor, record_id)
    values, linked = _clean(session, data, found)
    changes = {key: value for key, value in (values | linked).items()
               if getattr(found, key) != value}
    if not changes:
        return found
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, 'update', found, before=before, after=_snapshot(found))
    _flush(session)
    return found


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc


def _delete(session: Session, actor: Actor, record_id: int) -> None:
    """Delete uncited plans only; recorded occurrences must remain readable."""
    policy.require(actor, Action.DELETE, Resource.COMMUNICATION)
    found = crud.get(SPEC, session, actor, record_id)
    if any(session.scalar(select(exists().where(column == found.id))) for column in REFERENCES):
        raise Conflict(STILL_REFERENCED)
    audit.record(session, actor, 'delete', found, before=_snapshot(found))
    session.delete(found)
    _flush(session)


items = Register(SPEC, _conditions, _create, _update, _delete)
