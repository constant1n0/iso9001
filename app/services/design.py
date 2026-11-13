"""Design projects and ordered stages (ISO 9001 8.3, DD1).

Every role reads; administrators and auditors maintain projects and stages.
Transitions are web-only; completion requires an independent administrator. Administrators
may delete drafts without stages, inputs or outputs only. Done stages and
cancelled projects are retained.
Writes flush/audit but never commit; no-ops and validation refusals write nothing.
Every child write and transition locks the parent FOR UPDATE before judging it.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (DesignControlRecord, DesignPlannedControl,
                      DesignInput, DesignOutput, DesignProject, DesignStage,
                      DesignState, ProcesoOperacion)
from . import audit, clientes, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.DESIGN
READ_ONLY = 'Un proyecto cancelado es de solo lectura.'
DONE_LOCKED = 'Una etapa finalizada es de solo lectura.'
STARTER_NAMES = ('Planificación', 'Entradas', 'Diseño', 'Revisión',
                 'Verificación', 'Validación', 'Cierre')
TRANSITIONS = {
    DesignState.borrador: (DesignState.planificado, DesignState.cancelado),
    DesignState.planificado: (DesignState.en_curso, DesignState.cancelado),
    DesignState.en_curso: (DesignState.cancelado,),
}


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f'El campo «{key}» debe ser una fecha.')


def _boolean(data: Mapping[str, Any], key: str) -> bool:
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError(f'El campo «{key}» debe ser verdadero o falso.')
    return value


def _customer(session: Session, key: str, value: Any, current: Any) -> None:
    if value is not None:
        clientes.check_reference(session, key, value, current)


PROJECT_SPEC = crud.Spec(
    model=DesignProject, resource=RESOURCE, not_found='Proyecto de diseño no encontrado.',
    fields=(
        crud.Field('title', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('description', partial(fields.text, required=True, strip=False), required=True),
        crud.Field('owner_id', partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field('customer_id', fields.integer, check=_customer),
        crud.Field('planned_start', _optional_date),
        crud.Field('planned_end', _optional_date),
    ), order_by=(DesignProject.id,),
)
STAGE_SPEC = crud.Spec(
    model=DesignStage, resource=RESOURCE, not_found='Etapa de diseño no encontrada.',
    fields=(
        crud.Field('project_id', partial(fields.integer, required=True), required=True),
        crud.Field('name', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('owner_id', partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field('planned_date', _optional_date),
        crud.Field('done', _boolean),
        crud.Field('done_date', _optional_date),
        crud.Field('position', partial(fields.integer, required=True)),
    ), order_by=(DesignStage.project_id, DesignStage.position, DesignStage.id),
)
PROJECT_WRITABLE = PROJECT_SPEC.writable | {'process_ids'}


def _payload(data: Any, writable: frozenset[str]) -> None:
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, writable)


def _lock_project(session: Session, project_id: int) -> DesignProject:
    found = session.scalar(select(DesignProject).where(DesignProject.id == project_id)
                           .with_for_update().execution_options(populate_existing=True))
    if found is None:
        raise NotFound(PROJECT_SPEC.not_found)
    return found


def _editable(project: DesignProject) -> None:
    if project.state is DesignState.cancelado:
        raise ValidationError(READ_ONLY)
    if project.state is DesignState.completado:
        raise ValidationError('Un proyecto completado es de solo lectura.')


def _snapshot(project: DesignProject) -> dict[str, Any]:
    return audit.snapshot(project) | {'process_ids': project.process_ids}


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc


def _record_update(session: Session, who: Actor, row: Any,
                   before: dict[str, Any]) -> None:
    stamp_updated(row, who)
    after = _snapshot(row) if isinstance(row, DesignProject) else audit.snapshot(row)
    audit.record(session, who, 'update', row, before=before, after=after)


def _project_values(session: Session, data: Mapping[str, Any],
                    found: DesignProject | None = None
                    ) -> tuple[dict[str, Any], list[ProcesoOperacion] | None]:
    values = crud._clean(PROJECT_SPEC, session,
                         {key: value for key, value in data.items() if key != 'process_ids'}, found)
    start = values.get('planned_start', getattr(found, 'planned_start', None))
    end = values.get('planned_end', getattr(found, 'planned_end', None))
    if start is not None and end is not None and end < start:
        raise ValidationError('La fecha final prevista no puede ser anterior a la fecha inicial.')
    linked = None
    if 'process_ids' in data:
        ids = data['process_ids']
        if not isinstance(ids, (list, tuple)) or any(type(value) is not int for value in ids):
            raise ValidationError('El campo «process_ids» debe ser una lista de identificadores.')
        wanted = set(ids)
        linked = list(session.scalars(select(ProcesoOperacion).where(
            ProcesoOperacion.id_proceso.in_([i for i in wanted if people.is_db_id(i)]))
            .order_by(ProcesoOperacion.id_proceso)))
        missing = wanted - {p.id_proceso for p in linked}
        if missing:
            raise ValidationError('El campo «process_ids» contiene identificadores desconocidos: '
                                  f'{", ".join(map(str, sorted(missing)))}.')
    return values, linked


def _create_project(session: Session, who: Actor, data: Mapping[str, Any]) -> DesignProject:
    """Create a draft; the optional starter sequence uses the normal stage writer."""
    policy.require(who, Action.CREATE, RESOURCE)
    _payload(data, PROJECT_WRITABLE | {'starter_stages'})
    starter = _boolean(data, 'starter_stages') if 'starter_stages' in data else False
    columns = {key: value for key, value in data.items() if key != 'starter_stages'}
    fields.require_keys(columns, frozenset(f.name for f in PROJECT_SPEC.fields if f.required))
    values, linked = _project_values(session, columns)
    created = DesignProject(**values, state=DesignState.borrador, processes=linked or [])
    stamp_created(created, who)
    session.add(created)
    try:
        row = audit.record(session, who, 'create', created)
        row.after = _snapshot(created)
        _flush(session)
        if starter:
            for name in STARTER_NAMES:
                _create_stage(session, who, {'project_id': created.id, 'name': name,
                                             'owner_id': created.owner_id}, today=None)
    except IntegrityError as exc:
        raise Conflict() from exc
    return created


def _update_project(session: Session, who: Actor, record_id: int,
                    data: Mapping[str, Any]) -> DesignProject:
    """Replace supplied columns and process links, auditing the parent's snapshot."""
    policy.require(who, Action.UPDATE, RESOURCE)
    _payload(data, PROJECT_WRITABLE)
    found = _lock_project(session, record_id)
    if found.state is DesignState.completado:
        _editable(found)
    values, linked = _project_values(session, data, found)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if linked is not None and {p.id_proceso for p in linked} != set(found.process_ids):
        changes['processes'] = linked
    if not changes:
        return found
    _editable(found)
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    _record_update(session, who, found, before)
    _flush(session)
    return found


