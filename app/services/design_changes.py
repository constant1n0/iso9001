"""Authorized design changes and output version approval (ISO 9001 8.3.5/8.3.6).

Content versions are ordinary DD2 output rows with one-to-one metadata. Applying
an authorization appends draft copies, never updates its immutable change or
approved source rows. The existence of versions citing a change is its receipt.
Every writer locks the project then refreshes cited rows. No service commits.
"""
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import (DesignApprovalState, DesignChange, DesignChangeState,
                      DesignOutput, DesignOutputVersion, DesignProject,
                      Person, RoleEnum)
from . import audit, crud, design, fields, people, policy
from .actor import Actor
from .attribution import stamp_created
from .errors import NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

FROZEN = 'Una salida aprobada es de solo lectura; propone un cambio autorizado.'
IMMUTABLE = 'Un cambio autorizado o rechazado es de solo lectura.'
INVALID_OUTPUTS = 'Elige al menos una salida aprobada actual del mismo proyecto.'
APPLY_ONCE = 'Solo puedes aplicar un cambio autorizado una vez.'
NO_PERSON = 'Tu usuario debe tener una persona vinculada para aprobar.'
SEPARATION = 'La aprobación requiere una persona distinta del responsable del proyecto.'
OVERLAPPING = 'Aplica antes el cambio autorizado que afecta a las mismas salidas.'


def _ids(data: Mapping[str, Any], key: str) -> list[int]:
    """Normalize an explicit nonempty identifier set; booleans are not ids."""
    values = data[key]
    if (not isinstance(values, (list, tuple)) or not values
            or any(not people.is_db_id(value) for value in values)):
        raise ValidationError(INVALID_OUTPUTS)
    return sorted(set(values))


CHANGE_SPEC = crud.Spec(
    model=DesignChange, resource=Resource.DESIGN,
    not_found='Cambio del diseño no encontrado.',
    fields=(
        crud.Field('project_id', partial(fields.integer, required=True), required=True),
        *(crud.Field(name, partial(fields.text, required=True), required=True)
          for name in ('reason', 'impact', 'actions')),
        crud.Field('review', fields.text),
        crud.Field('output_ids', _ids, required=True),
    ), order_by=(DesignChange.project_id, DesignChange.id),
)
VERSION_SPEC = crud.Spec(
    model=DesignOutputVersion, resource=Resource.DESIGN,
    not_found='Versión de salida no encontrada.',
    fields=tuple(crud.Field(name, partial(fields.integer, required=True), required=True)
                 for name in ('output_id', 'lineage_id', 'version')) + (
                     crud.Field('change_id', fields.integer),),
    order_by=(DesignOutputVersion.lineage_id, DesignOutputVersion.version),
)


def _web_admin(who: Actor) -> None:
    """Approval authority is never available to MCP, auditors or system actors."""
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    if who.channel != 'web' or who.role is not RoleEnum.ADMINISTRADOR:
        raise PermissionDenied()


def may_approve(session: Session, who: Actor, project: DesignProject) -> bool:
    """Nonmutating UI hint; writers recheck linked identity under the parent lock."""
    person_id = people.of_user(session, who.user_id)
    return (who.channel == 'web' and who.role is RoleEnum.ADMINISTRADOR
            and person_id is not None and person_id != project.owner_id)


def approval_person(session: Session, who: Actor, project: DesignProject) -> int:
    """Resolve user→person with the existing authority and refresh under a share lock."""
    _web_admin(who)
    person_id = people.of_user(session, who.user_id)
    person = None if person_id is None else session.scalar(select(Person).where(
        Person.id == person_id).with_for_update(read=True)
        .execution_options(populate_existing=True))
    if person is None or person.user_id != who.user_id:
        raise ValidationError(NO_PERSON)
    if person.id == project.owner_id:
        raise ValidationError(SEPARATION)
    return person.id


def version_record(session: Session, output_id: int) -> DesignOutputVersion:
    """Refresh approval metadata after the caller has locked its project."""
    row = session.scalar(select(DesignOutputVersion).where(
        DesignOutputVersion.output_id == output_id)
        .execution_options(populate_existing=True))
    if row is None:
        raise NotFound(VERSION_SPEC.not_found)
    return row


def ensure_draft(session: Session, output: DesignOutput) -> DesignOutputVersion:
    """Refuse approved versions, even for an explicitly submitted no-op."""
    version = version_record(session, output.id)
    if version.state is not DesignApprovalState.draft:
        raise ValidationError(FROZEN)
    return version


