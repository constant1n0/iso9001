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
An interested party that risks or opportunities cite cannot be deleted
(``Conflict`` with ``CITED_BY_RISKS``): ``delete`` checks it, and the
``ON DELETE RESTRICT`` foreign keys, which SQLite only enforces on request,
refuse a citation added after the check. Any other conflict keeps its own
message.
"""

from __future__ import annotations

from functools import partial

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Oportunidad, ParteInteresada, Riesgo
from . import crud, fields, policy
from .actor import Actor
from .errors import Conflict
from .policy import Action, Resource

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

CITED_BY_RISKS = (
    "No se puede eliminar una parte interesada a la que se refieren riesgos u "
    "oportunidades; quítala antes de esos registros."
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)


def delete(session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete an interested party no risk or opportunity cites, keeping an audit snapshot.

    A cited party raises ``Conflict`` (``CITED_BY_RISKS``); so does one cited
    after the check, when the flush breaks one of those foreign keys and the
    database names it (PostgreSQL). Any other ``Conflict``, or one SQLite
    does not name, is raised as it came.
    """
    policy.require(actor, Action.DELETE, SPEC.resource)
    found = crud.get(SPEC, session, actor, record_id)
    if _cited_by_risks(session, found.id_interesado):
        raise Conflict(CITED_BY_RISKS)
    try:
        crud.delete(SPEC, session, actor, found.id_interesado)
    except Conflict as exc:
        if _violated_constraint(exc.__cause__) in RISKS_FKS:
            raise Conflict(CITED_BY_RISKS) from exc.__cause__
        raise


def list_(session: Session, actor: Actor) -> list[ParteInteresada]:
    """All interested parties in name order."""
    return crud.list_(SPEC, session, actor)


list_page = partial(crud.list_page, SPEC)  # one page in the ``list_`` order plus the total


def _cited_by_risks(session: Session, party_id: int) -> bool:
    return any(session.scalar(select(exists().where(column == party_id)))
               for column in (Riesgo.parte_interesada_id, Oportunidad.parte_interesada_id))


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to interested parties, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is ParteInteresada.__table__}
    return name


RISKS_FKS = frozenset({_foreign_key(Riesgo), _foreign_key(Oportunidad)})


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
