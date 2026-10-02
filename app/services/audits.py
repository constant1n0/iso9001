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

"""Audit (``Auditoria``) service, following ``nonconformities``.

Authorization comes from ``policy`` (resource ``AUDITS``); validation mirrors
``AuditoriaForm``. Services flush and never commit.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Auditoria, EstadoAuditoriaEnum
from . import policy
from .actor import Actor
from .errors import NotFound
from .policy import Action, Resource


def get(session: Session, actor: Actor, audit_id: int) -> Auditoria:
    """Return one audit or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.AUDITS)
    return _load(session, audit_id)


def _load(session: Session, audit_id: int) -> Auditoria:
    found = session.get(Auditoria, audit_id)
    if found is None:
        raise NotFound("Auditoría no encontrada.")
    return found


def list_page(
    session: Session,
    actor: Actor,
    *,
    area: str | None = None,
    auditor: str | None = None,
    estado: EstadoAuditoriaEnum | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[Auditoria], int]:
    """One page of audits in id order plus the total matching the filters."""
    policy.require(actor, Action.READ, Resource.AUDITS)
    conditions = []
    if area:
        conditions.append(Auditoria.area_auditada.ilike(f"%{area}%"))
    if auditor:
        conditions.append(Auditoria.auditor.ilike(f"%{auditor}%"))
    if estado:
        conditions.append(Auditoria.estado == estado)
    if fecha_inicio:
        conditions.append(Auditoria.fecha >= fecha_inicio)
    if fecha_fin:
        conditions.append(Auditoria.fecha <= fecha_fin)
    total = session.scalar(select(func.count()).select_from(Auditoria).where(*conditions))
    query = (
        select(Auditoria)
        .where(*conditions)
        .order_by(Auditoria.id)
        .limit(per_page)
        .offset((max(page, 1) - 1) * per_page)
    )
    return list(session.scalars(query)), total