def add_version(session: Session, who: Actor, output: DesignOutput, *,
                source: DesignOutputVersion | None = None,
                change_id: int | None = None) -> DesignOutputVersion:
    """Record a new version through the shared audited CRUD insertion path."""
    row = crud.create(VERSION_SPEC, session, who, dict(
        output_id=output.id, lineage_id=output.id if source is None else source.lineage_id,
        version=1 if source is None else source.version + 1, change_id=change_id))
    session.expire(output, ['version_record'])
    design._flush(session)
    return row


def _later(version: Any) -> Any:
    """Correlated later-version predicate, independent of cached relationships."""
    later = DesignOutputVersion.__table__.alias('later_version')
    return exists().where(later.c.lineage_id == version.lineage_id,
                          later.c.version > version.version)


def current_versions(session: Session, project_id: int) -> list[DesignOutputVersion]:
    """Read every current lineage tip while the completion writer holds the lock."""
    return list(session.scalars(select(DesignOutputVersion).join(
        DesignOutput, DesignOutput.id == DesignOutputVersion.output_id).where(
            DesignOutput.project_id == project_id, ~_later(DesignOutputVersion))
        .order_by(DesignOutputVersion.lineage_id)
        .execution_options(populate_existing=True)))


def history(session: Session, who: Actor, output_id: int) -> list[DesignOutput]:
    """Return all content versions, oldest first; every role may read history."""
    policy.require(who, Action.READ, Resource.DESIGN)
    version = version_record(session, output_id)
    return list(session.scalars(select(DesignOutput).join(
        DesignOutputVersion, DesignOutput.id == DesignOutputVersion.output_id).where(
            DesignOutputVersion.lineage_id == version.lineage_id)
        .order_by(DesignOutputVersion.version).execution_options(populate_existing=True)))


def approve_output(session: Session, who: Actor, output_id: int, *,
                   today: date) -> DesignOutput:
    """Freeze one draft with independent web administrator and person attribution."""
    from . import design_io
    _web_admin(who)
    parent, output = design_io._parent(design_io.OUTPUT_SPEC, session, output_id)
    design._editable(parent)
    person_id = approval_person(session, who, parent)
    version = ensure_draft(session, output)
    day = fields.required_date({'today': today}, 'today')
    before = audit.snapshot(version)
    version.state = DesignApprovalState.approved
    version.approved_at, version.approved_by_id = day, person_id
    design._record_update(session, who, version, before)
    design._flush(session)
    return output


def snapshot(change: DesignChange) -> dict[str, Any]:
    """Include the normalized affected-version set in every change audit."""
    return audit.snapshot(change) | {'output_ids': change.output_ids}


def _linked_outputs(session: Session, project_id: int, ids: list[int]
                    ) -> list[DesignOutput]:
    """Refresh cited outputs and require approved tips of this same project."""
    rows = list(session.scalars(select(DesignOutput).where(
        DesignOutput.id.in_(ids)).order_by(DesignOutput.id)
        .execution_options(populate_existing=True)))
    tips = {v.output_id for v in current_versions(session, project_id)
            if v.state is DesignApprovalState.approved}
    if len(rows) != len(ids) or any(row.id not in tips for row in rows):
        raise ValidationError(INVALID_OUTPUTS)
    return rows


def _change_parent(session: Session, change_id: int) -> tuple[DesignProject, DesignChange]:
    """Load identity only before locking, then refresh columns and linked versions."""
    found = crud._load(CHANGE_SPEC, session, change_id)
    parent = design._lock_project(session, found.project_id)
    found = session.scalar(select(DesignChange).where(DesignChange.id == change_id)
                           .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(CHANGE_SPEC.not_found)
    session.expire(found, ['outputs'])
    design._editable(parent)
    return parent, found


def _proposed(change: DesignChange) -> None:
    if change.state is not DesignChangeState.proposed:
        raise ValidationError(IMMUTABLE)


def _create(session: Session, who: Actor, data: Mapping[str, Any]) -> DesignChange:
    """Propose a change from web or MCP; review may remain pending until authorization."""
    policy.require(who, Action.CREATE, Resource.DESIGN)
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset({'project_id'}))
    project = design._lock_project(session, fields.integer(data, 'project_id', required=True))
    design._editable(project)
    fields.require_keys(data, frozenset(f.name for f in CHANGE_SPEC.fields if f.required))
    values = crud._clean(CHANGE_SPEC, session, data)
    linked = _linked_outputs(session, project.id, values.pop('output_ids'))
    created = DesignChange(**values, outputs=linked)
    stamp_created(created, who)
    session.add(created)
    log = audit.record(session, who, 'create', created)
    log.after = snapshot(created)
    design._flush(session)
    return created


