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

States (decisions N2 and N5 of ``nc-capa-loop``): ``estado`` is never part of
a write. A new nonconformity starts ``abierta``; while open, its state follows
its corrective actions (``expected_state``, applied by ``sync_state`` after
every write of ``corrective_actions``). ``close`` (administrators and
auditors, once every action proved effective), ``cancel`` (administrators and
auditors, with a reason) and ``reopen`` (administrators) are the explicit
transitions; closing and cancelling record the date the adapter passes. Every
state change goes through ``_transition``, which keeps ``fecha_cierre`` and
``motivo_cancelacion`` consistent. A ``cerrada`` or ``cancelada``
nonconformity is read-only until it is reopened.

Writes that can move the state take the nonconformity's row lock first
(``lock``), so concurrent ones serialize on PostgreSQL and the second sees
the first one's outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AccionCorrectiva, EstadoNoConformidad, GravedadNoConformidad, NoConformidad,
    OrigenNoConformidad, ResultadoVerificacion, RoleEnum,
)
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

OPEN_STATES = (
    EstadoNoConformidad.abierta,
    EstadoNoConformidad.accion_planificada,
    EstadoNoConformidad.en_verificacion,
)
TERMINAL_STATES = (EstadoNoConformidad.cerrada, EstadoNoConformidad.cancelada)
# Beyond writing nonconformities (the policy matrix), these roles cancel, close or reopen.
CANCEL_ROLES = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})
CLOSE_ROLES = CANCEL_ROLES
REOPEN_ROLES = frozenset({RoleEnum.ADMINISTRADOR})
NOT_FOUND = "No conformidad no encontrada."
ALREADY_TERMINAL = "La no conformidad ya está cerrada o cancelada."
DETECTED_AFTER_DONE = (
    "La fecha de detección no puede ser posterior a la fecha de realización de sus "
    "acciones correctivas."
)
READ_ONLY_MESSAGE = (
    "Esta no conformidad está cerrada o cancelada y no se puede modificar; "
    "un administrador puede reabrirla."
)


def get(session: Session, actor: Actor, nc_id: int) -> NoConformidad:
    """Return one nonconformity or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    return _load(session, nc_id)


def _load(session: Session, nc_id: int) -> NoConformidad:
    nc = session.get(NoConformidad, nc_id)
    if nc is None:
        raise NotFound(NOT_FOUND)
    return nc


def lock(session: Session, nc_id: int) -> NoConformidad:
    """Load a nonconformity afresh with its row locked (``FOR UPDATE``), or raise ``NotFound``.

    The lock lasts until the adapter ends the transaction; SQLite ignores it.
    """
    found = None
    if people.is_db_id(nc_id):
        found = session.scalars(
            select(NoConformidad).where(NoConformidad.id == nc_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


# Newest first; the id breaks ties between rows detected on the same day.
_ORDER = (NoConformidad.fecha_detectada.desc(), NoConformidad.id.desc())


def _state_filter(estado: EstadoNoConformidad | str) -> EstadoNoConformidad:
    """A state filter given as the member or its name; anything else is refused."""
    try:
        return fields.enum_member({"estado": estado}, "estado", EstadoNoConformidad)
    except ValidationError:
        raise ValidationError("El estado no es válido.") from None


def _conditions(
    descripcion: str | None,
    estado: EstadoNoConformidad | str | None,
    fecha_detectada: date | None,
) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if descripcion:
        where.append(NoConformidad.descripcion.ilike(f"%{descripcion}%"))
    if estado:
        where.append(NoConformidad.estado == _state_filter(estado))
    if fecha_detectada:
        where.append(NoConformidad.fecha_detectada == fecha_detectada)
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    descripcion: str | None = None,
    estado: EstadoNoConformidad | str | None = None,
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
    estado: EstadoNoConformidad | str | None = None,
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


WRITABLE_FIELDS = frozenset({
    "descripcion", "fecha_detectada", "responsable", "responsable_id", "origen", "gravedad",
    "contencion", "causa_raiz", "accion_correctiva",
})
RESPONSABLE_MAX = 50  # mirrors NoConformidad.responsable and the web form
_OPTIONAL_ENUMS = {"origen": OrigenNoConformidad, "gravedad": GravedadNoConformidad}


def _optional_enum(data: Mapping[str, Any], key: str) -> Any:
    """An optional enum member given as the member or its name; blank means none."""
    if data[key] is None or data[key] == "":
        return None
    return fields.enum_member(data, key, _OPTIONAL_ENUMS[key])


def _clean(
    session: Session, data: Mapping[str, Any], current: NoConformidad | None = None
) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values.

    ``current`` is the nonconformity being updated (``None`` when creating).
    """
    fields.reject_unknown(data, WRITABLE_FIELDS)
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
    for key in _OPTIONAL_ENUMS.keys() & data.keys():
        clean[key] = _optional_enum(data, key)
    for key in ("contencion", "causa_raiz", "accion_correctiva"):
        if key in data:
            clean[key] = fields.text(data, key, strip=key != "accion_correctiva")
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
    nc = NoConformidad(estado=EstadoNoConformidad.abierta, **_clean(session, data))
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
    """Apply the given fields; a call that changes nothing writes nothing.

    A closed or cancelled nonconformity is read-only (``ValidationError``).
    """
    policy.require(actor, Action.UPDATE, Resource.NONCONFORMITIES)
    nc = lock(session, nc_id)
    if nc.estado in TERMINAL_STATES:
        raise ValidationError(READ_ONLY_MESSAGE)
    values = _clean(session, _fill_responsable(session, data, nc), current=nc)
    before = audit.snapshot(nc)
    if all(getattr(nc, key) == value for key, value in values.items()):
        return nc
    if "fecha_detectada" in values:
        _check_detection_date(session, nc.id, values["fecha_detectada"])
    for key, value in values.items():
        setattr(nc, key, value)
    stamp_updated(nc, actor)
    audit.record(session, actor, "update", nc, before=before)
    _flush(session)
    return nc


