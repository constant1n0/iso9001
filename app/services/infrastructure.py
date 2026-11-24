"""Infrastructure plan and append-only maintenance evidence (ISO 9001 7.1.3).

The element lock serializes edits, retirement, deletion and history appends.
Dates are explicit, never calculated. Services validate before mutation, stamp
and audit both rows, flush but never commit; adapters own the transaction.
Process sets use the parent's audit snapshot, not separate association events.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (ElementoInfraestructura, MantenimientoInfraestructura, Person,
                      ProcesoOperacion, TipoInfraestructura, TipoMantenimiento, ResultadoMantenimiento)
from . import audit, crud, fields, nonconformities, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.INFRASTRUCTURE
IMMUTABLE = 'Los mantenimientos no se modifican ni se eliminan; registra uno nuevo.'
BACKDATED = 'El mantenimiento no puede ser anterior al último registrado.'
RETIRED = 'No se pueden registrar mantenimientos de un elemento retirado.'
NEXT_REQUIRED = 'Indica una nueva próxima fecha posterior al mantenimiento.'
ACTIONS_REQUIRED = 'Un resultado no conforme requiere indicar las acciones realizadas.'


def _retired(data, key):
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError('El campo «retirado» debe ser verdadero o falso.')
    return value


SPEC = crud.Spec(
    model=ElementoInfraestructura, resource=RESOURCE,
    not_found='Elemento de infraestructura no encontrado.',
    fields=(
        crud.Field('codigo', partial(fields.text, required=True, max_length=50), required=True),
        crud.Field('nombre', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('tipo', partial(fields.enum_member, enum_cls=TipoInfraestructura), required=True),
        crud.Field('responsable_id', fields.integer, check=people.check_reference),
        crud.Field('ubicacion', partial(fields.text, max_length=200)),
        crud.Field('frecuencia', partial(fields.text, required=True), required=True),
        crud.Field('fecha_proxima', fields.required_date, required=True),
        crud.Field('retirado', _retired),
    ),
    order_by=(ElementoInfraestructura.codigo, ElementoInfraestructura.id),
    conflict='Ya existe un elemento con ese código.',
)
WRITABLE = SPEC.writable | {'procesos'}
MAINTENANCE_SPEC = crud.Spec(
    model=MantenimientoInfraestructura, resource=RESOURCE,
    not_found='Mantenimiento no encontrado.',
    fields=(
        crud.Field('elemento_id', partial(fields.integer, required=True), required=True),
        crud.Field('fecha', fields.required_date, required=True),
        crud.Field('tipo', partial(fields.enum_member, enum_cls=TipoMantenimiento), required=True),
        crud.Field('resultado', partial(fields.enum_member, enum_cls=ResultadoMantenimiento), required=True),
        crud.Field('realizado_por', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('evidencia', partial(fields.text, required=True), required=True),
        crud.Field('acciones', fields.text),
        crud.Field('no_conformidad_id', fields.integer),
        crud.Field('fecha_proxima', fields.required_date, required=True),
    ),
    order_by=(MantenimientoInfraestructura.fecha.desc(), MantenimientoInfraestructura.id.desc()),
)


def is_overdue(item: ElementoInfraestructura, *, today: date) -> bool:
    """A due date remains valid through that entire day; retirement suppresses it."""
    return not item.retirado and item.fecha_proxima < today


def _snapshot(item):
    return audit.snapshot(item) | {'procesos': item.proceso_ids}


def _lock(session: Session, record_id: int) -> ElementoInfraestructura:
    found = None
    if people.is_db_id(record_id):
        found = session.scalar(select(ElementoInfraestructura).where(
            ElementoInfraestructura.id == record_id).with_for_update()
            .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(SPEC.not_found)
    return found


def _clean(session, data, found=None):
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, WRITABLE)
    columns = {key: value for key, value in data.items() if key != 'procesos'}
    owner = columns.get('responsable_id')
    if people.is_db_id(owner):
        session.scalar(select(Person).where(Person.id == owner).with_for_update(read=True)
                       .execution_options(populate_existing=True))
    values = crud._clean(SPEC, session, columns, found)
    if 'procesos' in data:
        ids = data['procesos']
        if not isinstance(ids, (list, tuple)) or any(type(value) is not int for value in ids):
            raise ValidationError('El campo «procesos» debe ser una lista de identificadores.')
        wanted = set(ids)
        searchable = [value for value in wanted if people.is_db_id(value)]
        rows = list(session.scalars(select(ProcesoOperacion).where(
            ProcesoOperacion.id_proceso.in_(searchable)).order_by(ProcesoOperacion.id_proceso)
            .with_for_update(read=True))) if searchable else []
        missing = sorted(wanted - {row.id_proceso for row in rows})
        if missing:
            raise ValidationError('El campo «procesos» contiene identificadores desconocidos: '
                                  + ', '.join(map(str, missing)) + '.')
        values['procesos'] = rows
    return values


def _create(session: Session, actor: Actor, data: Mapping[str, Any]):
    policy.require(actor, Action.CREATE, RESOURCE)
    if isinstance(data, Mapping):
        fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    values = _clean(session, data)
    created = ElementoInfraestructura(**({'procesos': []} | values))
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, 'create', created)
        row.after = _snapshot(created)
    except IntegrityError as exc:
        raise Conflict(SPEC.conflict) from exc
    return created


def _update(session: Session, actor: Actor, record_id: int, data: Mapping[str, Any]):
    policy.require(actor, Action.UPDATE, RESOURCE)
    found = _lock(session, record_id)
    values = _clean(session, data, found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    before = _snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, 'update', found, before=before, after=_snapshot(found))
    crud._flush(session, SPEC.conflict)
    return found


def latest(session: Session, record_id: int) -> MantenimientoInfraestructura | None:
    """Most recent entry; insertion order resolves same-day ties."""
    return session.scalar(select(MantenimientoInfraestructura).where(
        MantenimientoInfraestructura.elemento_id == record_id)
        .order_by(*MAINTENANCE_SPEC.order_by).limit(1))


def _delete(session: Session, actor: Actor, record_id: int):
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _lock(session, record_id)
    if latest(session, found.id) is not None:
        raise Conflict('No se puede eliminar un elemento con mantenimientos; retíralo en su lugar.')
    audit.record(session, actor, 'delete', found, before=_snapshot(found))
    session.delete(found)
    crud._flush(session)


def _record(session: Session, actor: Actor, data: Mapping[str, Any], *, today: date):
    policy.require(actor, Action.CREATE, RESOURCE)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset(f.name for f in MAINTENANCE_SPEC.fields if f.required))
    values = crud._clean(MAINTENANCE_SPEC, session, data)
    if values['fecha'] > today:
        raise ValidationError('La fecha de mantenimiento no puede ser futura.')
    if values['fecha_proxima'] <= values['fecha']:
        raise ValidationError(NEXT_REQUIRED)
    if values['resultado'] is ResultadoMantenimiento.no_conforme and not values.get('acciones'):
        raise ValidationError(ACTIONS_REQUIRED)
    try:
        item = _lock(session, values['elemento_id'])
    except NotFound as exc:
        raise ValidationError(SPEC.not_found) from exc
    if item.retirado:
        raise ValidationError(RETIRED)
    previous = latest(session, item.id)
    if previous is not None and values['fecha'] < previous.fecha:
        raise ValidationError(BACKDATED)
    if values.get('no_conformidad_id') is not None:
        try:
            nonconformities.lock(session, values['no_conformidad_id'])
        except NotFound as exc:
            raise ValidationError('El campo «no_conformidad_id» no corresponde a ninguna no conformidad.') from exc
    # Reuse the master update so attribution, no-op and audit invariants stay identical.
    items.update(session, actor, item.id, {'fecha_proxima': values['fecha_proxima']})
    return crud.create(MAINTENANCE_SPEC, session, actor, values)


def _immutable_update(session, actor, record_id, data, *, today):
    policy.require(actor, Action.UPDATE, RESOURCE)
    crud.get(MAINTENANCE_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _immutable_delete(session, actor, record_id):
    policy.require(actor, Action.DELETE, RESOURCE)
    crud.get(MAINTENANCE_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _conditions(codigo=None, tipo=None, retirado=None, proceso_id=None):
    """Unknown selector values are ignored, as on the register's web list."""
    where = []
    if codigo:
        where.append(ElementoInfraestructura.codigo.ilike(f'%{codigo}%'))
    if tipo is not None:
        try:
            member = fields.enum_member({'tipo': tipo}, 'tipo', TipoInfraestructura)
            where.append(ElementoInfraestructura.tipo == member)
        except ValidationError:
            pass
    if isinstance(retirado, bool):
        where.append(ElementoInfraestructura.retirado.is_(retirado))
    if proceso_id is not None:
        where.append(ElementoInfraestructura.procesos.any(ProcesoOperacion.id_proceso == proceso_id)
                     if people.is_db_id(proceso_id) else false())
    return where


def _maintenance_conditions(elemento_id=None, resultado=None):
    where = []
    if elemento_id is not None:
        where.append(MantenimientoInfraestructura.elemento_id == elemento_id
                     if people.is_db_id(elemento_id) else false())
    if resultado is not None:
        try:
            member = fields.enum_member({'resultado': resultado}, 'resultado', ResultadoMantenimiento)
            where.append(MantenimientoInfraestructura.resultado == member)
        except ValidationError:
            pass
    return where


items = Register(SPEC, _conditions, _create, _update, _delete)
maintenance = Register(MAINTENANCE_SPEC, _maintenance_conditions, _record,
                       _immutable_update, _immutable_delete)
