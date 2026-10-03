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

"""Risks and opportunities (``RiesgoOportunidad``) on the generic CRUD helper.

Serves the JSON register. ``tipo`` is given as the enum value or name.
"""

from __future__ import annotations

from functools import partial

from ..models import RiesgoOportunidad, TipoEnum
from . import crud, fields
from .policy import Resource

SPEC = crud.Spec(
    model=RiesgoOportunidad,
    resource=Resource.RISKS_OPPORTUNITIES,
    not_found="Riesgo u oportunidad no encontrado.",
    fields=(
        crud.Field("tipo", partial(fields.enum_member, enum_cls=TipoEnum), required=True),
        crud.Field("descripcion", partial(fields.text, required=True, strip=False), required=True),
        crud.Field("objetivo_calidad", partial(fields.text, strip=False)),
        crud.Field("plan_accion", partial(fields.text, strip=False)),
    ),
    order_by=(RiesgoOportunidad.id_riesgo,),
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)
list_page = partial(crud.list_page, SPEC)
