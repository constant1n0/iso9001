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

``responsable_id`` cites the person responsible (``personas``, see
``people.check_reference``); ``responsable`` keeps the legacy free-text name,
which a write that leaves it blank takes from that person (``people.fill_name``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import NoConformidad
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
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


# Newest first; the id breaks ties between rows detected on the same day.
_ORDER = (NoConformidad.fecha_detectada.desc(), NoConformidad.id.desc())


def _conditions(
    descripcion: str | None, estado: str | None, fecha_detectada: date | None
) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if descripcion:
        where.append(NoConformidad.descripcion.ilike(f"%{descripcion}%"))
    if estado:
        where.append(NoConformidad.estado == estado)
    if fecha_detectada:
        where.append(NoConformidad.fecha_detectada == fecha_detectada)
    return where


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
    where = _conditions(descripcion, estado, fecha_detectada)
    return list(session.scalars(select(NoConformidad).where(*where).order_by(*_ORDER)))


def list_page(
    session: Session,
    actor: Actor,
    *,
    descripcion: str | None = None,
    estado: str | None = None,
    fecha_detectada: date | None = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[NoConformidad], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    page, per_page = crud.page_bounds(page, per_page)
    where = _conditions(descripcion, estado, fecha_detectada)
    total = session.scalar(select(func.count()).select_from(NoConformidad).where(*where))
    query = (
        select(NoConformidad).where(*where).order_by(*_ORDER)
        .limit(per_page).offset((page - 1) * per_page)
    )
    return list(session.scalars(query)), total


def available_states(session: Session, actor: Actor) -> list[str]:
    """Fixed states first, then any legacy free-text values still stored."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    stored = set(session.scalars(select(NoConformidad.estado).distinct()))
    return [*ESTADOS_NO_CONFORMIDAD, *sorted(stored - set(ESTADOS_NO_CONFORMIDAD))]


WRITABLE_FIELDS = frozenset({
    "descripcion", "fecha_detectada", "responsable", "responsable_id", "estado",
    "accion_correctiva",
})
RESPONSABLE_MAX = 50  # mirrors NoConformidad.responsable and the web form


def _clean(
    session: Session, data: Mapping[str, Any], current: NoConformidad | None = None
) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values.

    ``current`` is the nonconformity being updated (``None`` when creating).
    """
    current_estado = current.estado if current is not None else None
    unknown = sorted(set(data) - WRITABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Campos no permitidos: {', '.join(unknown)}.")
    clean: dict[str, Any] = {}
    if "descripcion" in data:
        clean["descripcion"] = fields.text(data, "descripcion", required=True)
    if "fecha_detectada" in data:
        value = data["fecha_detectada"]
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValidationError("La fecha detectada es obligatoria y debe ser una fecha.")
        clean["fecha_detectada"] = value
    if "responsable" in data:
        clean["responsable"] = fields.text(data, "responsable", max_length=RESPONSABLE_MAX)
    if "accion_correctiva" in data:
        clean["accion_correctiva"] = fields.text(data, "accion_correctiva", strip=False)
    if "estado" in data:
        estado = data["estado"]
        if estado is None or (
            estado not in ESTADOS_NO_CONFORMIDAD and estado != current_estado
        ):
            raise ValidationError("El estado no es válido.")
        clean["estado"] = estado
    if "responsable_id" in data:  # last: it queries the database
        clean["responsable_id"] = people.reference(
            session, data, "responsable_id",
            current.responsable_id if current is not None else None,
        )
    return clean


def _fill_responsable(
    session: Session, data: Mapping[str, Any], current: NoConformidad | None = None
) -> Mapping[str, Any]:
    """``data`` with a blank ``responsable`` taken from the linked person."""
    return people.fill_name(session, data, current, link="responsable_id",
                            text="responsable", max_length=RESPONSABLE_MAX)


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> NoConformidad:
    """Create a nonconformity; ``descripcion`` and ``fecha_detectada`` are required."""
    policy.require(actor, Action.CREATE, Resource.NONCONFORMITIES)
    data = _fill_responsable(session, data)
    missing = {"descripcion", "fecha_detectada"} - set(data)
    if missing:
        raise ValidationError(f"Faltan campos obligatorios: {', '.join(sorted(missing))}.")
    values = {"estado": ESTADO_ABIERTA} | _clean(session, data)
    nc = NoConformidad(**values)
    stamp_created(nc, actor)
    session.add(nc)
    try:
        audit.record(session, actor, "create", nc)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict() from exc
    return nc


def update(
    session: Session, actor: Actor, nc_id: int, data: Mapping[str, Any]
) -> NoConformidad:
    """Apply the given fields; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.NONCONFORMITIES)
    nc = _load(session, nc_id)
    values = _clean(session, _fill_responsable(session, data, nc), current=nc)
    before = audit.snapshot(nc)
    if all(getattr(nc, key) == value for key, value in values.items()):
        return nc
    for key, value in values.items():
        setattr(nc, key, value)
    stamp_updated(nc, actor)
    audit.record(session, actor, "update", nc, before=before)
    _flush(session)
    return nc


def delete(session: Session, actor: Actor, nc_id: int) -> None:
    """Hard-delete a nonconformity, keeping its full snapshot in the audit log."""
    policy.require(actor, Action.DELETE, Resource.NONCONFORMITIES)
    nc = _load(session, nc_id)
    audit.record(session, actor, "delete", nc)
    session.delete(nc)
    _flush(session)
