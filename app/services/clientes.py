"""Customer master data (8.2), with provider-style NIF and contact validation.

Services flush and audit, never commit. A customer cited by a survey or complaint
cannot be deleted; RESTRICT foreign keys also protect against concurrent citations.
"""
from __future__ import annotations

from functools import partial
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Cliente, Reclamacion, SatisfaccionCliente
from . import audit, crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .providers import _nif, _optional_email

HAS_SURVEYS = 'No se puede eliminar un cliente que tiene encuestas de satisfacción.'
HAS_COMPLAINTS = 'No se puede eliminar un cliente que tiene reclamaciones.'
DUPLICATE = 'Ya existe un cliente con ese nombre o NIF.'
STILL_REFERENCED = 'No se puede eliminar el cliente porque tiene encuestas o reclamaciones.'
UNKNOWN_CUSTOMER = 'El campo «cliente_id» no corresponde a ningún cliente.'

SPEC = crud.Spec(
    model=Cliente, resource=Resource.CUSTOMERS, not_found='Cliente no encontrado.',
    fields=(
        crud.Field('nombre', partial(fields.text, required=True, max_length=200), required=True),
        crud.Field('nif', _nif),
        crud.Field('contacto', partial(fields.text, max_length=150)),
        crud.Field('email', _optional_email),
        crud.Field('telefono', partial(fields.text, max_length=30)),
        crud.Field('notas', partial(fields.text, strip=False)),
    ),
    order_by=(Cliente.nombre, Cliente.id), conflict=DUPLICATE,
)
get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)


def check_reference(session: Session, key: str, value: Any, current: Any) -> None:
    """A newly selected customer must exist; shared by surveys and complaints."""
    if not people.is_db_id(value) or session.get(Cliente, value) is None:
        raise ValidationError(UNKNOWN_CUSTOMER)


def _conditions(nombre: str | None = None) -> list[Any]:
    return [Cliente.nombre.ilike(f'%{nombre}%')] if nombre else []


def list_(session: Session, actor: Actor, *, nombre: str | None = None) -> list[Cliente]:
    return crud.list_(SPEC, session, actor, _conditions(nombre))


def list_page(session: Session, actor: Actor, *, nombre: str | None = None,
              page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE) -> tuple[list[Cliente], int]:
    return crud.list_page(SPEC, session, actor, _conditions(nombre), page=page, per_page=per_page)


def delete(session: Session, actor: Actor, customer_id: int) -> None:
    policy.require(actor, Action.DELETE, Resource.CUSTOMERS)
    found = None
    if people.is_db_id(customer_id):
        found = session.scalar(select(Cliente).where(Cliente.id == customer_id).with_for_update()
                               .execution_options(populate_existing=True))
    if found is None:
        raise NotFound(SPEC.not_found)
    for model, message in ((SatisfaccionCliente, HAS_SURVEYS), (Reclamacion, HAS_COMPLAINTS)):
        if session.scalar(select(exists().where(model.cliente_id == customer_id))):
            raise Conflict(message)
    audit.record(session, actor, 'delete', found)
    session.delete(found)
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(STILL_REFERENCED) from exc
