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

"""Training records (``Capacitacion``) on the generic CRUD helper.

Validation mirrors ``CapacitacionForm``; every role may use the register.
"""

from __future__ import annotations

from datetime import date
from functools import partial

from sqlalchemy.orm import Session

from ..models import Capacitacion
from . import crud, fields
from .actor import Actor
from .policy import Resource

TEMA_MAX = 100  # mirrors Capacitacion.tema and the web form
PERSONAL_MAX = 100  # mirrors Capacitacion.personal and the web form
EVALUACION_MAX = 20  # mirrors Capacitacion.evaluacion_final and the web form

SPEC = crud.Spec(
    model=Capacitacion,
    resource=Resource.TRAINING,
    not_found="Capacitación no encontrada.",
    fields=(
        crud.Field("tema", partial(fields.text, required=True, max_length=TEMA_MAX), required=True),
        crud.Field("fecha", fields.required_date, required=True),
        crud.Field("personal", partial(fields.text, required=True, max_length=PERSONAL_MAX), required=True),
        crud.Field("duracion_horas", partial(fields.integer, minimum=0)),
        crud.Field("evaluacion_final", partial(fields.text, max_length=EVALUACION_MAX)),
    ),
    order_by=(Capacitacion.fecha.desc(), Capacitacion.id.desc()),
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)


def list_(
    session: Session,
    actor: Actor,
    *,
    tema: str | None = None,
    fecha: date | None = None,
    personal: str | None = None,
) -> list[Capacitacion]:
    """Training records, newest first; filters combine with AND."""
    where = []
    if tema:
        where.append(Capacitacion.tema.ilike(f"%{tema}%"))
    if fecha:
        where.append(Capacitacion.fecha == fecha)
    if personal:
        where.append(Capacitacion.personal.ilike(f"%{personal}%"))
    return crud.list_(SPEC, session, actor, where)
