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
A role that competence requirements cite cannot be deleted (``Conflict``);
the people holding it simply lose it.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import CompetenceRequirement, RolResponsabilidad
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, NotFound
from .policy import Action, Resource

ROL_MAX = 50  # mirrors RolResponsabilidad.rol
IN_USE = (
    "No se puede eliminar un rol que citan requisitos de competencia; "
    "elimínalos o asígnalos a otro rol antes."
)

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
list_page = partial(crud.list_page, SPEC)


def _in_use(session: Session, rol_id: int) -> bool:
    """Whether a competence requirement cites the role."""
    if not people.is_db_id(rol_id):  # no stored id can match
        return False
    return bool(session.scalar(
        select(exists().where(CompetenceRequirement.rol_id == rol_id))
    ))


def delete(session: Session, actor: Actor, rol_id: int) -> None:
    """Hard-delete a role; one that competence requirements cite raises ``Conflict``.

    The foreign key is ``ON DELETE RESTRICT``, which SQLite only enforces on
    request, hence the explicit check; a requirement citing the role after
    the check meets the foreign key at the flush, with the same message.
    """
    policy.require(actor, Action.DELETE, Resource.ROLES_RESPONSIBILITIES)
    if not people.is_db_id(rol_id):  # same answer as an id nothing stores
        raise NotFound(SPEC.not_found)
    if _in_use(session, rol_id):
        raise Conflict(IN_USE)
    try:
        crud.delete(SPEC, session, actor, rol_id)
    except Conflict as exc:
        raise Conflict(IN_USE) from exc.__cause__
