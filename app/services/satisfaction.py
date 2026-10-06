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

from sqlalchemy.orm import Session

from ..models import SatisfaccionCliente
from . import crud, fields
from .actor import Actor
from .policy import Resource

CLIENTE_MAX = 100  # mirrors SatisfaccionCliente.cliente and the web form
PUNTUACION_MIN, PUNTUACION_MAX = 1, 10  # mirrors the web form

SPEC = crud.Spec(
    model=SatisfaccionCliente,
    resource=Resource.CUSTOMER_SATISFACTION,
    not_found="Encuesta no encontrada.",
    fields=(
        crud.Field("cliente", partial(fields.text, required=True, max_length=CLIENTE_MAX), required=True),
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


def _conditions(cliente: str | None, puntuacion_minima: int | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if cliente:
        where.append(SatisfaccionCliente.cliente.ilike(f"%{cliente}%"))
    if puntuacion_minima is not None:
        where.append(SatisfaccionCliente.puntuacion >= puntuacion_minima)
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    cliente: str | None = None,
    puntuacion_minima: int | None = None,
) -> list[SatisfaccionCliente]:
    """Surveys, newest first; filters combine with AND."""
    return crud.list_(SPEC, session, actor, _conditions(cliente, puntuacion_minima))


def list_page(
    session: Session,
    actor: Actor,
    *,
    cliente: str | None = None,
    puntuacion_minima: int | None = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[SatisfaccionCliente], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(SPEC, session, actor, _conditions(cliente, puntuacion_minima),
                          page=page, per_page=per_page)