def _delete_project(session: Session, who: Actor, record_id: int) -> None:
    """Delete only drafts without stages or IO elements; never cascade evidence."""
    policy.require(who, Action.DELETE, RESOURCE)
    found = _lock_project(session, record_id)
    if found.state is not DesignState.borrador or _stage_rows(session, found.id):
        raise ValidationError('Solo se puede eliminar un proyecto borrador sin etapas.')
    if any(session.scalar(select(model.id).where(model.project_id == found.id).limit(1))
           is not None for model in (DesignInput, DesignOutput)):
        raise ValidationError('No se puede eliminar un proyecto con entradas o salidas.')
    if any(session.scalar(select(model.id).where(model.project_id == found.id).limit(1))
           is not None for model in (DesignPlannedControl, DesignControlRecord)):
        raise ValidationError('No se puede eliminar un proyecto con controles del diseño.')
    audit.record(session, who, 'delete', found, before=_snapshot(found))
    session.delete(found)
    _flush(session)


def may_transition(who: Actor, project: DesignProject | None = None) -> bool:
    """The web-only write grant, narrowed to states with available DD1 transitions."""
    return (who.channel == 'web' and policy.can(who, Action.UPDATE, RESOURCE)
            and (project is None or project.state in TRANSITIONS))


def transition(session: Session, who: Actor, project_id: int, target: str, *,
               cancellation_reason: str | None = None,
               today: date | None = None) -> DesignProject:
    """Plan, start, cancel, or use the separate administrator completion gate."""
    if target == 'completado':
        return complete(session, who, project_id, today=today)
    if not may_transition(who):
        raise PermissionDenied()
    state = fields.enum_member({'state': target}, 'state', DesignState)
    found = _lock_project(session, project_id)
    if state not in TRANSITIONS.get(found.state, ()):
        raise ValidationError('La transición de estado no está permitida.')
    if state is DesignState.planificado and not _stage_rows(session, found.id):
        raise ValidationError('Para planificar el proyecto, registra al menos una etapa.')
    reason = None
    if state is DesignState.cancelado:
        reason = fields.text({'cancellation_reason': cancellation_reason}, 'cancellation_reason')
        if reason is None:
            raise ValidationError('Indica el motivo de la cancelación.')
    before = _snapshot(found)
    found.state = state
    if reason is not None:
        found.cancellation_reason = reason
    _record_update(session, who, found, before)
    _flush(session)
    return found


