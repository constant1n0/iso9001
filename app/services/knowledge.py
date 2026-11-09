"""Organizational knowledge and lessons learned (ISO 9001 7.1.6, KN1–KN2).

All roles read; administrators and auditors write and retire. Only administrators
outside MCP delete uncited entries. Reads use common CRUD; writes follow the
people/QMS-change association pattern using shared validation, attribution and
audit primitives. They flush but never commit; no-op edits write nothing.

Five id lists replace the corresponding citation sets. New experts must be active
and new documents in use; existing links survive deactivation, withdrawal and
knowledge retirement. Optional typed provenance uses the improvement convention
of real foreign keys, with at most one chosen origin, for either knowledge type.
A previously chosen owner may remain after deactivation; a new one must be active.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    KNOWLEDGE_ORIGINS, AccionRevision, Capacitacion, CompetenceRequirement,
    Document, HallazgoAuditoria, KnowledgeItem, KnowledgeType, Mejora,
    NoConformidad, Person, ProcesoOperacion,
)
from . import audit, crud, fields, people, policy
from .attribution import stamp_created, stamp_updated
from .actor import Actor
from .errors import Conflict, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

# Only inbound citations belong here. KN2's outbound links belong to the item
# and are removed with it; retirement retains them and their RESTRICT protection.
REFERENCES: tuple[Any, ...] = ()
# Payload key: relationship, model, primary key. All lists replace the current set.
LINKS = {
    'process_ids': ('processes', ProcesoOperacion, ProcesoOperacion.id_proceso),
    'document_ids': ('documents', Document, Document.id),
    'expert_ids': ('experts', Person, Person.id),
    'training_ids': ('trainings', Capacitacion, Capacitacion.id),
    'requirement_ids': ('requirements', CompetenceRequirement, CompetenceRequirement.id),
}
ORIGIN_TARGETS = {
    'no_conformidad_id': NoConformidad,
    'hallazgo_id': HallazgoAuditoria,
    'mejora_id': Mejora,
    'accion_revision_id': AccionRevision,
}
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
        *(crud.Field(column, fields.integer) for column in KNOWLEDGE_ORIGINS),
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


WRITABLE = SPEC.writable | LINKS.keys()


def link_ids(item: KnowledgeItem) -> dict[str, list[int]]:
    """Public association snapshot, shared by audit and MCP reads."""
    return {key: getattr(item, key) for key in LINKS}


def _snapshot(item: KnowledgeItem) -> dict[str, Any]:
    """Column values plus ordered association ids, as in people and QMS changes."""
    return audit.snapshot(item) | link_ids(item)


def _clean(session: Session, data: Mapping[str, Any],
           found: KnowledgeItem | None = None) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Validate the entire proposed write before mutating any record or audit row."""
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, WRITABLE)
    values = {field.name: field.clean(data, field.name)
              for field in SPEC.fields if field.name in data}
    for field in SPEC.fields:
        if field.check is not None and field.name in values:
            field.check(session, field.name, values[field.name],
                        getattr(found, field.name, None))
    origins = {key: values.get(key, getattr(found, key, None))
               for key in KNOWLEDGE_ORIGINS}
    if sum(value is not None for value in origins.values()) > 1:
        raise ValidationError('Elige como máximo un registro de origen.')
    for key, model in ORIGIN_TARGETS.items():
        if values.get(key) is not None:
            value = values[key]
            if not people.is_db_id(value) or session.get(model, value) is None:
                raise ValidationError(
                    f'El campo «{key}» no corresponde a ningún registro de origen.')
    linked = {}
    for key, (relation, model, column) in LINKS.items():
        if key not in data:
            continue
        ids = data[key]
        if not isinstance(ids, (list, tuple)) or any(type(value) is not int for value in ids):
            raise ValidationError(f'El campo «{key}» debe ser una lista de identificadores.')
        wanted = set(ids)
        searchable = [value for value in wanted if people.is_db_id(value)]
        # Shared locks hold active/in-use targets stable until the adapter commits.
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
            if key == 'expert_ids':
                people.check_reference(session, key, row_id,
                                       row_id if row_id in current else None)
            if key == 'document_ids' and row_id not in current and row.withdrawn_at is not None:
                raise ValidationError(
                    'El documento del campo «document_ids» está dado de baja; elige uno en uso.')
        linked[relation] = rows
    return values, linked


def _create(session: Session, actor: Actor, data: Mapping[str, Any]) -> KnowledgeItem:
    """Create knowledge and its citations with one parent audit snapshot."""
    policy.require(actor, Action.CREATE, Resource.KNOWLEDGE)
    if isinstance(data, Mapping):
        fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values, linked = _clean(session, data)
    # Initialize every list: snapshotting must not lazy-load and autoflush the audit.
    lists = {relation: [] for relation, _, _ in LINKS.values()}
    created = KnowledgeItem(**values, **(lists | linked))
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, 'create', created)
        row.after = _snapshot(created)
    except IntegrityError as exc:
        raise Conflict() from exc
    return created


def _update(session: Session, actor: Actor, record_id: int,
            data: Mapping[str, Any]) -> KnowledgeItem:
    """Replace only supplied fields and lists, auditing changes but not no-ops."""
    policy.require(actor, Action.UPDATE, Resource.KNOWLEDGE)
    # Serialize replacements of association sets, as the QMS changes service does.
    found = session.scalars(select(KnowledgeItem).where(KnowledgeItem.id == record_id)
                            .with_for_update().execution_options(populate_existing=True)).one_or_none()
    if found is None:
        return crud.get(SPEC, session, actor, record_id)  # common NotFound message
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
    """Translate concurrent referential-integrity failures into domain conflicts."""
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc


def _delete(session: Session, actor: Actor, record_id: int) -> None:
    """Delete only uncited entries, keeping their audited snapshot."""
    policy.require(actor, Action.DELETE, Resource.KNOWLEDGE)
    found = crud.get(SPEC, session, actor, record_id)
    if any(session.scalar(select(exists().where(column == found.id))) for column in REFERENCES):
        raise Conflict(STILL_REFERENCED)
    audit.record(session, actor, 'delete', found, before=_snapshot(found))
    session.delete(found)  # SQLAlchemy removes outbound association rows first.
    _flush(session)


items = Register(SPEC, _conditions, _create, _update, _delete)
