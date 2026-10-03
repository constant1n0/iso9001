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

"""Process operations (``ProcesoOperacion``) on the generic CRUD helper (JSON register).

``proceso`` is unique: a duplicate raises ``Conflict``.
"""

from __future__ import annotations

from functools import partial

from ..models import ProcesoOperacion
from . import crud, fields
from .policy import Resource

PROCESO_MAX = 100  # mirrors ProcesoOperacion.proceso

SPEC = crud.Spec(
    model=ProcesoOperacion,
    resource=Resource.PROCESS_OPERATIONS,
    not_found="Proceso de operación no encontrado.",
    fields=(
        crud.Field("proceso", partial(fields.text, required=True, max_length=PROCESO_MAX), required=True),
        crud.Field("criterio_calidad", partial(fields.text, strip=False)),
        crud.Field("control_proveedor", fields.boolean),
        crud.Field("no_conformidad", partial(fields.text, strip=False)),
    ),
    order_by=(ProcesoOperacion.id_proceso,),
    conflict="Ya existe un proceso con ese nombre.",
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)
list_page = partial(crud.list_page, SPEC)
