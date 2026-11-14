"""PS1 orders: web-only lifecycle, exact citations and parent-snapshot auditing.

Services flush, never commit. Creation owns its newly inserted row; all existing
order writes lock and refresh it before inspecting state or refreshing targets.
Only planned orders may be deleted by web administrators, including their links.
Finished means work finished, never a release decision (8.6 is a later module).
"""
from collections.abc import Mapping
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit_notifications import local_today
from ..models import (Cliente, DesignApprovalState, DesignOutput, DesignOutputVersion,
                      DocumentRevision, EquipoMedicion, Person, ProcesoOperacion,
                      ProductionKind, ProductionOrder, ProductionState)
from . import audit, crud, design, design_io, equipment, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.PRODUCTION
INVALID_REVISION = design_io.INVALID_REVISION
READ_ONLY = 'Una orden terminada o cancelada es de solo lectura.'
TRANSITIONS = {
    ProductionState.planned: (ProductionState.in_progress, ProductionState.cancelled),
    ProductionState.in_progress: (ProductionState.finished, ProductionState.cancelled),
}
LINKS = {'revision_ids': ('revisions', DocumentRevision),
         'equipment_ids': ('equipment', EquipoMedicion), 'person_ids': ('people', Person)}
SPEC = crud.Spec(
    model=ProductionOrder, resource=RESOURCE, not_found='Orden no encontrada.',
    fields=(
        crud.Field('code', partial(fields.text, required=True, max_length=50), required=True),
        crud.Field('kind', partial(fields.enum_member, enum_cls=ProductionKind), required=True),
        crud.Field('title', partial(fields.text, required=True, strip=False), required=True),
        crud.Field('process_id', partial(fields.integer, required=True), required=True),
        *(crud.Field(key, fields.integer) for key in ('customer_id', 'design_output_id', 'owner_id')),
        crud.Field('lot', fields.text),
        *(crud.Field(key, design._optional_date) for key in ('planned_start', 'planned_end')),
    ), order_by=(ProductionOrder.code, ProductionOrder.id),
    conflict='Ya existe una orden con ese código.',
)
WRITABLE = SPEC.writable | LINKS.keys()


def link_ids(row: ProductionOrder) -> dict[str, list[int]]:
    """Stable association ids shared by audit snapshots and MCP reads."""
    return {key: getattr(row, key) for key in LINKS}


def _snapshot(row: ProductionOrder) -> dict[str, Any]:
    return audit.snapshot(row) | link_ids(row)


