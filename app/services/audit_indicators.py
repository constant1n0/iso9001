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

"""Audit indicators (``AuditoriaIndicador``) on the generic CRUD helper (JSON register).

``fecha_auditoria`` accepts a ``datetime`` or an ISO 8601 string (an explicit
``null`` is a validation error); when absent the column default sets it on create.
"""

from __future__ import annotations

from functools import partial

from ..models import AuditoriaIndicador
from . import crud, fields
from .policy import Resource

AREA_MAX = 50  # mirrors AuditoriaIndicador.area_auditoria

SPEC = crud.Spec(
    model=AuditoriaIndicador,
    resource=Resource.AUDIT_INDICATORS,
    not_found="Auditoría e indicador no encontrado.",
    fields=(
        crud.Field("area_auditoria", partial(fields.text, required=True, max_length=AREA_MAX), required=True),
        crud.Field("fecha_auditoria", partial(fields.optional_datetime, nullable=False)),
        crud.Field("resultado", partial(fields.text, strip=False)),
        crud.Field("accion_correctiva", partial(fields.text, strip=False)),
        crud.Field("indicador_desempeno", partial(fields.text, strip=False)),
    ),
    order_by=(AuditoriaIndicador.id_auditoria,),
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)
list_page = partial(crud.list_page, SPEC)