def completion_blockers(session: Session, project: DesignProject) -> list[str]:
    """Describe unmet gates; completion calls this only after locking the project."""
    from ..models import DesignApprovalState, DesignChange, DesignChangeState
    from . import design_changes, design_controls
    reasons = []
    if project.state is not DesignState.en_curso:
        reasons.append('Solo puedes completar un proyecto en curso.')
    if design_controls.unresolved_planned_controls(session, project.id):
        reasons.append('Resuelve todos los controles planificados antes de completar.')
    versions = design_changes.current_versions(session, project.id)
    if not versions:
        reasons.append('Para completar el proyecto, registra al menos una salida.')
    elif any(v.state is not DesignApprovalState.approved for v in versions):
        reasons.append('Aprueba todas las versiones actuales de las salidas antes de completar.')
    if session.scalar(select(DesignChange.id).where(
            DesignChange.project_id == project.id,
            DesignChange.state == DesignChangeState.proposed).limit(1)) is not None:
        reasons.append('Resuelve todos los cambios propuestos antes de completar.')
    authorized = session.scalars(select(DesignChange.id).where(
        DesignChange.project_id == project.id,
        DesignChange.state == DesignChangeState.authorized))
    # An authorized change must become new output versions before the project closes.
    if any(not design_changes.is_applied(session, change_id) for change_id in authorized):
        reasons.append('Aplica todos los cambios autorizados antes de completar.')
    return reasons


def complete(session: Session, who: Actor, project_id: int, *,
             today: date | None) -> DesignProject:
    """Complete under the shared parent lock, recording an independent approver."""
    from . import design_changes
    design_changes._web_admin(who)
    project = _lock_project(session, project_id)
    _editable(project)
    if project.state is not DesignState.en_curso:
        raise ValidationError('Solo puedes completar un proyecto en curso.')
    person_id = design_changes.approval_person(session, who, project)
    reasons = completion_blockers(session, project)
    if reasons:
        raise ValidationError(reasons[0])
    day = fields.required_date({'today': today}, 'today')
    before = _snapshot(project)
    project.state = DesignState.completado
    project.completed_at, project.completed_by_id = day, person_id
    _record_update(session, who, project, before)
    _flush(session)
    return project


def _stage_rows(session: Session, project_id: int) -> list[DesignStage]:
    return list(session.scalars(select(DesignStage).where(DesignStage.project_id == project_id)
                               .order_by(DesignStage.position)
                               .execution_options(populate_existing=True)))


def _stage_values(session: Session, data: Mapping[str, Any], today: date | None,
                  found: DesignStage | None = None) -> dict[str, Any]:
    values = crud._clean(STAGE_SPEC, session, data, found)
    done = values.get('done', getattr(found, 'done', False))
    done_date = values.get('done_date', getattr(found, 'done_date', None))
    if done != (done_date is not None):
        raise ValidationError('Indica la fecha de finalización solo para una etapa finalizada.')
    if done_date is not None:
        if not isinstance(today, date) or isinstance(today, datetime):
            raise ValueError('today must be a date for stage completion')
        if done_date > today:
            raise ValidationError('La fecha de finalización no puede ser futura.')
    return values


