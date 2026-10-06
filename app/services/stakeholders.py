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

"""Interested parties (``ParteInteresada``) on the generic CRUD helper.

Validation mirrors ``ParteInteresadaForm``; every role may use the register.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy.orm import Session

from ..models import ParteInteresada
from . import crud, fields
from .actor import Actor
from .policy import Resource

NOMBRE_MAX = 50  # mirrors ParteInteresada.nombre and the web form

SPEC = crud.Spec(
    model=ParteInteresada,
    resource=Resource.INTERESTED_PARTIES,
    not_found="Parte interesada no encontrada.",
    fields=(
        crud.Field("nombre", partial(fields.text, required=True, max_length=NOMBRE_MAX), required=True),
        crud.Field("necesidades_expectativas", partial(fields.text, strip=False)),
        crud.Field("requisitos_identificados", partial(fields.text, strip=False)),
        crud.Field("objetivo_estrategico", partial(fields.text, strip=False)),
    ),
    order_by=(ParteInteresada.nombre, ParteInteresada.id_interesado),
    conflict="Ya existe una parte interesada con ese nombre.",
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)


def list_(session: Session, actor: Actor) -> list[ParteInteresada]:
    """All interested parties in name order."""
    return crud.list_(SPEC, session, actor)


list_page = partial(crud.list_page, SPEC)  # one page in the ``list_`` order plus the total