def _lock(session: Session, record_id: int) -> ProductionOrder:
    row = session.scalar(select(ProductionOrder).where(ProductionOrder.id == record_id)
                         .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise NotFound(SPEC.not_found)
    # Select-in relationships may have been loaded before a competing commit.
    session.expire(row, [relation for relation, _ in LINKS.values()])
    return row


def _editable(row: ProductionOrder) -> None:
    if row.state not in TRANSITIONS:
        raise ValidationError(READ_ONLY)


def _target(session: Session, model: type, value: int, key: str) -> Any:
    column = ProcesoOperacion.id_proceso if model is ProcesoOperacion else model.id
    row = None if not people.is_db_id(value) else session.scalar(select(model).where(
        column == value).with_for_update(read=True).execution_options(populate_existing=True))
    if row is None:
        raise ValidationError(f'El campo «{key}» no corresponde a ningún registro.')
    return row


def _clean(session: Session, who: Actor, data: Mapping[str, Any],
           found: ProductionOrder | None = None) -> tuple[dict, dict]:
    design._payload(data, WRITABLE)
    values = crud._clean(SPEC, session, {k: v for k, v in data.items() if k not in LINKS}, found)
    start = values.get('planned_start', getattr(found, 'planned_start', None))
    end = values.get('planned_end', getattr(found, 'planned_end', None))
    if start is not None and end is not None and end < start:
        raise ValidationError('La fecha final prevista no puede ser anterior a la fecha inicial.')
    for key, model in (('process_id', ProcesoOperacion), ('customer_id', Cliente),
                       ('owner_id', Person), ('design_output_id', DesignOutput)):
        value = values.get(key)
        if value is None:
            continue
        _target(session, model, value, key)
        current = getattr(found, key, None)
        if key == 'owner_id':
            people.check_reference(session, key, value, current)
        if key == 'design_output_id' and value != current:
            version = session.scalar(select(DesignOutputVersion).where(
                DesignOutputVersion.output_id == value).with_for_update(read=True)
                .execution_options(populate_existing=True))
            if version is None or version.state is not DesignApprovalState.approved:
                raise ValidationError('Elige una versión aprobada de una salida del diseño.')
    code = values.get('code')
    if code is not None and session.scalar(select(ProductionOrder.id).where(
            ProductionOrder.code == code,
            ProductionOrder.id != getattr(found, 'id', 0))) is not None:
        raise Conflict(SPEC.conflict)
    linked = {}
    for key, (relation, model) in LINKS.items():
        if key not in data:
            continue
        ids = data[key]
        if not isinstance(ids, (list, tuple)) or any(not people.is_db_id(i) for i in ids):
            raise ValidationError(f'El campo «{key}» debe ser una lista de identificadores.')
        current = set(getattr(found, key, []))
        rows = []
        for value in sorted(set(ids)):
            if key == 'revision_ids' and value not in current:
                if design_io.visible_revision(session, who, value) is None:
                    raise ValidationError(INVALID_REVISION)
            row = _target(session, model, value, key)
            if value not in current:
                if key == 'revision_ids' and (row.estado not in design_io.LINKABLE_STATES
                        or design_io.visible_revision(session, who, value) is None):
                    raise ValidationError(INVALID_REVISION)
                if key == 'equipment_ids' and equipment.status(session, row, today=local_today()) != 'apto':
                    raise ValidationError('Elige un equipo apto.')
                if key == 'person_ids':
                    people.check_reference(session, key, value)
            rows.append(row)
        linked[relation] = rows
    return values, linked


def _create(session: Session, who: Actor, data: Mapping[str, Any]) -> ProductionOrder:
    """Validate all citations before insertion; own and lock the new order row."""
    policy.require(who, Action.CREATE, RESOURCE)
    design._payload(data, WRITABLE)
    fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values, linked = _clean(session, who, data)
    row = ProductionOrder(**values, **({relation: [] for relation, _ in LINKS.values()} | linked))
    stamp_created(row, who)
    session.add(row)
    try:
        log = audit.record(session, who, 'create', row)
        log.after = _snapshot(row)
        design._flush(session)
        _lock(session, row.id)
    except IntegrityError as exc:
        raise Conflict(SPEC.conflict) from exc
    return row


def _record(session: Session, who: Actor, row: ProductionOrder, before: dict) -> None:
    stamp_updated(row, who)
    audit.record(session, who, 'update', row, before=before, after=_snapshot(row))
    design._flush(session)


def _update(session: Session, who: Actor, record_id: int, data: Mapping[str, Any]) -> ProductionOrder:
    """Replace supplied fields/sets; even no-op writes to terminal orders are refused."""
    policy.require(who, Action.UPDATE, RESOURCE)
    row = _lock(session, record_id)
    _editable(row)
    values, linked = _clean(session, who, data, row)
    changes = {key: value for key, value in (values | linked).items() if getattr(row, key) != value}
    if changes:
        before = _snapshot(row)
        for key, value in changes.items():
            setattr(row, key, value)
        _record(session, who, row, before)
    return row


def _delete(session: Session, who: Actor, record_id: int) -> None:
    """Web administrators delete planned orders only, removing their outbound links."""
    policy.require(who, Action.DELETE, RESOURCE)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    if row.state is not ProductionState.planned:
        raise ValidationError('Solo puedes eliminar órdenes planificadas.')
    audit.record(session, who, 'delete', row, before=_snapshot(row))
    session.delete(row)
    design._flush(session)


def transition(session: Session, who: Actor, record_id: int, target: str, *,
               cancellation_reason: str | None = None) -> ProductionOrder:
    """Start, finish work or cancel with a reason, never authorize delivery."""
    policy.require(who, Action.UPDATE, RESOURCE)
    if who.channel != 'web':
        raise PermissionDenied()
    row = _lock(session, record_id)
    state = fields.enum_member({'state': target}, 'state', ProductionState)
    if state not in TRANSITIONS.get(row.state, ()):
        raise ValidationError('La transición de estado no está permitida.')
    reason = fields.text({'reason': cancellation_reason}, 'reason')
    if state is ProductionState.cancelled and not reason:
        raise ValidationError('Indica el motivo de la cancelación.')
    if state is not ProductionState.cancelled and reason:
        # An explicit reason for another transition is refused, never silently dropped.
        raise ValidationError('El motivo solo se indica al cancelar la orden.')
    before = _snapshot(row)
    row.state, row.cancellation_reason = state, reason
    _record(session, who, row, before)
    return row


def _conditions(state: Any = None, kind: Any = None, process_id: int | None = None,
                customer_id: int | None = None, lot: str | None = None) -> list[Any]:
    where = []
    for key, value, members in (('state', state, ProductionState), ('kind', kind, ProductionKind)):
        if value is not None:
            where.append(getattr(ProductionOrder, key) == fields.enum_member({key: value}, key, members))
    for key, value in (('process_id', process_id), ('customer_id', customer_id)):
        if value is not None:
            where.append(getattr(ProductionOrder, key) == fields.integer({key: value}, key, required=True))
    if lot:
        where.append(ProductionOrder.lot.ilike(f'%{lot}%'))
    return where


orders = Register(SPEC, _conditions, _create, _update, _delete)
