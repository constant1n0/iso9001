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

"""Internal audit findings (``HallazgoAuditoria``, ISO 9001 clause 9.2.2).

Decisions AP4, AP5 and AP7 of ``audit-programme``. Findings share the
``AUDITS`` policy resource: administrators and auditors read, create and
update, administrators delete, and the ``mcp`` channel never deletes.

- A finding belongs to one audit for good and has a type (``TipoHallazgo``),
  a description, an optional ISO 9001 clause (dotted numbers whose first one
  is 4 to 10, such as ``8.5.1``) and evidence.
- Findings are added or edited only while their audit is not ``CANCELADA``;
  a ``COMPLETADA`` audit still takes them (the report may be finished after
  closing the audit). The audit row is read with a shared lock (``FOR SHARE``)
  so a concurrent state change waits for the write.
- A nonconformity finding (``nc_mayor``, ``nc_menor``) creates its
  nonconformity in the same transaction through ``nonconformities.create``
  (origin ``auditoria``, severity ``mayor``/``menor``, the audit's area, date
  and description, the audited-area owner as responsible when that person is
  active, otherwise no responsible) and keeps the link (``no_conformidad_id``,
  never written by callers). A finding that becomes a nonconformity creates it
  then, unless it still holds the link from an earlier type, which it reuses.
- Downgrading such a finding keeps the nonconformity and the link; the type
  change shows on the finding's audit row. While the finding is a
  nonconformity its severity follows the type, but only while the linked
  nonconformity is still ``abierta``; otherwise the change is refused. Other
  edits never touch the nonconformity, which is a record of its own.
- Deleting a finding (administrators) never deletes its nonconformity, and
  ``nonconformities.delete`` clears the link of its finding with an audit row.
- An audit with findings cannot be deleted (``audits.delete`` raises
  ``Conflict`` asking to delete its findings first; the foreign key is
  ``ON DELETE RESTRICT``).

Like every service this module flushes and never commits, writes an audit row
next to each change, and an update that changes nothing writes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Auditoria, EstadoAuditoriaEnum, EstadoNoConformidad, GravedadNoConformidad,
    HallazgoAuditoria, NoConformidad, OrigenNoConformidad, Person, TipoHallazgo,
)
from . import audit, crud, fields, nonconformities, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

WRITABLE_FIELDS = frozenset({"auditoria_id", "tipo", "descripcion", "clausula", "evidencia"})
REQUIRED_ON_CREATE = frozenset({"auditoria_id", "tipo", "descripcion"})
# The nonconformity types and the severity of the nonconformity each creates.
NC_SEVERITY = {
    TipoHallazgo.nc_mayor: GravedadNoConformidad.mayor,
    TipoHallazgo.nc_menor: GravedadNoConformidad.menor,
}
CLAUSULA_MAX = 20  # mirrors HallazgoAuditoria.clausula
CLAUSE = re.compile(r"(?:[4-9]|10)(?:\.[1-9][0-9]?){0,3}")

NOT_FOUND = "Hallazgo de auditoría no encontrado."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
UNKNOWN_AUDIT = "El campo «auditoria_id» no corresponde a ninguna auditoría."
AUDIT_FIXED = "Un hallazgo no puede cambiar de auditoría."
AUDIT_CANCELLED = "No se pueden añadir ni modificar hallazgos de una auditoría cancelada."
BAD_CLAUSE = (
    "La cláusula debe ser un apartado de la norma del 4 al 10 en números separados por "
    "puntos, como «8.5.1»."
)
BAD_TYPE = "El tipo de hallazgo no es válido."
CONFLICT = (
    "No se ha podido guardar el hallazgo porque sus datos han cambiado; vuelve a intentarlo."
)

_READS = crud.Spec(
    model=HallazgoAuditoria,
    resource=Resource.AUDITS,
    not_found=NOT_FOUND,
    fields=(),  # writes are bespoke (see ``WRITABLE_FIELDS``)
    # Newest audits first; each audit's findings in the order they were recorded.
    order_by=(HallazgoAuditoria.auditoria_id.desc(), HallazgoAuditoria.id),
)

get = partial(crud.get, _READS)


def _conditions(auditoria_id: int | None, tipo: TipoHallazgo | str | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if auditoria_id is not None:
        where.append(HallazgoAuditoria.auditoria_id == auditoria_id
                     if people.is_db_id(auditoria_id) else false())
    if tipo:
        try:
            member = fields.enum_member({"tipo": tipo}, "tipo", TipoHallazgo)
        except ValidationError:
            raise ValidationError(BAD_TYPE) from None
        where.append(HallazgoAuditoria.tipo == member)
    return where


def list_(
    session: Session, actor: Actor, *, auditoria_id: int | None = None,
    tipo: TipoHallazgo | str | None = None,
) -> list[HallazgoAuditoria]:
    """Findings, newest audit first; ``tipo`` takes the member or its name."""
    return crud.list_(_READS, session, actor, _conditions(auditoria_id, tipo))


def list_page(
    session: Session, actor: Actor, *, auditoria_id: int | None = None,
    tipo: TipoHallazgo | str | None = None, page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[HallazgoAuditoria], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(_READS, session, actor, _conditions(auditoria_id, tipo),
                          page=page, per_page=per_page)


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> HallazgoAuditoria:
    """Record a finding; a nonconformity finding creates its nonconformity too.

    Raises:
        PermissionDenied: The actor may not write audits.
        ValidationError: Invalid data, an unknown audit or a cancelled one.
        Conflict: The database refused the write.
    """
    policy.require(actor, Action.CREATE, Resource.AUDITS)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values = _clean(data)
    parent = _writable_audit(session, values["auditoria_id"])
    if values["tipo"] in NC_SEVERITY:
        values["no_conformidad_id"] = _nonconformity(
            session, actor, parent, values["descripcion"], values["tipo"]).id
    created = HallazgoAuditoria(**values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(CONFLICT) from exc
    return created


def update(
    session: Session, actor: Actor, finding_id: int, data: Mapping[str, Any]
) -> HallazgoAuditoria:
    """Apply the given fields; a type change follows the rules in the module docstring.

    Raises:
        PermissionDenied: The actor may not write audits.
        NotFound: Unknown finding.
        ValidationError: Invalid data, another audit, a cancelled audit, or a
            severity change while the linked nonconformity is no longer open.
        Conflict: The database refused the write.
    """
    policy.require(actor, Action.UPDATE, Resource.AUDITS)
    found = _lock(session, finding_id)
    values = _clean(data)
    if values.get("auditoria_id", found.auditoria_id) != found.auditoria_id:
        raise ValidationError(AUDIT_FIXED)
    parent = _writable_audit(session, found.auditoria_id)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if not changes:
        return found
    before = audit.snapshot(found)
    if "tipo" in changes and changes["tipo"] in NC_SEVERITY:
        if found.no_conformidad_id is not None:
            _follow_severity(session, actor, found.no_conformidad_id, changes["tipo"])
        elif found.tipo not in NC_SEVERITY:  # becomes a nonconformity now
            changes["no_conformidad_id"] = _nonconformity(
                session, actor, parent, changes.get("descripcion", found.descripcion),
                changes["tipo"]).id
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    _flush(session)
    return found


def delete(session: Session, actor: Actor, finding_id: int) -> None:
    """Hard-delete a finding with its full snapshot in the audit log; its nonconformity stays."""
    policy.require(actor, Action.DELETE, Resource.AUDITS)
    found = _lock(session, finding_id)
    audit.record(session, actor, "delete", found)
    session.delete(found)
    _flush(session)


def _lock(session: Session, finding_id: int) -> HallazgoAuditoria:
    """Load a finding afresh with its row locked (``FOR UPDATE``), or raise ``NotFound``."""
    found = None
    if people.is_db_id(finding_id):
        found = session.scalars(
            select(HallazgoAuditoria).where(HallazgoAuditoria.id == finding_id)
            .with_for_update().execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _writable_audit(session: Session, audit_id: int) -> Auditoria:
    """The audit, read with a shared lock, that may take findings (not ``CANCELADA``)."""
    found = None
    if people.is_db_id(audit_id):
        found = session.scalars(
            select(Auditoria).where(Auditoria.id == audit_id)
            .with_for_update(read=True).execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise ValidationError(UNKNOWN_AUDIT)
    if found.estado is EstadoAuditoriaEnum.CANCELADA:
        raise ValidationError(AUDIT_CANCELLED)
    return found


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)
    values: dict[str, Any] = {}
    if "auditoria_id" in data:
        values["auditoria_id"] = fields.integer(data, "auditoria_id", required=True)
    if "tipo" in data:
        values["tipo"] = fields.enum_member(data, "tipo", TipoHallazgo)
    if "descripcion" in data:
        values["descripcion"] = fields.text(data, "descripcion", required=True)
    if "clausula" in data:
        values["clausula"] = _clause(data["clausula"])
    if "evidencia" in data:
        values["evidencia"] = fields.text(data, "evidencia")
    return values


def _clause(value: Any) -> str | None:
    """An ISO 9001 clause such as ``8.5.1``, trimmed; blank means none."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(BAD_CLAUSE)
    value = value.strip()
    if not value:
        return None
    if len(value) > CLAUSULA_MAX or not CLAUSE.fullmatch(value):
        raise ValidationError(BAD_CLAUSE)
    return value


