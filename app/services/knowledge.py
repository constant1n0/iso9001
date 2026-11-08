"""Organizational knowledge and lessons learned (ISO 9001 7.1.6, KN1).

All roles read; administrators and auditors write and retire. Only administrators
outside MCP delete uncited entries. Writes use the common audited CRUD pipeline,
flush but never commit, and no-op edits write nothing. A previously chosen owner
may remain after deactivation; choosing another requires an active person.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, not_, select
from sqlalchemy.orm import Session

from ..models import KnowledgeItem, KnowledgeType
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

# Future inbound citations belong here; the FK must also use ON DELETE RESTRICT.
REFERENCES: tuple[Any, ...] = ()
STILL_REFERENCED = 'No se puede eliminar un conocimiento citado; retíralo en su lugar.'


def _retired(data: Mapping[str, Any], key: str) -> bool:
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError('El campo «retired» debe ser verdadero o falso.')
    return value


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    return None if data[key] is None else fields.required_date(data, key)


SPEC = crud.Spec(
    model=KnowledgeItem, resource=Resource.KNOWLEDGE,
    not_found='Conocimiento no encontrado.',
    fields=(
        crud.Field('kind', partial(fields.enum_member, enum_cls=KnowledgeType), required=True),
        crud.Field('title', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('summary', partial(fields.text, required=True, strip=False), required=True),
        crud.Field('source', fields.text),
        crud.Field('owner_id', partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field('next_review_date', _optional_date),
        crud.Field('retired', _retired),
    ),
    order_by=(KnowledgeItem.title, KnowledgeItem.id),
)


def is_overdue(item: KnowledgeItem, *, today: date) -> bool:
    """Whether an unretired item's optional review date is strictly before today."""
    return not item.retired and item.next_review_date is not None and item.next_review_date < today


def _conditions(kind: str | KnowledgeType | None = None, *,
                retired: bool | None = False, overdue: bool | None = None,
                today: date | None = None) -> list[Any]:
    """Combine filters; hide retired items unless explicitly requested otherwise."""
    where = []
    if kind is not None:
        value = fields.enum_member({'kind': kind}, 'kind', KnowledgeType)
        where.append(KnowledgeItem.kind == value)
    if retired is not None:
        where.append(KnowledgeItem.retired.is_(_retired({'retired': retired}, 'retired')))
    if overdue is not None:
        value = fields.boolean({'overdue': overdue}, 'overdue')
        if not isinstance(today, date) or isinstance(today, datetime):
            raise ValueError('today must be a date when filtering overdue reviews')
        late = (KnowledgeItem.retired.is_(False) &
                KnowledgeItem.next_review_date.is_not(None) &
                (KnowledgeItem.next_review_date < today))
        where.append(late if value else not_(late))
    return where


def _delete(session: Session, actor: Actor, record_id: int) -> None:
    """Delete only uncited entries, keeping their audited snapshot."""
    policy.require(actor, Action.DELETE, Resource.KNOWLEDGE)
    found = crud.get(SPEC, session, actor, record_id)
    if any(session.scalar(select(exists().where(column == found.id))) for column in REFERENCES):
        raise Conflict(STILL_REFERENCED)
    crud.delete(SPEC, session, actor, found.id)


items = Register(SPEC, _conditions, partial(crud.create, SPEC),
                 partial(crud.update, SPEC), _delete)
