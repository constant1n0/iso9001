"""Design control planning and append-only evidence (DD3, ISO 9001 8.3.4).

Every write locks and refreshes the project before content/state validation.
Plans are editable only while unresolved, in draft/planned/in-progress projects;
records can be appended in planned/in-progress projects, including repeats and
backdated occurrences. Evidence is never edited/deleted. Plans are withdrawn,
never deleted, so omitted controls always carry an explicit justification.
A withdrawal or ANY linked conforming/conforming-with-observations occurrence
resolves a plan; later nonconforming evidence does not undo that resolution.
DD4 must call unresolved_planned_controls while holding the same parent lock.
Services reuse CRUD validation, attribution and audit; they never commit.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import (DesignControlKind, DesignControlRecord, DesignControlResult,
                      DesignPlannedControl, DesignStage, DesignState, Person)
from . import crud, design, fields, people, policy
from .actor import Actor
from .errors import NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

IMMUTABLE = ('Los controles registrados no se modifican ni se eliminan; '
             'registra uno nuevo.')
PLAN_RESOLVED = 'Un control planificado resuelto es de solo lectura.'
WITHDRAWAL_REQUIRED = 'Justifica por qué se retira el control planificado.'
ACTIONS_REQUIRED = 'Indica las acciones para un resultado distinto de «conforme».'
STAGE_REQUIRED = 'Elige una etapa del mismo proyecto.'
PLAN_REQUIRED = 'Elige un control planificado del mismo proyecto y tipo.'
RECORD_STATES = (DesignState.planificado, DesignState.en_curso)
CONFORMING = (DesignControlResult.conforming,
              DesignControlResult.conforming_with_observations)


def _common() -> tuple[crud.Field, ...]:
    """Fields shared by plans and evidence; person validation is locked below."""
    return (
        crud.Field('project_id', partial(fields.integer, required=True), required=True),
        crud.Field('kind', partial(fields.enum_member, enum_cls=DesignControlKind),
                   required=True),
        crud.Field('responsible_id', partial(fields.integer, required=True), required=True),
        crud.Field('stage_id', fields.integer),
    )


PLAN_SPEC = crud.Spec(
    model=DesignPlannedControl, resource=Resource.DESIGN,
    not_found='Control planificado no encontrado.',
    fields=(*_common(),
        crud.Field('description', partial(fields.text, required=True), required=True),
        crud.Field('planned_date', design._optional_date),
        crud.Field('withdrawn', design._boolean),
        crud.Field('withdrawal_reason', fields.text),
    ), order_by=(DesignPlannedControl.project_id, DesignPlannedControl.id),
)
RECORD_SPEC = crud.Spec(
    model=DesignControlRecord, resource=Resource.DESIGN,
    not_found='Control registrado no encontrado.',
    fields=(*_common(),
        crud.Field('date', fields.required_date, required=True),
        crud.Field('result', partial(fields.enum_member, enum_cls=DesignControlResult),
                   required=True),
        crud.Field('evidence', partial(fields.text, required=True), required=True),
        crud.Field('actions', fields.text),
        crud.Field('planned_control_id', fields.integer),
    ), order_by=(DesignControlRecord.date.desc(), DesignControlRecord.id.desc()),
)


def _conforming_record() -> Any:
    """Correlated existence predicate shared by the resolution readers."""
    return exists().where(
        DesignControlRecord.planned_control_id == DesignPlannedControl.id,
        DesignControlRecord.result.in_(CONFORMING))


def unresolved_planned_controls(session: Session, project_id: int
                                ) -> list[DesignPlannedControl]:
    """Return unresolved plans, id ordered; DD4 calls this under the project lock.

    No approval/completion mutation occurs here. Withdrawn plans count as resolved
    even without evidence, and unlinked or nonconforming records do not resolve.
    """
    return list(session.scalars(select(DesignPlannedControl).where(
        DesignPlannedControl.project_id == project_id,
        DesignPlannedControl.withdrawn.is_(False), ~_conforming_record())
        .order_by(DesignPlannedControl.id).execution_options(populate_existing=True)))


def is_resolved(session: Session, plan: DesignPlannedControl) -> bool:
    """Read resolution from stored withdrawal/evidence, never from cached relations."""
    return bool(session.scalar(select(
        DesignPlannedControl.withdrawn | _conforming_record()).where(
            DesignPlannedControl.id == plan.id)))


def _values(spec: crud.Spec, session: Session, data: Mapping[str, Any],
            found: Any = None) -> dict[str, Any]:
    """Validate all content/citations before mutation, under the project's lock."""
    values = crud._clean(spec, session, data, found)
    project_id = values.get('project_id', getattr(found, 'project_id', None))
    if found is not None and project_id != found.project_id:
        raise ValidationError('Un control no puede pasar a otro proyecto.')
    person_id = values.get('responsible_id')
    current_person = getattr(found, 'responsible_id', None)
    if person_id is not None and person_id != current_person:
        if people.is_db_id(person_id):
            session.scalar(select(Person).where(Person.id == person_id)
                           .with_for_update(read=True)
                           .execution_options(populate_existing=True))
        people.check_reference(session, 'responsible_id', person_id, current_person)
    stage_id = values.get('stage_id')
    if stage_id is not None:
        stage = (session.scalar(select(DesignStage).where(DesignStage.id == stage_id)
                                .execution_options(populate_existing=True))
                 if people.is_db_id(stage_id) else None)
        if stage is None or stage.project_id != project_id:
            raise ValidationError(STAGE_REQUIRED)
    if spec is PLAN_SPEC:
        withdrawn = values.get('withdrawn', getattr(found, 'withdrawn', False))
        reason = values.get('withdrawal_reason', getattr(found, 'withdrawal_reason', None))
        if withdrawn and not reason:
            raise ValidationError(WITHDRAWAL_REQUIRED)
        if not withdrawn and reason is not None:
            raise ValidationError('El motivo de retirada requiere retirar el control.')
        if found is not None and values.get('kind', found.kind) != found.kind:
            if session.scalar(select(DesignControlRecord.id).where(
                    DesignControlRecord.planned_control_id == found.id).limit(1)) is not None:
                raise ValidationError('No puedes cambiar el tipo de un control citado por registros.')
    else:
        if values['result'] is not DesignControlResult.conforming and not values.get('actions'):
            raise ValidationError(ACTIONS_REQUIRED)
        plan_id = values.get('planned_control_id')
        if plan_id is not None:
            # The caller may have loaded the plan before waiting for the lock.
            plan = (session.scalar(select(DesignPlannedControl).where(
                DesignPlannedControl.id == plan_id)
                .execution_options(populate_existing=True))
                if people.is_db_id(plan_id) else None)
            if plan is None or plan.project_id != project_id or plan.kind != values['kind']:
                raise ValidationError(PLAN_REQUIRED)
    return values