def _check_detection_date(session: Session, nc_id: int, detected: date) -> None:
    """A detection date never falls after a done date already recorded on its actions."""
    earliest_done = session.scalar(
        select(func.min(AccionCorrectiva.fecha_realizada))
        .where(AccionCorrectiva.no_conformidad_id == nc_id)
    )
    if earliest_done is not None and detected > earliest_done:
        raise ValidationError(DETECTED_AFTER_DONE)


def may_cancel(actor: Actor, nc: NoConformidad) -> bool:
    """Whether ``actor`` may cancel ``nc`` now (role, token scope and state)."""
    return _may(actor, CANCEL_ROLES) and nc.estado not in TERMINAL_STATES


def may_close(actor: Actor, nc: NoConformidad) -> bool:
    """Whether ``actor`` may try to close ``nc`` now (role, token scope and state).

    Whether its actions allow it is checked by ``close`` (see ``close_blockers``).
    """
    return _may(actor, CLOSE_ROLES) and nc.estado not in TERMINAL_STATES


def may_reopen(actor: Actor, nc: NoConformidad) -> bool:
    """Whether ``actor`` may reopen ``nc`` now (role, token scope and state)."""
    return _may(actor, REOPEN_ROLES) and nc.estado in TERMINAL_STATES


def _may(actor: Actor, roles: frozenset[RoleEnum]) -> bool:
    return (
        policy.can(actor, Action.UPDATE, Resource.NONCONFORMITIES) and actor.role in roles
    )


def _require_role(actor: Actor, roles: frozenset[RoleEnum]) -> None:
    """The policy check for writing nonconformities, narrowed to ``roles``."""
    policy.require(actor, Action.UPDATE, Resource.NONCONFORMITIES)
    if actor.role not in roles:
        raise PermissionDenied()


def cancel(
    session: Session, actor: Actor, nc_id: int, motivo: str | None, *, today: date,
) -> NoConformidad:
    """Cancel an open nonconformity, recording why; administrators and auditors only.

    Args:
        motivo: Why it is cancelled; required and stored trimmed.
        today: The closing date to record (the adapter's local date).

    Raises:
        PermissionDenied: The actor may not cancel nonconformities.
        ValidationError: The reason is blank or the record is already closed
            or cancelled.
    """
    _require_role(actor, CANCEL_ROLES)
    nc = lock(session, nc_id)
    if not isinstance(motivo, str) or not motivo.strip():
        raise ValidationError("El motivo de la cancelación es obligatorio.")
    if nc.estado in TERMINAL_STATES:
        raise ValidationError(ALREADY_TERMINAL)
    _transition(session, actor, nc, EstadoNoConformidad.cancelada,
                today=today, motivo=motivo.strip())
    return nc


def close(session: Session, actor: Actor, nc_id: int, *, today: date) -> NoConformidad:
    """Close an open nonconformity; administrators and auditors only (decision N5).

    Every corrective action must be verified effective; an ineffective one
    followed by a later action no longer counts (``close_blockers``).

    Args:
        today: The closing date to record (the adapter's local date).

    Raises:
        PermissionDenied: The actor may not close nonconformities.
        ValidationError: Already closed or cancelled, or the actions do not
            allow it yet; the message says what is missing.
    """
    _require_role(actor, CLOSE_ROLES)
    nc = lock(session, nc_id)
    if nc.estado in TERMINAL_STATES:
        raise ValidationError(ALREADY_TERMINAL)
    missing = close_blockers(_actions(session, nc.id))
    if missing:
        raise ValidationError(f"No se puede cerrar la no conformidad: {'; '.join(missing)}.")
    _transition(session, actor, nc, EstadoNoConformidad.cerrada, today=today)
    return nc


