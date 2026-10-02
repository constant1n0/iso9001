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

"""Nonconformity service: the reference pattern for every other module.

Rules this module follows, and the services that copy it should too:

- No Flask. Every function takes an explicit ``Session`` and ``Actor``.
- The adapter owns the transaction: services flush, never commit or roll back.
- ``policy.require`` first, then load, validate, mutate, stamp and audit.
- Writes accept a mapping restricted to a field whitelist; unknown keys are a
  ``ValidationError``, so a caller can never set ``id`` or attribution columns.
- ``IntegrityError`` becomes ``Conflict`` (the session then needs a rollback,
  which the adapter performs).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import NoConformidad
from . import policy
from .actor import Actor
from .errors import NotFound
from .policy import Action, Resource

ESTADO_ABIERTA = "Abierta"
ESTADO_EN_PROCESO = "En proceso"
ESTADO_CERRADA = "Cerrada"
# Single source for forms, dashboard and services. Rows created before this
# set existed may hold other free-text states; those stay readable and
# keep their value until a user picks one of these (decision D8).
ESTADOS_NO_CONFORMIDAD = (ESTADO_ABIERTA, ESTADO_EN_PROCESO, ESTADO_CERRADA)


def get(session: Session, actor: Actor, nc_id: int) -> NoConformidad:
    """Return one nonconformity or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    return _load(session, nc_id)


def _load(session: Session, nc_id: int) -> NoConformidad:
    nc = session.get(NoConformidad, nc_id)
    if nc is None:
        raise NotFound("No conformidad no encontrada.")
    return nc


def list_(
    session: Session,
    actor: Actor,
    *,
    descripcion: str | None = None,
    estado: str | None = None,
    fecha_detectada: date | None = None,
) -> list[NoConformidad]:
    """List nonconformities, newest first; filters combine with AND."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    query = select(NoConformidad)
    if descripcion:
        query = query.where(NoConformidad.descripcion.ilike(f"%{descripcion}%"))
    if estado:
        query = query.where(NoConformidad.estado == estado)
    if fecha_detectada:
        query = query.where(NoConformidad.fecha_detectada == fecha_detectada)
    query = query.order_by(NoConformidad.fecha_detectada.desc(), NoConformidad.id.desc())
    return list(session.scalars(query))


def available_states(session: Session, actor: Actor) -> list[str]:
    """Fixed states first, then any legacy free-text values still stored."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    stored = set(session.scalars(select(NoConformidad.estado).distinct()))
    return [*ESTADOS_NO_CONFORMIDAD, *sorted(stored - set(ESTADOS_NO_CONFORMIDAD))]
