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

"""Improvements (``Mejora``) on the generic CRUD helper, for the HTML register.

Validation mirrors ``MejoraForm``. ``fecha_implementacion`` is not writable: the
column default sets it on create. The HTML routes and the JSON API both use it.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy.orm import Session

from ..models import Mejora
from . import crud, fields
from .actor import Actor
from .policy import Resource

SPEC = crud.Spec(
    model=Mejora,
    resource=Resource.IMPROVEMENTS,
    not_found="Mejora no encontrada.",
    fields=(
        crud.Field("no_conformidad", partial(fields.text, required=True, strip=False), required=True),
        crud.Field("accion_correctiva", partial(fields.text, strip=False)),
        crud.Field("accion_preventiva", partial(fields.text, strip=False)),
    ),
    order_by=(Mejora.id_mejora,),
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)


def list_page(
    session: Session, actor: Actor, *, page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE
) -> tuple[list[Mejora], int]:
    """One page of improvements in id order plus the total."""
    return crud.list_page(SPEC, session, actor, page=page, per_page=per_page)
