"""Customer complaints (9.1.2): received -> responded -> closed, or cancelled.

Only an administrator reopens a terminal complaint. All transitions are web-only;
ordinary writes never accept lifecycle fields. Locks serialize edits/transitions,
audit rows preserve the historic response, closing date and cancellation reason.
The adapter supplies today; services flush but never commit or read the clock.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import CanalContacto, EstadoReclamacion as State, NoConformidad, Reclamacion, RoleEnum
from . import audit, clientes, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

TERMINAL_STATES = (State.cerrada, State.cancelada)
READ_ONLY_MESSAGE = ('Esta reclamación está cerrada o cancelada y no se puede modificar; '
                     'un administrador puede reabrirla.')
BAD_STATE = 'El estado de la reclamación no permite esta operación.'
BAD_DATE = 'La fecha no puede ser anterior a la etapa precedente.'


def _channel(data: Mapping[str, Any], key: str) -> Any:
    return None if data[key] in (None, '') else fields.enum_member(data, key, CanalContacto)


def _nc_reference(session: Session, key: str, value: Any, current: Any) -> None:
    if value is not None and (not people.is_db_id(value) or session.get(NoConformidad, value) is None):
        raise ValidationError('El campo «no_conformidad_id» no corresponde a ninguna no conformidad.')


SPEC = crud.Spec(
    model=Reclamacion, resource=Resource.COMPLAINTS, not_found='Reclamación no encontrada.',
    fields=(
        crud.Field('cliente_id', partial(fields.integer, required=True), required=True,
                   check=clientes.check_reference),
        crud.Field('fecha_recepcion', fields.required_date),
        crud.Field('descripcion', partial(fields.text, required=True), required=True),
        crud.Field('canal', _channel),
        crud.Field('no_conformidad_id', fields.integer, check=_nc_reference),
    ),
    order_by=(Reclamacion.fecha_recepcion.desc(), Reclamacion.id.desc()),
)
get = partial(crud.get, SPEC)


def _today(today: Any) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    return today


def _dates(data: Any, found: Reclamacion | None, today: date) -> None:
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, SPEC.writable)
    if 'fecha_recepcion' in data:
        received = fields.required_date(data, 'fecha_recepcion')
        if received > today:
            raise ValidationError('La fecha de recepción no puede ser futura.')
        if found is not None and found.fecha_respuesta is not None and received > found.fecha_respuesta:
            raise ValidationError(BAD_DATE)


def create(session: Session, actor: Actor, data: Mapping[str, Any], *, today: date) -> Reclamacion:
    policy.require(actor, Action.CREATE, Resource.COMPLAINTS)
    today = _today(today)
    _dates(data, None, today)
    return crud.create(SPEC, session, actor, {'fecha_recepcion': today} | dict(data))


def lock(session: Session, complaint_id: int) -> Reclamacion:
    found = None
    if people.is_db_id(complaint_id):
        found = session.scalar(select(Reclamacion).where(Reclamacion.id == complaint_id)
                               .with_for_update().execution_options(populate_existing=True))
    if found is None:
        raise NotFound(SPEC.not_found)
    return found


def update(session: Session, actor: Actor, complaint_id: int, data: Mapping[str, Any], *,
           today: date) -> Reclamacion:
    policy.require(actor, Action.UPDATE, Resource.COMPLAINTS)
    today = _today(today)
    found = lock(session, complaint_id)
    if found.estado in TERMINAL_STATES:
        raise ValidationError(READ_ONLY_MESSAGE)
    _dates(data, found, today)
    return crud.update(SPEC, session, actor, complaint_id, data)


def delete(session: Session, actor: Actor, complaint_id: int) -> None:
    policy.require(actor, Action.DELETE, Resource.COMPLAINTS)
    lock(session, complaint_id)
    crud.delete(SPEC, session, actor, complaint_id)


def _conditions(cliente_id: int | None = None, estado: Any = None, canal: Any = None) -> list[Any]:
    where = []
    if cliente_id is not None:
        where.append(Reclamacion.cliente_id == cliente_id if people.is_db_id(cliente_id) else false())
    for key, value, enum_cls in (('estado', estado, State), ('canal', canal, CanalContacto)):
        if value is not None and value != '':
            where.append(getattr(Reclamacion, key) == fields.enum_member({key: value}, key, enum_cls))
    return where


def list_(session: Session, actor: Actor, *, cliente_id: int | None = None,
          estado: Any = None, canal: Any = None) -> list[Reclamacion]:
    policy.require(actor, Action.READ, Resource.COMPLAINTS)
    return crud.list_(SPEC, session, actor, _conditions(cliente_id, estado, canal))


def list_page(session: Session, actor: Actor, *, cliente_id: int | None = None,
              estado: Any = None, canal: Any = None, page: int = 1,
              per_page: int = crud.DEFAULT_PER_PAGE) -> tuple[list[Reclamacion], int]:
    policy.require(actor, Action.READ, Resource.COMPLAINTS)
    return crud.list_page(SPEC, session, actor, _conditions(cliente_id, estado, canal),
                          page=page, per_page=per_page)


def _may(actor: Actor) -> bool:
    return actor.channel != 'mcp' and policy.can(actor, Action.UPDATE, Resource.COMPLAINTS)


def may_respond(actor: Actor, found: Reclamacion) -> bool:
    return _may(actor) and found.estado is State.recibida


def may_close(actor: Actor, found: Reclamacion) -> bool:
    return _may(actor) and found.estado is State.respondida


def may_cancel(actor: Actor, found: Reclamacion) -> bool:
    return _may(actor) and found.estado is State.recibida


def may_reopen(actor: Actor, found: Reclamacion) -> bool:
    return _may(actor) and actor.role is RoleEnum.ADMINISTRADOR and found.estado in TERMINAL_STATES


def _require_transition(actor: Actor, *, admin: bool = False) -> None:
    policy.require(actor, Action.UPDATE, Resource.COMPLAINTS)
    if actor.channel == 'mcp' or (admin and actor.role is not RoleEnum.ADMINISTRADOR):
        raise PermissionDenied()


def _transition(session: Session, actor: Actor, found: Reclamacion, **values: Any) -> Reclamacion:
    before = audit.snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, 'update', found, before=before)
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc
    return found


def respond(session: Session, actor: Actor, complaint_id: int, respuesta: str | None, *,
            today: date) -> Reclamacion:
    _require_transition(actor)
    today = _today(today)
    found = lock(session, complaint_id)
    if found.estado is not State.recibida:
        raise ValidationError(BAD_STATE)
    response = fields.text({'respuesta': respuesta}, 'respuesta', required=True)
    if today < found.fecha_recepcion:
        raise ValidationError(BAD_DATE)
    return _transition(session, actor, found, estado=State.respondida, respuesta=response,
                       fecha_respuesta=today)


def close(session: Session, actor: Actor, complaint_id: int, *, today: date) -> Reclamacion:
    _require_transition(actor)
    today = _today(today)
    found = lock(session, complaint_id)
    if found.estado is not State.respondida:
        raise ValidationError(BAD_STATE)
    if today < found.fecha_respuesta:
        raise ValidationError(BAD_DATE)
    return _transition(session, actor, found, estado=State.cerrada, fecha_cierre=today)


def cancel(session: Session, actor: Actor, complaint_id: int, motivo: str | None) -> Reclamacion:
    _require_transition(actor)
    found = lock(session, complaint_id)
    if found.estado is not State.recibida:
        raise ValidationError(BAD_STATE)
    reason = fields.text({'motivo_cancelacion': motivo}, 'motivo_cancelacion', required=True)
    return _transition(session, actor, found, estado=State.cancelada, motivo_cancelacion=reason)


def reopen(session: Session, actor: Actor, complaint_id: int) -> Reclamacion:
    _require_transition(actor, admin=True)
    found = lock(session, complaint_id)
    if found.estado not in TERMINAL_STATES:
        raise ValidationError(BAD_STATE)
    state = State.respondida if found.estado is State.cerrada else State.recibida
    return _transition(session, actor, found, estado=state, fecha_cierre=None, motivo_cancelacion=None)