def reopen(session: Session, actor: Actor, nc_id: int) -> NoConformidad:
    """Reopen a closed or cancelled nonconformity; administrators only.

    It lands on the state its corrective actions call for (``expected_state``,
    ``abierta`` without actions); the closing date and the cancellation reason
    are cleared.
    """
    _require_role(actor, REOPEN_ROLES)
    nc = lock(session, nc_id)
    if nc.estado not in TERMINAL_STATES:
        raise ValidationError("Solo se puede reabrir una no conformidad cerrada o cancelada.")
    _transition(session, actor, nc, expected_state(_actions(session, nc.id)))
    return nc


def _actions(session: Session, nc_id: int) -> list[AccionCorrectiva]:
    """The corrective actions of a nonconformity, in the order they were registered."""
    return list(session.scalars(
        select(AccionCorrectiva).where(AccionCorrectiva.no_conformidad_id == nc_id)
        .order_by(AccionCorrectiva.id)
    ))


def _latest_failed(actions: Sequence[AccionCorrectiva]) -> bool:
    """Whether the latest action was verified not effective: a new one is needed."""
    return bool(actions) and actions[-1].resultado_verificacion is ResultadoVerificacion.no_eficaz


def expected_state(actions: Sequence[AccionCorrectiva]) -> EstadoNoConformidad:
    """The open state that ``actions``, in registration order, call for (decision N5).

    ``abierta`` without actions; ``accion_planificada`` while an action is not
    done or the latest one was verified not effective; otherwise
    ``en_verificacion``, which only an explicit ``close`` leaves. An
    ineffective action followed by a later one stays as evidence.
    """
    if not actions:
        return EstadoNoConformidad.abierta
    if _latest_failed(actions) or any(a.fecha_realizada is None for a in actions):
        return EstadoNoConformidad.accion_planificada
    return EstadoNoConformidad.en_verificacion


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def close_blockers(actions: Sequence[AccionCorrectiva]) -> list[str]:
    """What keeps a nonconformity with ``actions`` from closing, in Spanish; empty if nothing."""
    if not actions:
        return ["no tiene ninguna acción correctiva"]
    missing = []
    not_done = sum(a.fecha_realizada is None for a in actions)
    unverified = sum(
        a.fecha_realizada is not None and a.resultado_verificacion is None for a in actions
    )
    if not_done:
        missing.append(_count(not_done, "acción sin realizar", "acciones sin realizar"))
    if unverified:
        missing.append(_count(unverified, "acción pendiente de verificar",
                              "acciones pendientes de verificar"))
    if _latest_failed(actions):
        missing.append(
            "la última acción correctiva no fue eficaz; registra una nueva acción correctiva"
        )
    return missing


def sync_state(session: Session, actor: Actor, nc: NoConformidad) -> None:
    """Move an open ``nc`` to the state its actions call for (``expected_state``).

    ``corrective_actions`` calls it after each write, holding the row lock; a
    closed or cancelled nonconformity is left as it is.
    """
    if nc.estado in TERMINAL_STATES:
        return
    wanted = expected_state(_actions(session, nc.id))
    if wanted is not nc.estado:
        _transition(session, actor, nc, wanted)


def _transition(
    session: Session, actor: Actor, nc: NoConformidad, estado: EstadoNoConformidad,
    *, today: date | None = None, motivo: str | None = None,
) -> None:
    """Move ``nc`` to ``estado``, then stamp, audit and flush; the only writer of states.

    Callers check permissions and whether the move is allowed first.
    """
    before = audit.snapshot(nc)
    _set_state(nc, estado, today=today, motivo=motivo)
    stamp_updated(nc, actor)
    audit.record(session, actor, "update", nc, before=before)
    _flush(session)


def _set_state(
    nc: NoConformidad, estado: EstadoNoConformidad,
    *, today: date | None = None, motivo: str | None = None,
) -> None:
    """Set ``estado`` and keep its companions consistent.

    ``fecha_cierre`` holds ``today`` in a terminal state, which needs it (there
    is no server-clock fallback), and is empty otherwise;
    ``motivo_cancelacion`` is kept only while ``cancelada``.
    """
    terminal = estado in TERMINAL_STATES
    if terminal and today is None:
        raise ValueError("A closed or cancelled state needs the adapter's date.")
    nc.estado = estado
    nc.fecha_cierre = today if terminal else None
    nc.motivo_cancelacion = motivo if estado is EstadoNoConformidad.cancelada else None


def delete(session: Session, actor: Actor, nc_id: int) -> None:
    """Hard-delete a nonconformity and its corrective actions, with full audit snapshots."""
    policy.require(actor, Action.DELETE, Resource.NONCONFORMITIES)
    nc = lock(session, nc_id)
    for action in _actions(session, nc.id):
        audit.record(session, actor, "delete", action)
        session.delete(action)
    _flush(session)  # the actions' rows go before the row they reference
    audit.record(session, actor, "delete", nc)
    session.delete(nc)
    _flush(session)
