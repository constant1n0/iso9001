"""Process environment conditions and append-only checks (ISO 9001 7.1.4).

The condition lock serializes edits, retirement, deletion and history appends,
refreshing preloaded identities after contention. Dates are explicit, never
calculated. Adapters commit or roll back the entire audited transaction.
"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import partial

from sqlalchemy import false, select

from ..models import (CondicionAmbiente, ComprobacionAmbiente, FactorAmbiente,
                      Person, ProcesoOperacion, ResultadoMantenimiento)
from . import crud, fields, people, policy
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register, _process_exists

RESOURCE = Resource.PROCESS_ENVIRONMENT
IMMUTABLE = 'Las comprobaciones no se modifican ni se eliminan; registra una nueva.'
BACKDATED = 'La comprobación no puede ser anterior a la última registrada.'
RETIRED = 'No se pueden registrar comprobaciones de una condición retirada.'
NEXT_REQUIRED = 'Indica una nueva próxima fecha posterior a la comprobación.'
ACTIONS_REQUIRED = 'Un resultado no conforme requiere indicar las acciones realizadas.'


def _retired(data, key):
    value = fields.boolean(data, key)
    if value is None:
        raise ValidationError('El campo «retirado» debe ser verdadero o falso.')
    return value


SPEC = crud.Spec(
    model=CondicionAmbiente, resource=RESOURCE,
    not_found='Condición de ambiente no encontrada.',
    fields=(
        crud.Field('proceso_id', partial(fields.integer, required=True), required=True,
                   check=_process_exists),
        crud.Field('factor', partial(fields.enum_member, enum_cls=FactorAmbiente), required=True),
        crud.Field('descripcion', partial(fields.text, required=True), required=True),
        crud.Field('criterio', partial(fields.text, required=True), required=True),
        crud.Field('frecuencia', partial(fields.text, required=True), required=True),
        crud.Field('responsable_id', fields.integer, check=people.check_reference),
        crud.Field('fecha_proxima', fields.required_date, required=True),
        crud.Field('retirado', _retired),
    ),
    order_by=(CondicionAmbiente.id,),
)
CHECK_SPEC = crud.Spec(
    model=ComprobacionAmbiente, resource=RESOURCE,
    not_found='Comprobación de ambiente no encontrada.',
    fields=(
        crud.Field('condicion_id', partial(fields.integer, required=True), required=True),
        crud.Field('fecha', fields.required_date, required=True),
        crud.Field('resultado', partial(fields.enum_member, enum_cls=ResultadoMantenimiento), required=True),
        crud.Field('realizado_por', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('evidencia', partial(fields.text, required=True), required=True),
        crud.Field('acciones', fields.text),
        crud.Field('fecha_proxima', fields.required_date, required=True),
    ),
    order_by=(ComprobacionAmbiente.fecha.desc(), ComprobacionAmbiente.id.desc()),
)


def is_overdue(item, *, today):
    """The date is valid through that day; retired conditions never become overdue."""
    return not item.retirado and item.fecha_proxima < today


def _lock(session, record_id):
    found = None
    if people.is_db_id(record_id):
        found = session.scalar(select(CondicionAmbiente).where(
            CondicionAmbiente.id == record_id).with_for_update()
            .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(SPEC.not_found)
    return found


def _clean(session, data, found=None):
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, SPEC.writable)
    # Refresh and protect selected references before the shared validators run.
    for key, model, column in (('responsable_id', Person, Person.id),
                               ('proceso_id', ProcesoOperacion, ProcesoOperacion.id_proceso)):
        value = data.get(key)
        if people.is_db_id(value):
            session.scalar(select(model).where(column == value).with_for_update(read=True)
                           .execution_options(populate_existing=True))
    return crud._clean(SPEC, session, data, found)


def _create(session, actor, data):
    policy.require(actor, Action.CREATE, RESOURCE)
    if isinstance(data, Mapping):
        fields.require_keys(data, frozenset(f.name for f in SPEC.fields if f.required))
    return crud.create(SPEC, session, actor, _clean(session, data))


def _update(session, actor, record_id, data):
    policy.require(actor, Action.UPDATE, RESOURCE)
    found = _lock(session, record_id)
    return crud.update(SPEC, session, actor, found.id, _clean(session, data, found))


def latest(session, record_id):
    """Insertion order resolves same-day ties."""
    return session.scalar(select(ComprobacionAmbiente).where(
        ComprobacionAmbiente.condicion_id == record_id)
        .order_by(*CHECK_SPEC.order_by).limit(1))


def _delete(session, actor, record_id):
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _lock(session, record_id)
    if latest(session, found.id) is not None:
        raise Conflict('No se puede eliminar una condición con comprobaciones; retírala en su lugar.')
    crud.delete(SPEC, session, actor, found.id)


def _record(session, actor, data, *, today):
    policy.require(actor, Action.CREATE, RESOURCE)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.require_keys(data, frozenset(f.name for f in CHECK_SPEC.fields if f.required))
    values = crud._clean(CHECK_SPEC, session, data)
    if values['fecha'] > today:
        raise ValidationError('La fecha de comprobación no puede ser futura.')
    if values['fecha_proxima'] <= values['fecha']:
        raise ValidationError(NEXT_REQUIRED)
    if values['resultado'] is ResultadoMantenimiento.no_conforme and not values.get('acciones'):
        raise ValidationError(ACTIONS_REQUIRED)
    try:
        item = _lock(session, values['condicion_id'])
    except NotFound as exc:
        raise ValidationError(SPEC.not_found) from exc
    if item.retirado:
        raise ValidationError(RETIRED)
    previous = latest(session, item.id)
    if previous is not None and values['fecha'] < previous.fecha:
        raise ValidationError(BACKDATED)
    # Reuse the master update: identical attribution, audit and no-op handling.
    items.update(session, actor, item.id, {'fecha_proxima': values['fecha_proxima']})
    return crud.create(CHECK_SPEC, session, actor, values)


def _immutable_update(session, actor, record_id, data, *, today):
    policy.require(actor, Action.UPDATE, RESOURCE)
    crud.get(CHECK_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _immutable_delete(session, actor, record_id):
    policy.require(actor, Action.DELETE, RESOURCE)
    crud.get(CHECK_SPEC, session, actor, record_id)
    raise ValidationError(IMMUTABLE)


def _conditions(proceso_id=None, factor=None, retirado=None, vencida=None, *, today=None):
    """Unknown selector values are ignored, as on the existing web registers."""
    where = []
    if proceso_id is not None:
        where.append(CondicionAmbiente.proceso_id == proceso_id
                     if people.is_db_id(proceso_id) else false())
    if factor is not None:
        try:
            member = fields.enum_member({'factor': factor}, 'factor', FactorAmbiente)
            where.append(CondicionAmbiente.factor == member)
        except ValidationError:
            pass
    if isinstance(retirado, bool):
        where.append(CondicionAmbiente.retirado.is_(retirado))
    if isinstance(vencida, bool):
        if today is None:
            raise ValueError('today is required for vencida')
        late = (CondicionAmbiente.retirado.is_(False) & (CondicionAmbiente.fecha_proxima < today))
        where.append(late if vencida else ~late)
    return where


def _check_conditions(condicion_id=None, resultado=None):
    where = []
    if condicion_id is not None:
        where.append(ComprobacionAmbiente.condicion_id == condicion_id
                     if people.is_db_id(condicion_id) else false())
    if resultado is not None:
        try:
            member = fields.enum_member({'resultado': resultado}, 'resultado', ResultadoMantenimiento)
            where.append(ComprobacionAmbiente.resultado == member)
        except ValidationError:
            pass
    return where


items = Register(SPEC, _conditions, _create, _update, _delete)
checks = Register(CHECK_SPEC, _check_conditions, _record, _immutable_update, _immutable_delete)