def _nonconformity(
    session: Session, actor: Actor, parent: Auditoria, descripcion: str, tipo: TipoHallazgo
) -> NoConformidad:
    """Create the nonconformity of a nonconformity finding (decision AP5).

    The nonconformity service authorizes, validates, stamps and audits it; the
    legacy ``responsable`` name is filled in from the person cited.
    """
    data: dict[str, Any] = {
        "descripcion": f"Hallazgo de auditoría ({parent.area_auditada}, "
                       f"{_day(parent.fecha)}): {descripcion}",
        "fecha_detectada": parent.fecha,
        "origen": OrigenNoConformidad.auditoria,
        "gravedad": NC_SEVERITY[tipo],
    }
    owner_id = parent.responsable_area_id
    owner = session.get(Person, owner_id) if owner_id is not None else None
    if owner is not None and owner.activo:
        data["responsable_id"] = owner.id
    return nonconformities.create(session, actor, data)


def _follow_severity(session: Session, actor: Actor, nc_id: int, tipo: TipoHallazgo) -> None:
    """Give the linked nonconformity the severity of ``tipo``; only while it is open."""
    nc = nonconformities.lock(session, nc_id)
    severity = NC_SEVERITY[tipo]
    if nc.gravedad is severity:
        return
    if nc.estado is not EstadoNoConformidad.abierta:
        raise ValidationError(
            "No se puede cambiar la gravedad: la no conformidad vinculada ya no está "
            f"abierta (estado: {nc.estado.value.lower()})."
        )
    nonconformities.update(session, actor, nc_id, {"gravedad": severity})


def _day(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(CONFLICT) from exc
