# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Customer satisfaction surveys (``SatisfaccionCliente``) on the generic CRUD helper.

Validation mirrors ``SatisfaccionClienteForm``; every role may use the register.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from sqlalchemy import false
from sqlalchemy.orm import Session

from ..models import CanalContacto, SatisfaccionCliente, TipoEncuesta
from . import clientes, crud, fields, people
from .actor import Actor
from .policy import Resource

PUNTUACION_MIN, PUNTUACION_MAX = 1, 10  # mirrors the web form

SPEC = crud.Spec(
    model=SatisfaccionCliente,
    resource=Resource.CUSTOMER_SATISFACTION,
    not_found="Encuesta no encontrada.",
    fields=(
        crud.Field("cliente_id", partial(fields.integer, required=True), required=True,
                   check=clientes.check_reference),
        crud.Field("tipo_encuesta", partial(fields.enum_member, enum_cls=TipoEncuesta), required=True),
        crud.Field("canal", partial(fields.enum_member, enum_cls=CanalContacto), required=True),
        crud.Field("fecha_encuesta", fields.required_date, required=True),
        crud.Field("puntuacion", partial(fields.integer, required=True,
                                         minimum=PUNTUACION_MIN, maximum=PUNTUACION_MAX), required=True),
        crud.Field("comentarios", partial(fields.text, strip=False)),
    ),
    order_by=(SatisfaccionCliente.fecha_encuesta.desc(), SatisfaccionCliente.id.desc()),
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)


def _conditions(cliente_id: int | None, puntuacion_minima: int | None,
                tipo_encuesta: Any = None, canal: Any = None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if cliente_id is not None:
        where.append(SatisfaccionCliente.cliente_id == cliente_id
                     if people.is_db_id(cliente_id) else false())
    for key, value, enum_cls in (("tipo_encuesta", tipo_encuesta, TipoEncuesta),
                                  ("canal", canal, CanalContacto)):
        if value is not None and value != "":
            where.append(getattr(SatisfaccionCliente, key) ==
                         fields.enum_member({key: value}, key, enum_cls))
    if puntuacion_minima is not None:
        where.append(SatisfaccionCliente.puntuacion >= puntuacion_minima)
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    cliente_id: int | None = None,
    puntuacion_minima: int | None = None,
    tipo_encuesta: Any = None,
    canal: Any = None,
) -> list[SatisfaccionCliente]:
    """Surveys, newest first; filters combine with AND."""
    return crud.list_(SPEC, session, actor, _conditions(cliente_id, puntuacion_minima,
                                                       tipo_encuesta, canal))


def list_page(
    session: Session,
    actor: Actor,
    *,
    cliente_id: int | None = None,
    puntuacion_minima: int | None = None,
    tipo_encuesta: Any = None,
    canal: Any = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[SatisfaccionCliente], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(SPEC, session, actor, _conditions(cliente_id, puntuacion_minima,
                                                           tipo_encuesta, canal),
                          page=page, per_page=per_page)
