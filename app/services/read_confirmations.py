"""Evidence of reading documents and quality objectives (ISO 9001 5.2, 7.3).

All roles read and write; administrators delete outside MCP. Writes use the
adapter's local date, flush without committing, and preserve attribution and
audit history through crud.Spec. Partial updates validate the resulting object.
Cited people and revisions are protected by RESTRICT foreign keys; people also
check their REFERENCES list before deletion on databases without FK enforcement.
"""
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime
from functools import partial

from sqlalchemy import false

from ..models import ConfirmacionLectura, DocumentRevision, TipoConfirmacion
from . import crud, fields, people, policy
from .errors import ValidationError
from .policy import Action, Resource

REVISION_REQUIRED = 'Una confirmación de documento requiere una revisión del documento.'
OBJECTIVES_ONLY = 'Solo una confirmación de documento puede indicar una revisión del documento.'
UNKNOWN_REVISION = 'El campo «document_revision_id» no corresponde a ninguna revisión.'
FUTURE_DATE = 'La fecha de confirmación no puede ser futura.'


def _check_revision(session, key, value, current=None):
    if value is not None and (not people.is_db_id(value) or session.get(DocumentRevision, value) is None):
        raise ValidationError(UNKNOWN_REVISION)


SPEC = crud.Spec(
    model=ConfirmacionLectura,
    resource=Resource.READ_CONFIRMATIONS,
    not_found='Confirmación de lectura no encontrada.',
    fields=(
        crud.Field('persona_id', partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field('fecha_confirmacion', fields.required_date, required=True),
        crud.Field('tipo', partial(fields.enum_member, enum_cls=TipoConfirmacion), required=True),
        crud.Field('document_revision_id', fields.integer, check=_check_revision),
        crud.Field('notas', partial(fields.text, strip=False)),
    ),
    order_by=(ConfirmacionLectura.fecha_confirmacion.desc(), ConfirmacionLectura.id.desc()),
)

get = partial(crud.get, SPEC)
delete = partial(crud.delete, SPEC)


def _prepare(session, data, found, *, today):
    if not isinstance(data, Mapping):
        return data  # crud reports the malformed payload
    fields.reject_unknown(data, SPEC.writable)
    values = {field.name: field.clean(data, field.name)
              for field in SPEC.fields if field.name in data}
    final = lambda key: values.get(key, getattr(found, key, None))
    _check_revision(session, 'document_revision_id', final('document_revision_id'))
    tipo, revision = final('tipo'), final('document_revision_id')
    if tipo is TipoConfirmacion.documento and revision is None:
        raise ValidationError(REVISION_REQUIRED)
    if tipo is TipoConfirmacion.objetivos and revision is not None:
        raise ValidationError(OBJECTIVES_ONLY)
    confirmed = final('fecha_confirmacion')
    if confirmed is not None and confirmed > today:
        raise ValidationError(FUTURE_DATE)
    return data


def _dated_spec(today):
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError('today must be a date')
    return replace(SPEC, prepare=partial(_prepare, today=today))


def create(session, actor, data, *, today):
    policy.require(actor, Action.CREATE, SPEC.resource)
    return crud.create(_dated_spec(today), session, actor, data)


def update(session, actor, record_id, data, *, today):
    policy.require(actor, Action.UPDATE, SPEC.resource)
    return crud.update(_dated_spec(today), session, actor, record_id, data)


def _conditions(persona_id=None, tipo=None, document_revision_id=None):
    where = []
    for key, value in (('persona_id', persona_id), ('document_revision_id', document_revision_id)):
        if value is not None:
            where.append(getattr(ConfirmacionLectura, key) == value if people.is_db_id(value) else false())
    if tipo is not None:
        where.append(ConfirmacionLectura.tipo == fields.enum_member({'tipo': tipo}, 'tipo', TipoConfirmacion))
    return where


def list_(session, actor, *, persona_id=None, tipo=None, document_revision_id=None):
    policy.require(actor, Action.READ, SPEC.resource)
    return crud.list_(SPEC, session, actor, _conditions(persona_id, tipo, document_revision_id))


def list_page(session, actor, *, persona_id=None, tipo=None, document_revision_id=None,
              page=1, per_page=crud.DEFAULT_PER_PAGE):
    policy.require(actor, Action.READ, SPEC.resource)
    return crud.list_page(SPEC, session, actor, _conditions(persona_id, tipo, document_revision_id),
                          page=page, per_page=per_page)
