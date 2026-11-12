"""Typed design inputs/outputs (DD2, ISO 9001 8.3.3/8.3.5).

Reuse DD1 parent locking/editability and the common CRUD validators, stamps and
snapshots. Writes flush, never commit. No-op updates and refusals write nothing.
New citations require readable approved/effective revisions, held under a shared
row lock until commit. Existing exact citations survive obsolescence/withdrawal.
Only drafts can be discarded by document control; drafts cannot be cited here.
MCP returns column ids, never related revision metadata, like knowledge citations.
"""
from collections.abc import Mapping
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (DesignInput, DesignInputType, DesignOutput, DesignOutputType,
                      Document, DocumentRevision, EstadoRevision)
from . import audit, crud, design, document_revisions, fields, people, policy
from .actor import Actor
from .errors import NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

INVALID_REVISION = 'Elige una revisión aprobada o vigente que puedas consultar.'
LINKABLE_STATES = (EstadoRevision.aprobado, EstadoRevision.vigente)


def _spec(model: type, kind: type, not_found: str, *, output: bool = False) -> crud.Spec:
    """Describe the public writable fields; identity/attribution stay read-only."""
    return crud.Spec(
        model=model, resource=Resource.DESIGN, not_found=not_found,
        fields=(
            crud.Field('project_id', partial(fields.integer, required=True), required=True),
            crud.Field('kind', partial(fields.enum_member, enum_cls=kind), required=True),
            crud.Field('text', partial(fields.text, required=True, strip=False), required=True),
            *((crud.Field('acceptance_criteria', partial(
                fields.text, required=True, strip=False), required=True),) if output else ()),
            crud.Field('revision_id', fields.integer),
        ), order_by=(model.project_id, model.id),
    )


INPUT_SPEC = _spec(DesignInput, DesignInputType, 'Entrada de diseño no encontrada.')
OUTPUT_SPEC = _spec(DesignOutput, DesignOutputType, 'Salida de diseño no encontrada.',
                    output=True)


def visible_revision(session: Session, who: Actor,
                     revision_id: int | None) -> DocumentRevision | None:
    """Return a readable citation or None, without leaking restricted metadata."""
    if revision_id is None:
        return None
    try:
        return document_revisions.get(session, who, revision_id)
    except (NotFound, PermissionDenied):
        return None


def revision_choices(session: Session, who: Actor,
                     current: int | None = None) -> list[tuple[int | str, str]]:
    """Offer readable approved/effective revisions plus the retained current link."""
    choices: list[tuple[int | str, str]] = [('', '— Sin revisión —')]
    if policy.can(who, Action.READ, Resource.DOCUMENTS):
        candidates = session.scalars(select(DocumentRevision).where(
            DocumentRevision.estado.in_(LINKABLE_STATES)).order_by(
                DocumentRevision.document_id, DocumentRevision.numero))
        for revision in candidates:
            if visible_revision(session, who, revision.id) is not None:
                document = session.get(Document, revision.document_id)
                choices.append((revision.id,
                                f'{document.code} · Revisión {revision.numero}'))
    if current is not None and current not in {key for key, _ in choices}:
        revision = visible_revision(session, who, current)
        label = 'Revisión vinculada'
        if revision is not None:
            document = session.get(Document, revision.document_id)
            label = f'{document.code} · Revisión {revision.numero} (conservada)'
        choices.append((current, label))
    return choices


def _values(spec: crud.Spec, session: Session, who: Actor, data: Mapping[str, Any],
            found: Any = None) -> dict[str, Any]:
    values = crud._clean(spec, session, data, found)
    if found is not None and values.get('project_id', found.project_id) != found.project_id:
        raise ValidationError('Un elemento no puede pasar a otro proyecto.')
    revision_id = values.get('revision_id')
    if revision_id is not None and revision_id != getattr(found, 'revision_id', None):
        if not people.is_db_id(revision_id):
            raise ValidationError(INVALID_REVISION)
        # Reuse the document reader as the authority both before and after refresh.
        if visible_revision(session, who, revision_id) is None:
            raise ValidationError(INVALID_REVISION)
        revision = session.scalar(select(DocumentRevision).where(
            DocumentRevision.id == revision_id).with_for_update(read=True)
            .execution_options(populate_existing=True))
        if (revision is None or revision.estado not in LINKABLE_STATES
                or visible_revision(session, who, revision_id) is None):
            raise ValidationError(INVALID_REVISION)
    return values


def _parent(spec: crud.Spec, session: Session, record_id: int) -> tuple[Any, Any]:
    found = crud._load(spec, session, record_id)
    parent = design._lock_project(session, found.project_id)
    found = session.scalar(select(spec.model).where(spec.model.id == record_id)
                           .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(spec.not_found)
    return parent, found


def _create(spec: crud.Spec, session: Session, who: Actor,
            data: Mapping[str, Any]) -> Any:
    policy.require(who, Action.CREATE, Resource.DESIGN)
    design._payload(data, spec.writable)
    fields.require_keys(data, frozenset(f.name for f in spec.fields if f.required))
    project_id = fields.integer(data, 'project_id', required=True)
    design._editable(design._lock_project(session, project_id))
    values = _values(spec, session, who, data)
    return crud.create(spec, session, who, values)


def _update(spec: crud.Spec, session: Session, who: Actor, record_id: int,
            data: Mapping[str, Any]) -> Any:
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    design._payload(data, spec.writable)
    parent, found = _parent(spec, session, record_id)
    values = _values(spec, session, who, data, found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    design._editable(parent)
    return crud.update(spec, session, who, record_id, values)


def _delete(spec: crud.Spec, session: Session, who: Actor, record_id: int) -> None:
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    if who.channel == 'mcp':
        raise PermissionDenied()
    parent, found = _parent(spec, session, record_id)
    design._editable(parent)
    audit.record(session, who, 'delete', found)
    session.delete(found)
    design._flush(session)


def _conditions(model: type, enum_cls: type, project_id: int | None = None,
                kind: Any = None) -> list[Any]:
    where = []
    if project_id is not None:
        where.append(model.project_id == fields.integer(
            {'project_id': project_id}, 'project_id', required=True))
    if kind is not None:
        where.append(model.kind == fields.enum_member({'kind': kind}, 'kind', enum_cls))
    return where


def _register(spec: crud.Spec, enum_cls: type) -> Register:
    return Register(spec, partial(_conditions, spec.model, enum_cls),
                    partial(_create, spec), partial(_update, spec), partial(_delete, spec))


inputs = _register(INPUT_SPEC, DesignInputType)
outputs = _register(OUTPUT_SPEC, DesignOutputType)