def _position(value: int, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValidationError(f'La posición debe estar entre 1 y {maximum}.')
    return value


def _reposition(session: Session, who: Actor, rows: list[DesignStage], *,
                changes: dict[int, dict[str, Any]] | None = None) -> None:
    """Park changed rows above the maximum, then audit final contiguous positions.

    Both SQLite and PostgreSQL check UNIQUE immediately. Positive, disjoint
    temporary slots avoid collisions without a PostgreSQL-only deferred key.
    Temporary Core writes are never audit events; final before/after snapshots
    record every affected row exactly once in the same transaction.
    """
    changes = changes or {}
    affected = [(row, position) for position, row in enumerate(rows, 1)
                if row.position != position or row.id in changes]
    if any(row.done for row, _ in affected):
        raise ValidationError(DONE_LOCKED)
    before = {row.id: audit.snapshot(row) for row, _ in affected}
    maximum = max((row.position for row in rows), default=0) + len(rows)
    for offset, (row, position) in enumerate(affected, 1):
        if row.position != position:
            session.execute(DesignStage.__table__.update().where(DesignStage.id == row.id)
                            .values(position=maximum + offset))
    for row, position in affected:
        for key, value in changes.get(row.id, {}).items():
            setattr(row, key, value)
        row.position = position
        _record_update(session, who, row, before[row.id])
    _flush(session)


def _create_stage(session: Session, who: Actor, data: Mapping[str, Any], *,
                  today: date | None) -> DesignStage:
    """Append/insert a stage while holding its parent's exclusive row lock."""
    policy.require(who, Action.CREATE, RESOURCE)
    _payload(data, STAGE_SPEC.writable)
    fields.require_keys(data, frozenset(f.name for f in STAGE_SPEC.fields if f.required))
    project_id = fields.integer(data, 'project_id', required=True)
    project = _lock_project(session, project_id)
    _editable(project)
    values = _stage_values(session, data, today)
    rows = _stage_rows(session, project_id)
    position = _position(values.pop('position', len(rows) + 1), len(rows) + 1)
    # Validate a reorder before creating anything, including its audit row.
    if (any(row.done for row in rows[position - 1:])
            or (values.get('done', False) and position != len(rows) + 1)):
        raise ValidationError(DONE_LOCKED)
    created = DesignStage(**values, position=len(rows) + 1)
    stamp_created(created, who)
    session.add(created)
    audit.record(session, who, 'create', created)
    _flush(session)
    if position != len(rows) + 1:
        rows.insert(position - 1, created)
        _reposition(session, who, rows)
    return created


def _stage_parent(session: Session, record_id: int) -> tuple[DesignProject, DesignStage]:
    # The writer already required UPDATE; do not also require a read token scope.
    found = crud._load(STAGE_SPEC, session, record_id)
    parent = _lock_project(session, found.project_id)
    # Refresh only after the parent lock: concurrent updates/deletes are now visible.
    row = session.scalar(select(DesignStage).where(DesignStage.id == record_id)
                         .execution_options(populate_existing=True))
    if row is None:
        raise NotFound(STAGE_SPEC.not_found)
    return parent, row


def _update_stage(session: Session, who: Actor, record_id: int, data: Mapping[str, Any], *,
                  today: date) -> DesignStage:
    """Edit or move an unfinished stage, never changing its parent."""
    policy.require(who, Action.UPDATE, RESOURCE)
    _payload(data, STAGE_SPEC.writable)
    parent, found = _stage_parent(session, record_id)
    if parent.state is DesignState.completado:
        _editable(parent)
    values = _stage_values(session, data, today, found)
    if values.get('project_id', found.project_id) != found.project_id:
        raise ValidationError('Una etapa no puede pasar a otro proyecto.')
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if not changes:
        return found
    _editable(parent)
    if found.done:
        raise ValidationError(DONE_LOCKED)
    rows = _stage_rows(session, parent.id)
    position = _position(changes.pop('position', found.position), len(rows))
    rows.remove(found)
    rows.insert(position - 1, found)
    _reposition(session, who, rows, changes={found.id: changes})
    return found


def _delete_stage(session: Session, who: Actor, record_id: int) -> None:
    """Both maintaining roles delete unfinished stages on the web, never via MCP."""
    policy.require(who, Action.UPDATE, RESOURCE)
    if who.channel == 'mcp':
        raise PermissionDenied()
    parent, found = _stage_parent(session, record_id)
    _editable(parent)
    if any(session.scalar(select(model.id).where(model.stage_id == found.id).limit(1))
           is not None for model in (DesignPlannedControl, DesignControlRecord)):
        raise ValidationError('No se puede eliminar una etapa citada por controles del diseño.')
    rows = _stage_rows(session, parent.id)
    if any(row.done for row in rows if row.position >= found.position):
        raise ValidationError(DONE_LOCKED)
    rows.remove(found)
    audit.record(session, who, 'delete', found)
    session.delete(found)
    _flush(session)
    _reposition(session, who, rows)


def _project_conditions(state: Any = None, owner_id: int | None = None,
                        customer_id: int | None = None) -> list[Any]:
    where = []
    if state is not None:
        where.append(DesignProject.state == fields.enum_member({'state': state}, 'state', DesignState))
    for column, value in ((DesignProject.owner_id, owner_id), (DesignProject.customer_id, customer_id)):
        if value is not None:
            where.append(column == fields.integer({column.key: value}, column.key, required=True))
    return where


def _stage_conditions(project_id: int | None = None) -> list[Any]:
    return [] if project_id is None else [DesignStage.project_id == project_id]


projects = Register(PROJECT_SPEC, _project_conditions, _create_project, _update_project, _delete_project)
stages = Register(STAGE_SPEC, _stage_conditions, _create_stage, _update_stage, _delete_stage)