def _update(session: Session, who: Actor, record_id: int,
            data: Mapping[str, Any]) -> DesignChange:
    """Edit only proposed web changes; MCP has create/read only, even for no-ops."""
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    if who.channel != 'web':
        raise PermissionDenied()
    parent, found = _change_parent(session, record_id)
    _proposed(found)
    values = crud._clean(CHANGE_SPEC, session, data, found)
    if values.get('project_id', parent.id) != parent.id:
        raise ValidationError('Un cambio no puede pasar a otro proyecto.')
    linked = None
    if 'output_ids' in values:
        linked = _linked_outputs(session, parent.id, values.pop('output_ids'))
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if linked is not None and [row.id for row in linked] != found.output_ids:
        changes['outputs'] = linked
    if not changes:
        return found
    before = snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    _record_change(session, who, found, before)
    return found


def _record_change(session: Session, who: Actor, change: DesignChange,
                   before: dict[str, Any]) -> None:
    """Audit columns and links together using the existing attribution helper."""
    from .attribution import stamp_updated
    stamp_updated(change, who)
    audit.record(session, who, 'update', change, before=before, after=snapshot(change))
    design._flush(session)


def authorize(session: Session, who: Actor, change_id: int, *, today: date) -> DesignChange:
    """Authorize reviewed changes, retaining exact approved affected versions."""
    _web_admin(who)
    project, change = _change_parent(session, change_id)
    person_id = approval_person(session, who, project)
    _proposed(change)
    if not change.review or not change.review.strip():
        raise ValidationError('Revisa el cambio antes de autorizarlo.')
    _linked_outputs(session, project.id, change.output_ids)
    # Two pending authorizations on one output would strand the second forever.
    pending = session.scalars(select(DesignChange).where(
        DesignChange.project_id == project.id, DesignChange.id != change.id,
        DesignChange.state == DesignChangeState.authorized))
    if any(set(other.output_ids) & set(change.output_ids)
           and not is_applied(session, other.id) for other in pending):
        raise ValidationError(OVERLAPPING)
    day = fields.required_date({'today': today}, 'today')
    before = snapshot(change)
    change.state = DesignChangeState.authorized
    change.authorized_by_id, change.authorized_at = person_id, day
    _record_change(session, who, change, before)
    return change


def reject(session: Session, who: Actor, change_id: int, *, reason: str | None) -> DesignChange:
    """Reject explicitly, with the same web authority and separation as authorization."""
    _web_admin(who)
    project, change = _change_parent(session, change_id)
    approval_person(session, who, project)
    _proposed(change)
    reason = fields.text({'reason': reason}, 'reason')
    if not reason:
        raise ValidationError('Indica el motivo del rechazo.')
    before = snapshot(change)
    change.state, change.rejection_reason = DesignChangeState.rejected, reason
    _record_change(session, who, change, before)
    return change


def is_applied(session: Session, change_id: int) -> bool:
    """Application receipt is append-only version metadata, not a change mutation."""
    return session.scalar(select(DesignOutputVersion.output_id).where(
        DesignOutputVersion.change_id == change_id).limit(1)) is not None


def apply(session: Session, who: Actor, change_id: int) -> list[DesignOutput]:
    """Atomically open all new drafts once; authorization itself stays immutable."""
    from . import design_io
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    if who.channel != 'web':
        raise PermissionDenied()
    project, change = _change_parent(session, change_id)
    if change.state is not DesignChangeState.authorized or is_applied(session, change.id):
        raise ValidationError(APPLY_ONCE)
    outputs = _linked_outputs(session, project.id, change.output_ids)
    # Validate every source before creating any row or audit entry.
    sources = [version_record(session, row.id) for row in outputs]
    result = []
    for old, source in zip(outputs, sources):
        # Retain even obsolete exact document citations: copying an approved version
        # is not a newly chosen citation. Subsequent draft edits reuse DD2 validation.
        content = {field.name: getattr(old, field.name) for field in design_io.OUTPUT_SPEC.fields}
        created = crud.create(design_io.OUTPUT_SPEC, session, who, content)
        add_version(session, who, created, source=source, change_id=change.id)
        result.append(created)
    return result


def _delete(session: Session, who: Actor, record_id: int) -> None:
    """Retain proposed and decided changes, including rejections."""
    policy.require(who, Action.DELETE, Resource.DESIGN)
    _change_parent(session, record_id)
    raise ValidationError('Los cambios del diseño no se eliminan; recházalos con un motivo.')


def _conditions(project_id: int | None = None, state: Any = None) -> list[Any]:
    where = []
    if project_id is not None:
        where.append(DesignChange.project_id == fields.integer(
            {'project_id': project_id}, 'project_id', required=True))
    if state is not None:
        where.append(DesignChange.state == fields.enum_member(
            {'state': state}, 'state', DesignChangeState))
    return where


changes = Register(CHANGE_SPEC, _conditions, _create, _update, _delete)
