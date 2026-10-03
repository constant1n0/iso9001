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

"""Roles and responsibilities (``RolResponsabilidad``) on the generic CRUD helper.

Serves the JSON register. ``rol`` is unique: a duplicate raises ``Conflict``.
"""

from __future__ import annotations

from functools import partial

from ..models import RolResponsabilidad
from . import crud, fields
from .policy import Resource

ROL_MAX = 50  # mirrors RolResponsabilidad.rol

SPEC = crud.Spec(
    model=RolResponsabilidad,
    resource=Resource.ROLES_RESPONSIBILITIES,
    not_found="Rol y responsabilidad no encontrado.",
    fields=(
        crud.Field("rol", partial(fields.text, required=True, max_length=ROL_MAX), required=True),
        crud.Field("compromiso_calidad", fields.boolean),
        crud.Field("descripcion_politica_calidad", partial(fields.text, strip=False)),
    ),
    order_by=(RolResponsabilidad.id_rol,),
    conflict="Ya existe un rol con ese nombre.",
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)
list_page = partial(crud.list_page, SPEC)
