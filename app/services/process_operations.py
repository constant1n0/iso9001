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

``proceso`` is unique: a duplicate raises ``Conflict``. A process that quality
objectives cite cannot be deleted (``Conflict`` with ``CITED_BY_OBJECTIVES``):
``delete`` checks it, and the ``ON DELETE RESTRICT`` foreign key, which SQLite
only enforces on request, refuses a citation added after the check. Any other
conflict keeps its own message.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ObjetivoCalidad, ProcesoOperacion
from . import crud, fields, policy
from .actor import Actor
from .errors import Conflict
from .policy import Action, Resource

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

CITED_BY_OBJECTIVES = (
    "No se puede eliminar un proceso al que se refieren objetivos de calidad; "
    "quítalo antes de esos objetivos."
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
list_page = partial(crud.list_page, SPEC)


def delete(session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete a process that no quality objective cites, keeping an audit snapshot.

    A cited process raises ``Conflict`` (``CITED_BY_OBJECTIVES``); so does one
    cited after the check, when the flush breaks the objectives' foreign key
    and the database names it (PostgreSQL). A failed flush leaves the session
    unusable until rolled back, so the citation cannot be checked again: any
    other ``Conflict``, or one SQLite does not name, is raised as it came.
    """
    policy.require(actor, Action.DELETE, SPEC.resource)
    found = crud.get(SPEC, session, actor, record_id)
    if _cited_by_objectives(session, found.id_proceso):
        raise Conflict(CITED_BY_OBJECTIVES)
    try:
        crud.delete(SPEC, session, actor, found.id_proceso)
    except Conflict as exc:
        violated = _violated_constraint(exc.__cause__)
        if violated is not None and violated == OBJECTIVES_FK:
            raise Conflict(CITED_BY_OBJECTIVES) from exc.__cause__
        raise


def _cited_by_objectives(session: Session, process_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(ObjetivoCalidad.proceso_id == process_id))))


def _objectives_foreign_key() -> str:
    """Name of the objectives' foreign key to processes, as the model declares it."""
    (name,) = {fk.constraint.name for fk in ObjetivoCalidad.__table__.foreign_keys
               if fk.column.table is ProcesoOperacion.__table__}
    return name


OBJECTIVES_FK = _objectives_foreign_key()


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