def _create(spec: crud.Spec, session: Session, who: Actor,
            data: Mapping[str, Any], *, today: date | None = None) -> Any:
    """Append a plan or immutable occurrence with an audited CRUD insert."""
    policy.require(who, Action.CREATE, Resource.DESIGN)
    # Only the parent id is parsed before locking; every other rule follows it.
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset({'project_id'}))
    project = design._lock_project(session, fields.integer(data, 'project_id', required=True))
    design._editable(project)
    design._payload(data, spec.writable)
    fields.require_keys(data, frozenset(f.name for f in spec.fields if f.required))
    if spec is RECORD_SPEC and project.state not in RECORD_STATES:
        raise ValidationError('Solo puedes registrar controles en proyectos planificados o en curso.')
    values = _values(spec, session, data)
    if spec is RECORD_SPEC:
        if not isinstance(today, date) or isinstance(today, datetime):
            raise ValueError('today must be a date')
        if values['date'] > today:
            raise ValidationError('La fecha del control no puede ser futura.')
    return crud.create(spec, session, who, values)


def _update_plan(session: Session, who: Actor, record_id: int,
                 data: Mapping[str, Any]) -> DesignPlannedControl:
    """Edit an unresolved plan or withdraw it with an explicit justification."""
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    found = crud._load(PLAN_SPEC, session, record_id)
    parent = design._lock_project(session, found.project_id)
    found = session.scalar(select(DesignPlannedControl).where(
        DesignPlannedControl.id == record_id).execution_options(populate_existing=True))
    if found is None:
        raise NotFound(PLAN_SPEC.not_found)
    design._editable(parent)
    values = _values(PLAN_SPEC, session, data, found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    if is_resolved(session, found):
        raise ValidationError(PLAN_RESOLVED)
    return crud.update(PLAN_SPEC, session, who, record_id, values)


def _immutable_update(session: Session, who: Actor, record_id: int,
                      data: Mapping[str, Any], *, today: date | None = None
                      ) -> DesignControlRecord:
    """Refuse every update, even a no-op, mirroring communication/equipment."""
    policy.require(who, Action.UPDATE, Resource.DESIGN)
    crud._load(RECORD_SPEC, session, record_id)
    raise ValidationError(IMMUTABLE)


def _no_delete(spec: crud.Spec, session: Session, who: Actor, record_id: int) -> None:
    """Retain evidence and planned omissions; MCP's delete grant is always denied."""
    policy.require(who, Action.DELETE, Resource.DESIGN)
    crud._load(spec, session, record_id)
    raise ValidationError(IMMUTABLE if spec is RECORD_SPEC else
                          'Los controles planificados no se eliminan; retíralos con justificación.')


def _conditions(model: type, project_id: int | None = None, kind: Any = None,
                result: Any = None) -> list[Any]:
    """AND-combined typed project/kind/result filters (result is records only)."""
    where = []
    if project_id is not None:
        where.append(model.project_id == fields.integer(
            {'project_id': project_id}, 'project_id', required=True))
    if kind is not None:
        where.append(model.kind == fields.enum_member({'kind': kind}, 'kind', DesignControlKind))
    if result is not None:
        if model is not DesignControlRecord:
            raise ValidationError('El filtro de resultado solo se aplica al registro.')
        where.append(model.result == fields.enum_member(
            {'result': result}, 'result', DesignControlResult))
    return where


plans = Register(PLAN_SPEC, partial(_conditions, DesignPlannedControl),
                 partial(_create, PLAN_SPEC), _update_plan, partial(_no_delete, PLAN_SPEC))
records = Register(RECORD_SPEC, partial(_conditions, DesignControlRecord),
                   partial(_create, RECORD_SPEC), _immutable_update,
                   partial(_no_delete, RECORD_SPEC))
