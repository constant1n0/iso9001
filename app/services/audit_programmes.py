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

"""Yearly internal audit programmes (``ProgramaAuditoria``, ISO 9001 clause 9.2.2).

Decisions AP1 and AP7 of ``audit-programme``. Programmes share the ``AUDITS``
policy resource: administrators and auditors read, create and update,
administrators delete, and the ``mcp`` channel never deletes. One programme
per year (2000-2100), with objectives and scope; it starts as ``borrador``.

- ``approve`` is for administrators only and never on the ``mcp`` channel: it
  moves a draft to ``aprobado`` with the adapter's date and an active approving
  person.
- The year of an approved programme never changes, nor does that of a draft
  that already has audits (each audit's date falls in its programme's year,
  see ``audits``).
- A programme with audits cannot be deleted (``Conflict``).

Like every service this module flushes and never commits, writes an audit row
next to each change, and an update that changes nothing writes nothing.
Programme rows are locked (``FOR UPDATE``) before they change.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Auditoria, EstadoProgramaAuditoria, ProgramaAuditoria, RoleEnum
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

ANIO_MIN, ANIO_MAX = 2000, 2100  # mirrors ``ck_programas_auditoria_anio``
WRITABLE_FIELDS = frozenset({"anio", "objetivos", "alcance"})
REQUIRED_ON_CREATE = WRITABLE_FIELDS
APPROVE_ROLES = frozenset({RoleEnum.ADMINISTRADOR})

NOT_FOUND = "Programa de auditoría no encontrado."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
YEAR_RANGE = f"El año del programa debe ser un número entre {ANIO_MIN} y {ANIO_MAX}."
YEAR_LOCKED = "El año de un programa aprobado no se puede cambiar."
YEAR_HAS_AUDITS = "No se puede cambiar el año de un programa que ya tiene auditorías."
HAS_AUDITS = "No se puede eliminar un programa que tiene auditorías; quítalas antes."
NOT_DRAFT = "Solo se puede aprobar un programa en borrador."
APPROVER_REQUIRED = "Indica la persona que aprueba el programa."
DUPLICATE = "Ya existe un programa de auditoría para ese año."

_READS = crud.Spec(
    model=ProgramaAuditoria,
    resource=Resource.AUDITS,
    not_found=NOT_FOUND,
    fields=(),  # writes are bespoke (see ``WRITABLE_FIELDS``)
    order_by=(ProgramaAuditoria.anio.desc(), ProgramaAuditoria.id.desc()),
)

get = partial(crud.get, _READS)


def _conditions(anio: int | None, estado: EstadoProgramaAuditoria | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if anio is not None:
        where.append(ProgramaAuditoria.anio == anio)
    if estado is not None:
        where.append(ProgramaAuditoria.estado == estado)
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    anio: int | None = None,
    estado: EstadoProgramaAuditoria | None = None,
) -> list[ProgramaAuditoria]:
    """Programmes, newest year first."""
    return crud.list_(_READS, session, actor, _conditions(anio, estado))


def list_page(
    session: Session,
    actor: Actor,
    *,
    anio: int | None = None,
    estado: EstadoProgramaAuditoria | None = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[ProgramaAuditoria], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(_READS, session, actor, _conditions(anio, estado),
                          page=page, per_page=per_page)


def lock(session: Session, programme_id: int) -> ProgramaAuditoria:
    """Load a programme afresh with its row locked (``FOR UPDATE``), or raise ``NotFound``.

    The lock lasts until the adapter ends the transaction; SQLite ignores it.
    """
    found = None
    if people.is_db_id(programme_id):
        found = session.scalars(
            select(ProgramaAuditoria).where(ProgramaAuditoria.id == programme_id)
            .with_for_update().execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> ProgramaAuditoria:
    """Create a draft programme; year, objectives and scope are required."""
    policy.require(actor, Action.CREATE, Resource.AUDITS)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values = _clean(data)
    _ensure_year_free(session, values["anio"])
    created = ProgramaAuditoria(estado=EstadoProgramaAuditoria.borrador, **values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:  # a concurrent programme for the same year
        raise Conflict(DUPLICATE) from exc
    return created


def update(
    session: Session, actor: Actor, programme_id: int, data: Mapping[str, Any]
) -> ProgramaAuditoria:
    """Apply the given fields; the year is locked once approved or once it has audits."""
    policy.require(actor, Action.UPDATE, Resource.AUDITS)
    found = lock(session, programme_id)
    values = _clean(data)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if not changes:
        return found
    if "anio" in changes:
        if found.estado is EstadoProgramaAuditoria.aprobado:
            raise ValidationError(YEAR_LOCKED)
        if _has_audits(session, found.id):
            raise ValidationError(YEAR_HAS_AUDITS)
        _ensure_year_free(session, changes["anio"], own_id=found.id)
    _change(session, actor, found, DUPLICATE, **changes)
    return found


def delete(session: Session, actor: Actor, programme_id: int) -> None:
    """Hard-delete a programme without audits, keeping its full snapshot in the audit log.

    An audit committed after the check meets the foreign key at the flush.
    """
    policy.require(actor, Action.DELETE, Resource.AUDITS)
    found = lock(session, programme_id)
    if _has_audits(session, found.id):
        raise Conflict(HAS_AUDITS)
    audit.record(session, actor, "delete", found)
    session.delete(found)
    _flush(session, HAS_AUDITS)


def approve(
    session: Session, actor: Actor, programme_id: int, *, aprobado_por_id: int | None,
    today: date,
) -> ProgramaAuditoria:
    """Approve a draft programme; administrators only, never through the ``mcp`` channel.

    Args:
        aprobado_por_id: The approving person (an active ``personas`` id).
        today: The approval date to record (the adapter's local date).

    Raises:
        PermissionDenied: The actor may not approve programmes.
        NotFound: Unknown programme.
        ValidationError: The programme is not a draft, or the approving
            person is missing, unknown or inactive.
        ValueError: ``today`` is not a ``date`` (a programming error).
    """
    policy.require(actor, Action.UPDATE, Resource.AUDITS)
    if actor.role not in APPROVE_ROLES or actor.channel == "mcp":
        raise PermissionDenied()
    found = lock(session, programme_id)
    if found.estado is not EstadoProgramaAuditoria.borrador:
        raise ValidationError(NOT_DRAFT)
    approver = people.reference(session, {"aprobado_por_id": aprobado_por_id}, "aprobado_por_id")
    if approver is None:
        raise ValidationError(APPROVER_REQUIRED)
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("The approval date must come from the adapter.")
    _change(session, actor, found, None, estado=EstadoProgramaAuditoria.aprobado,
            aprobado_en=today, aprobado_por_id=approver)
    return found


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)
    values: dict[str, Any] = {}
    if "anio" in data:
        anio = data["anio"]
        if not isinstance(anio, int) or isinstance(anio, bool) or not ANIO_MIN <= anio <= ANIO_MAX:
            raise ValidationError(YEAR_RANGE)
        values["anio"] = anio
    for key in ("objetivos", "alcance"):
        if key in data:
            values[key] = fields.text(data, key, required=True)
    return values


def _change(session: Session, actor: Actor, found: ProgramaAuditoria, conflict: str | None,
            **changes: Any) -> None:
    """Apply ``changes`` with stamps and an audit row; ``conflict`` names a refused flush."""
    before = audit.snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    _flush(session, conflict)


def _ensure_year_free(session: Session, anio: int, own_id: int | None = None) -> None:
    query = select(ProgramaAuditoria.id).where(ProgramaAuditoria.anio == anio)
    if own_id is not None:
        query = query.where(ProgramaAuditoria.id != own_id)
    if session.scalar(query) is not None:
        raise Conflict(f"Ya existe un programa de auditoría para {anio}.")


def _has_audits(session: Session, programme_id: int) -> bool:
    return bool(session.scalar(select(exists().where(Auditoria.programa_id == programme_id))))


def _flush(session: Session, message: str | None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc
