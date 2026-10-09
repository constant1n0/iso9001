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

"""Corrective actions of a nonconformity and their effectiveness (ISO 9001 clause 10.2).

Decisions N3-N5 of ``nc-capa-loop``. Policy resource ``CORRECTIVE_ACTIONS``
has the nonconformity grant: every role reads, creates and updates,
administrators delete, and the ``mcp`` channel never deletes. ``verify`` is
further limited to administrators and auditors.

An action belongs to one nonconformity for good, names its owner
(``responsable_id``, a person) and a planned date, and is done once
``fecha_realizada`` is set, never before the nonconformity was detected. Its
status is derived (``AccionCorrectiva.estado``). ``verify`` records the
effectiveness check of a done action: result, date, verifier (a person other
than the owner) and evidence. The verification columns are not writable
through ``create`` or ``update``; a verified action is read-only, though an
administrator may still delete it.

Actions are added, changed, verified or deleted only while their
nonconformity is open. Each write takes the nonconformity's row lock first
(``nonconformities.lock``), so concurrent writes serialize on PostgreSQL, and
ends by moving it to the state its actions call for
(``nonconformities.sync_state``). Every write is audited; like every service
this module flushes and never commits.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import and_, false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AccionCorrectiva, EstadoAccionCorrectiva, NoConformidad, ResultadoVerificacion, RoleEnum,
)
from . import audit, crud, fields, nonconformities, people, policy
from .actor import Actor
from .attribution import stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

DESCRIPCION_MAX = 1000  # mirrors AccionCorrectiva.descripcion
VERIFY_ROLES = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})

NOT_FOUND = "Acción correctiva no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
UNKNOWN_NC = "El campo «no_conformidad_id» no corresponde a ninguna no conformidad."
NC_NOT_OPEN = (
    "La no conformidad está cerrada o cancelada: sus acciones correctivas no se pueden "
    "añadir, modificar, verificar ni eliminar."
)
MOVED = "Una acción correctiva no puede pasar a otra no conformidad."
VERIFIED_READ_ONLY = "Una acción correctiva verificada no se puede modificar."
DONE_BEFORE_DETECTED = (
    "La fecha de realización no puede ser anterior a la fecha en que se detectó "
    "la no conformidad."
)
NOT_DONE = (
    "Solo se puede verificar una acción correctiva realizada; indica antes su fecha "
    "de realización."
)
ALREADY_VERIFIED = "Esta acción correctiva ya está verificada."
VERIFIER_IS_OWNER = (
    "La persona que verifica la eficacia no puede ser la responsable de la acción."
)
VERIFIED_BEFORE_DONE = (
    "La fecha de verificación no puede ser anterior a la fecha de realización."
)


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A real ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


SPEC = crud.Spec(
    model=AccionCorrectiva,
    resource=Resource.CORRECTIVE_ACTIONS,
    not_found=NOT_FOUND,
    fields=(
        crud.Field("no_conformidad_id", partial(fields.integer, required=True), required=True),
        crud.Field("descripcion", partial(fields.text, required=True, max_length=DESCRIPCION_MAX),
                   required=True),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("fecha_prevista", fields.required_date, required=True),
        crud.Field("fecha_realizada", _optional_date),
    ),
    order_by=(AccionCorrectiva.no_conformidad_id, AccionCorrectiva.id),
)
WRITABLE_FIELDS = SPEC.writable
REQUIRED_ON_CREATE = frozenset(f.name for f in SPEC.fields if f.required)

get = partial(crud.get, SPEC)

_STATUS_CONDITIONS = {
    EstadoAccionCorrectiva.planificada: AccionCorrectiva.fecha_realizada.is_(None),
    EstadoAccionCorrectiva.realizada: and_(
        AccionCorrectiva.fecha_realizada.is_not(None),
        AccionCorrectiva.resultado_verificacion.is_(None),
    ),
    EstadoAccionCorrectiva.verificada_eficaz:
        AccionCorrectiva.resultado_verificacion == ResultadoVerificacion.eficaz,
    EstadoAccionCorrectiva.verificada_no_eficaz:
        AccionCorrectiva.resultado_verificacion == ResultadoVerificacion.no_eficaz,
}


def _matches(column: Any, value: Any) -> Any:
    """``column == value``, or nothing when no stored id can equal ``value``."""
    return column == value if people.is_db_id(value) else false()


def _conditions(no_conformidad_id: int | None, responsable_id: int | None,
                estado: EstadoAccionCorrectiva | str | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND.

    ``estado`` is the derived status, given as the member or its name.
    """
    where: list[Any] = []
    if no_conformidad_id is not None:
        where.append(_matches(AccionCorrectiva.no_conformidad_id, no_conformidad_id))
    if responsable_id is not None:
        where.append(_matches(AccionCorrectiva.responsable_id, responsable_id))
    if estado:
        try:
            status = fields.enum_member({"estado": estado}, "estado", EstadoAccionCorrectiva)
        except ValidationError:
            raise ValidationError("El estado no es válido.") from None
        where.append(_STATUS_CONDITIONS[status])
    return where


def list_(
    session: Session, actor: Actor, *, no_conformidad_id: int | None = None,
    responsable_id: int | None = None, estado: EstadoAccionCorrectiva | str | None = None,
) -> list[AccionCorrectiva]:
    """Actions by nonconformity, then in registration order; filters combine with AND."""
    policy.require(actor, Action.READ, Resource.CORRECTIVE_ACTIONS)
    where = _conditions(no_conformidad_id, responsable_id, estado)
    return crud.list_(SPEC, session, actor, where)


def list_page(
    session: Session, actor: Actor, *, no_conformidad_id: int | None = None,
    responsable_id: int | None = None, estado: EstadoAccionCorrectiva | str | None = None,
    page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[AccionCorrectiva], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    policy.require(actor, Action.READ, Resource.CORRECTIVE_ACTIONS)
    where = _conditions(no_conformidad_id, responsable_id, estado)
    return crud.list_page(SPEC, session, actor, where, page=page, per_page=per_page)


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> AccionCorrectiva:
    """Add an action to an open nonconformity, which then follows its actions.

    ``no_conformidad_id``, ``descripcion``, ``responsable_id`` (an active
    person) and ``fecha_prevista`` are required; ``fecha_realizada`` is
    optional. The first action moves an ``abierta`` nonconformity on.
    """
    policy.require(actor, Action.CREATE, Resource.CORRECTIVE_ACTIONS)
    _check_keys(data)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    try:
        nc = _open(session, fields.integer(data, "no_conformidad_id", required=True))
    except NotFound:
        raise ValidationError(UNKNOWN_NC) from None
    _check_done_date(data, nc)
    created = crud.create(SPEC, session, actor, data)
    nonconformities.sync_state(session, actor, nc)
    return created


def update(
    session: Session, actor: Actor, action_id: int, data: Mapping[str, Any]
) -> AccionCorrectiva:
    """Apply the given fields; a call that changes nothing writes nothing.

    Refused for a verified action, for a closed or cancelled nonconformity and
    for a different ``no_conformidad_id``.
    """
    policy.require(actor, Action.UPDATE, Resource.CORRECTIVE_ACTIONS)
    found, nc = _locked(session, action_id)
    _check_keys(data)
    if found.resultado_verificacion is not None:
        raise ValidationError(VERIFIED_READ_ONLY)
    if "no_conformidad_id" in data and fields.integer(
        data, "no_conformidad_id", required=True
    ) != found.no_conformidad_id:
        raise ValidationError(MOVED)
    _check_done_date(data, nc)
    updated = crud.update(SPEC, session, actor, found.id, data)
    nonconformities.sync_state(session, actor, nc)
    return updated


def delete(session: Session, actor: Actor, action_id: int) -> None:
    """Hard-delete an action, verified or not, while its nonconformity is open.

    The policy leaves deleting to administrators; the audit row keeps the full
    snapshot.
    """
    policy.require(actor, Action.DELETE, Resource.CORRECTIVE_ACTIONS)
    found, nc = _locked(session, action_id)
    crud.delete(SPEC, session, actor, found.id)
    nonconformities.sync_state(session, actor, nc)


def verify(
    session: Session, actor: Actor, action_id: int, *, resultado: ResultadoVerificacion | str,
    fecha: date, verificador_id: int, evidencia: str,
) -> AccionCorrectiva:
    """Record whether a done action proved effective; administrators and auditors only.

    Args:
        resultado: The result, as a ``ResultadoVerificacion`` member or name.
        fecha: When it was verified; not before the action was done.
        verificador_id: The person who verified it: an active person other
            than the action's owner. The rule is on people, so the acting
            user's own person may verify.
        evidencia: What shows the result; required and stored trimmed.

    Raises:
        PermissionDenied: The actor may not verify actions.
        NotFound: Unknown action.
        ValidationError: The nonconformity is closed or cancelled, the action
            is not done or already verified, or a value breaks a rule above.
            Messages name the columns (``resultado_verificacion``,
            ``fecha_verificacion``, ``verificador_id``, ``evidencia_verificacion``).
    """
    policy.require(actor, Action.UPDATE, Resource.CORRECTIVE_ACTIONS)
    if actor.role not in VERIFY_ROLES:
        raise PermissionDenied()
    found, nc = _locked(session, action_id)
    if found.resultado_verificacion is not None:
        raise ValidationError(ALREADY_VERIFIED)
    if found.fecha_realizada is None:
        raise ValidationError(NOT_DONE)
    given = {"resultado_verificacion": resultado, "fecha_verificacion": fecha,
             "verificador_id": verificador_id, "evidencia_verificacion": evidencia}
    values = {
        "resultado_verificacion": fields.enum_member(
            given, "resultado_verificacion", ResultadoVerificacion),
        "fecha_verificacion": fields.required_date(given, "fecha_verificacion"),
        "verificador_id": fields.integer(given, "verificador_id", required=True),
        "evidencia_verificacion": fields.text(given, "evidencia_verificacion", required=True),
    }
    if values["fecha_verificacion"] < found.fecha_realizada:
        raise ValidationError(VERIFIED_BEFORE_DONE)
    if values["verificador_id"] == found.responsable_id:
        raise ValidationError(VERIFIER_IS_OWNER)
    people.check_reference(session, "verificador_id", values["verificador_id"])
    before = audit.snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc
    nonconformities.sync_state(session, actor, nc)
    return found


def _check_keys(data: Any) -> None:
    """Refuse anything but a mapping of writable fields."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)


def _open(session: Session, nc_id: int) -> NoConformidad:
    """The nonconformity, locked and read afresh; refused unless it is open."""
    nc = nonconformities.lock(session, nc_id)
    if nc.estado in nonconformities.TERMINAL_STATES:
        raise ValidationError(NC_NOT_OPEN)
    return nc


def _fresh(session: Session, action_id: int) -> AccionCorrectiva | None:
    """The action as stored now, or ``None``."""
    if not people.is_db_id(action_id):
        return None
    return session.scalars(
        select(AccionCorrectiva).where(AccionCorrectiva.id == action_id)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _locked(session: Session, action_id: int) -> tuple[AccionCorrectiva, NoConformidad]:
    """An action and its open nonconformity; the action is read again under the lock."""
    found = _fresh(session, action_id)
    if found is None:
        raise NotFound(NOT_FOUND)
    nc = _open(session, found.no_conformidad_id)
    found = _fresh(session, action_id)  # a concurrent write may have changed or deleted it
    if found is None:
        raise NotFound(NOT_FOUND)
    return found, nc


def _check_done_date(data: Mapping[str, Any], nc: NoConformidad) -> None:
    """Refuse a done date earlier than the nonconformity's detection."""
    if "fecha_realizada" not in data:
        return
    done = _optional_date(data, "fecha_realizada")
    if done is not None and done < nc.fecha_detectada:
        raise ValidationError(DONE_BEFORE_DETECTED)
