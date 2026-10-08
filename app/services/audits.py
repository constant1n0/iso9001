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
``AuditoriaForm``. Services flush and never commit. ``auditor_id`` cites the
auditor (``personas``, see ``people.check_reference``); ``auditor`` keeps the
legacy free-text name. ``auditor`` is required unless a person is cited: a
write that leaves it blank takes the person's name (``people.fill_name``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Auditoria, EstadoAuditoriaEnum
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound
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
    page, per_page = crud.page_bounds(page, per_page, default_per_page=10)
    total = session.scalar(select(func.count()).select_from(Auditoria).where(*conditions))
    query = (
        select(Auditoria)
        .where(*conditions)
        .order_by(Auditoria.id)
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return list(session.scalars(query)), total


AREA_MAX = 50  # mirrors Auditoria.area_auditada and the web form
AUDITOR_MAX = 50  # mirrors Auditoria.auditor and the web form
WRITABLE_FIELDS = frozenset({
    "area_auditada", "fecha", "auditor", "auditor_id", "resultado", "accion_correctiva",
    "estado",
})
REQUIRED_ON_CREATE = frozenset({"area_auditada", "fecha", "auditor", "resultado"})


def _clean(
    session: Session, data: Mapping[str, Any], current: Auditoria | None = None
) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values.

    ``current`` is the audit being updated (``None`` when creating).
    """
    fields.reject_unknown(data, WRITABLE_FIELDS)
    clean: dict[str, Any] = {}
    if "area_auditada" in data:
        clean["area_auditada"] = fields.text(
            data, "area_auditada", required=True, max_length=AREA_MAX
        )
    if "fecha" in data:
        clean["fecha"] = fields.required_date(data, "fecha")
    if "auditor" in data:
        clean["auditor"] = fields.text(data, "auditor", required=True, max_length=AUDITOR_MAX)
    if "resultado" in data:
        clean["resultado"] = fields.text(data, "resultado", required=True)
    if "accion_correctiva" in data:
        clean["accion_correctiva"] = fields.text(data, "accion_correctiva", strip=False)
    if "estado" in data:
        clean["estado"] = fields.enum_member(data, "estado", EstadoAuditoriaEnum)
    if "auditor_id" in data:  # last: it queries the database
        clean["auditor_id"] = people.reference(
            session, data, "auditor_id", current.auditor_id if current is not None else None
        )
    return clean


def _fill_auditor(
    session: Session, data: Mapping[str, Any], current: Auditoria | None = None
) -> Mapping[str, Any]:
    """``data`` with a blank ``auditor`` taken from the linked person."""
    return people.fill_name(session, data, current, link="auditor_id", text="auditor",
                            max_length=AUDITOR_MAX)


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> Auditoria:
    """Create an audit; area, date, result and the auditor (name or person) are required."""
    policy.require(actor, Action.CREATE, Resource.AUDITS)
    data = _fill_auditor(session, data)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values = {"estado": EstadoAuditoriaEnum.PENDIENTE} | _clean(session, data)
    created = Auditoria(**values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict() from exc
    return created


def update(
    session: Session, actor: Actor, audit_id: int, data: Mapping[str, Any]
) -> Auditoria:
    """Apply the given fields; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.AUDITS)
    found = _load(session, audit_id)
    values = _clean(session, _fill_auditor(session, data, found), current=found)
    before = audit.snapshot(found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    _flush(session)
    return found


def delete(session: Session, actor: Actor, audit_id: int) -> None:
    """Hard-delete an audit, keeping its full snapshot in the audit log."""
    policy.require(actor, Action.DELETE, Resource.AUDITS)
    found = _load(session, audit_id)
    audit.record(session, actor, "delete", found)
    session.delete(found)
    _flush(session)
