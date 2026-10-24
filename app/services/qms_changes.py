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

"""Planned changes to the QMS and their tasks (ISO 9001 clause 6.3, ``change-planning``).

Decisions CP1 to CP6. Two registers, ``changes`` (``CambioSGC``) and ``tasks``
(``TareaCambio``), each with ``get``, ``list_``, ``list_page``, ``create``,
``update`` and ``delete`` like ``quality_objectives``. They share the policy
resource ``QMS_CHANGES``: every role reads, administrators and auditors create
and update, administrators delete, and the ``mcp`` channel never deletes.
Writes take ``today``, the adapter's local date (services never read the
clock), and so do the lists whose ``fuera_de_plazo`` or ``vencida`` filter is
used.

Changes:

- A new change is ``propuesto``. ``titulo``, ``descripcion``, ``origen``,
  ``proposito``, ``responsable_id`` (a person, active when newly chosen) and
  ``fecha_propuesta`` (never after ``today``) are required; ``fecha_prevista``,
  the planned implementation, is never before the proposal.
- ``origen`` is a member name, and the change may name the record behind it
  through the one link of that origin (``models.CHANGE_LINKS``): a review action
  of type ``cambio_sgc``, a nonconformity, an audit, a risk, an opportunity or a
  ``vigente`` context issue (the action and the issue read ``FOR SHARE``). A link
  is checked when newly set; one made earlier stays even if its record changed
  since. A link with another origin is refused (``LINK_NEEDS_ORIGIN``), and so is
  changing the origin while the old link stays (``CLEAR_LINK_FIRST``).
- ``procesos`` and ``documentos`` are lists of process and document ids
  (withdrawn documents too) that replace the links (``cambio_procesos``,
  ``cambio_documentos``). Those tables have no audit rows of their own, so every
  audit row of a change carries both lists next to the columns.
- The state and the columns of each stage (approval, implementation,
  effectiveness review, cancellation) are never part of a write. ``approve``
  (administrators), ``implement`` and ``review_effectiveness`` (administrators
  and auditors) and ``cancel`` (administrators) move a change (CP2), never on
  the ``mcp`` channel; each has a ``may_*`` guard for the screens and refuses a
  failing one with a bare ``PermissionDenied``. Approval needs the four 6.3
  considerations, a planned date and a task that is not cancelled, and records
  the acting user's person; implementation needs every task closed and one
  completed; the effectiveness review needs a comment when the change was not
  effective and a reviewer (the acting user's person) other than the owner.
- What an update may change follows the state (CP3): a ``propuesto`` change
  takes every field; an ``aprobado`` one only ``APPROVED_WRITABLE``, its plan
  being fixed (``PLAN_FIXED``); an ``implantado``, ``eficacia_revisada`` or
  ``cancelado`` one nothing (``READ_ONLY``).
- Only a ``propuesto`` change is deleted, with its tasks (each with its own
  audit row) and its links.
- Filters: ``estado``, ``origen``, ``responsable_id``, ``proceso_id``,
  ``documento_id``, ``fuera_de_plazo`` (``aprobado`` with ``fecha_prevista``
  before ``today``) and ``texto`` (in the title or the description, any case);
  the latest proposed first.

Tasks belong to one change, which they never leave, and are created, changed
and deleted only while it is ``propuesto`` or ``aprobado`` (the change read
``FOR SHARE``, the task locked ``FOR UPDATE``). A ``completada`` task, and only
one, has ``fecha_completada``, never after ``today``. A task is overdue
(``is_overdue``, the ``vencida`` filter) when pending or in progress after its
deadline while its change is still ``propuesto`` or ``aprobado``: a cancelled
change keeps its tasks as they were, and they are no longer due. Filters:
``cambio_id``, ``estado``, ``responsable_id`` and ``vencida``; the soonest
deadline first.

``attention`` gathers what the dashboard flags (CP6), ``in_period`` what a
management review of a period takes as input, and ``for_origin`` the changes
one record gives rise to, for the origin pages. Every rule judges the record as
the write would leave it. An unknown key is refused before anything is looked
up, and an edit that changes nothing writes nothing, whatever the state. Like
every service this module flushes and never commits, writes an audit row next
to each change, and locks the rows that change (``FOR UPDATE``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import and_, exists, false, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    CHANGE_LINKS, AccionRevision, Auditoria, CambioSGC, CuestionContexto, Document,
    EficaciaCambio, EstadoCambio, EstadoCuestion, EstadoTareaCambio, NoConformidad,
    Oportunidad, OrigenCambio, ProcesoOperacion, Riesgo, RoleEnum, TareaCambio,
    TipoAccionRevision,
)
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.QMS_CHANGES
TITLE_MAX = 200  # mirrors ``titulo``
PROCESSES, DOCUMENTS = "procesos", "documentos"
OPEN_STATES = (EstadoCambio.propuesto, EstadoCambio.aprobado)  # tasks are written, cancellable
OPEN_TASK_STATES = (EstadoTareaCambio.pendiente, EstadoTareaCambio.en_curso)
APPROVE_ROLES = CANCEL_ROLES = frozenset({RoleEnum.ADMINISTRADOR})
IMPLEMENT_ROLES = REVIEW_ROLES = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})
# CP3: what an approved change still takes; the rest, its plan, is fixed.
APPROVED_WRITABLE = frozenset({"responsable_id", "fecha_prevista", "comunicacion",
                               PROCESSES, DOCUMENTS})

CHANGE_NOT_FOUND = "Cambio en el SGC no encontrado."
TASK_NOT_FOUND = "Tarea del cambio no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
PROPOSED_IN_FUTURE = "La fecha de propuesta no puede ser futura."
PLANNED_BEFORE_PROPOSAL = (
    "La fecha prevista de implantación no puede ser anterior a la fecha de propuesta.")
LINK_NEEDS_ORIGIN = "El campo «{key}» solo se admite con el origen «{origen}»."
CLEAR_LINK_FIRST = (
    "El cambio sigue vinculado a su origen «{origen}» por «{key}»; quita antes ese vínculo "
    "para cambiar el origen.")
UNKNOWN_LINK = "El campo «{key}» no corresponde a {nothing}."
REVIEW_ACTION_NOT_A_CHANGE = (
    "La acción de la revisión por la dirección no es de tipo "
    f"«{TipoAccionRevision.cambio_sgc.value}»; elige una de ese tipo.")
ISSUE_SUPERSEDED = "La cuestión del contexto está superada; elige una vigente."
PROCESS_IDS = "El campo «procesos» debe ser una lista de identificadores de proceso."
DOCUMENT_IDS = "El campo «documentos» debe ser una lista de identificadores de documento."
UNKNOWN_PROCESSES = "Procesos desconocidos en «procesos»: {ids}."
UNKNOWN_DOCUMENTS = "Documentos desconocidos en «documentos»: {ids}."
PLAN_FIXED = (
    "El cambio está aprobado y su plan ya no cambia ({campos}); solo cambian el responsable, "
    "la fecha prevista, la comunicación, los procesos y los documentos.")
READ_ONLY = {
    EstadoCambio.implantado: (
        "Un cambio implantado ya no se modifica; solo queda revisar su eficacia."),
    EstadoCambio.eficacia_revisada: "Un cambio con la eficacia revisada ya no se modifica.",
    EstadoCambio.cancelado: "Un cambio cancelado ya no se modifica.",
}
ONLY_PROPOSED_DELETED = (
    "Solo se puede eliminar un cambio propuesto; uno aprobado se cancela, y los demás se "
    "conservan.")
DELETE_CONFLICT = (
    "No se ha podido eliminar el cambio porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo.")
WRITE_CONFLICT = (
    "No se ha podido guardar el cambio porque los datos han cambiado entretanto; "
    "vuelve a intentarlo.")
NOT_PROPOSED = "Solo se aprueba un cambio propuesto."
APPROVAL_INCOMPLETE = "Para aprobar el cambio falta: {faltan}."
# The 6.3 considerations approval needs (CP2), as ``APPROVAL_INCOMPLETE`` names them.
CONSIDERATIONS = (
    ("consecuencias", "las consecuencias potenciales («consecuencias»)"),
    ("integridad", "la integridad del SGC («integridad»)"),
    ("recursos", "los recursos («recursos»)"),
    ("responsabilidades", "las responsabilidades y autoridades («responsabilidades»)"),
)
PLANNED_DATE_NEEDED = "la fecha prevista de implantación («fecha_prevista»)"
TASK_NEEDED = "al menos una tarea que no esté cancelada"
APPROVER_REQUIRED = (
    "Tu usuario no está vinculado a ninguna persona; vincúlalo para aprobar el cambio.")
NOT_APPROVED = "Solo se implanta un cambio aprobado."
TASKS_OPEN = (
    "Quedan tareas pendientes o en curso; complétalas o cancélalas antes de implantar el "
    "cambio.")
NO_COMPLETED_TASK = "Para implantar el cambio, al menos una de sus tareas debe estar completada."
IMPLEMENTED_IN_FUTURE = "La fecha de implantación no puede ser futura."
IMPLEMENTED_BEFORE_APPROVAL = (
    "La fecha de implantación no puede ser anterior a la aprobación del cambio.")
NOT_IMPLEMENTED = "Solo se revisa la eficacia de un cambio implantado."
COMMENT_REQUIRED = "Explica por qué el cambio no ha sido eficaz («comentario_eficacia»)."
REVIEWED_BEFORE_IMPLEMENTATION = (
    "La fecha de la revisión de la eficacia no puede ser anterior a la implantación.")
REVIEWED_IN_FUTURE = "La fecha de la revisión de la eficacia no puede ser futura."
REVIEWER_REQUIRED = (
    "Tu usuario no está vinculado a ninguna persona; vincúlalo para revisar la eficacia del "
    "cambio.")
REVIEWER_IS_OWNER = "La persona que revisa la eficacia no puede ser la responsable del cambio."
NOT_CANCELLABLE = "Solo se cancela un cambio propuesto o aprobado."
REASON_REQUIRED = "Indica el motivo de la cancelación («motivo_cancelacion»)."
UNKNOWN_CHANGE = "El campo «cambio_id» no corresponde a ningún cambio en el SGC."
TASKS_CLOSED = (
    "Las tareas solo se crean, modifican o eliminan mientras el cambio está propuesto o "
    "aprobado (ahora: {estado}).")
TASK_CHANGE_FIXED = "Una tarea no puede pasar a otro cambio."
COMPLETION_REQUIRED = "Indica la fecha de finalización de una tarea completada."
COMPLETION_ONLY_WHEN_DONE = "Solo una tarea completada tiene fecha de finalización."
COMPLETION_IN_FUTURE = "La fecha de finalización no puede ser futura."
BAD_FILTER = "El filtro «{key}» no admite ese valor."
BAD_FLAG = "El filtro «{key}» debe ser verdadero o falso."

# The record behind each link (``CHANGE_LINKS``) and how a message says "none of them".
_LINK_TARGETS: dict[str, tuple[type, str]] = {
    "accion_revision_id": (AccionRevision, "ninguna acción de una revisión por la dirección"),
    "no_conformidad_id": (NoConformidad, "ninguna no conformidad"),
    "auditoria_id": (Auditoria, "ninguna auditoría"),
    "riesgo_id": (Riesgo, "ningún riesgo"),
    "oportunidad_id": (Oportunidad, "ninguna oportunidad"),
    "cuestion_id": (CuestionContexto, "ninguna cuestión del contexto"),
}
# Targets whose state a link depends on: read ``FOR SHARE`` so it holds until the commit.
_SHARED_TARGETS = (AccionRevision, CuestionContexto)
_LINK_OF_ORIGIN = {origin: column for column, (origin, _) in CHANGE_LINKS.items()}


# -- field validators ------------------------------------------------------------------


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


_TEXT = partial(fields.text, strip=False)
_REQUIRED_TEXT = partial(fields.text, required=True, strip=False)

SPEC = crud.Spec(
    model=CambioSGC,
    resource=RESOURCE,
    not_found=CHANGE_NOT_FOUND,
    fields=(
        crud.Field("titulo", partial(fields.text, required=True, max_length=TITLE_MAX),
                   required=True),
        crud.Field("descripcion", _REQUIRED_TEXT, required=True),
        crud.Field("origen", partial(fields.enum_member, enum_cls=OrigenCambio), required=True),
        *(crud.Field(column, fields.integer) for column in CHANGE_LINKS),
        crud.Field("proposito", _REQUIRED_TEXT, required=True),
        *(crud.Field(name, _TEXT) for name, _ in CONSIDERATIONS),
        crud.Field("comunicacion", _TEXT),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("fecha_propuesta", fields.required_date, required=True),
        crud.Field("fecha_prevista", _optional_date),
    ),
    # The latest proposed first.
    order_by=(CambioSGC.fecha_propuesta.desc(), CambioSGC.id.desc()),
)
# The columns plus ``procesos`` and ``documentos``; the MCP registry mirrors both.
CHANGE_WRITABLE = SPEC.writable | {PROCESSES, DOCUMENTS}
CHANGE_REQUIRED = frozenset(f.name for f in SPEC.fields if f.required)

TASK_SPEC = crud.Spec(
    model=TareaCambio,
    resource=RESOURCE,
    not_found=TASK_NOT_FOUND,
    fields=(
        crud.Field("cambio_id", partial(fields.integer, required=True), required=True),
        crud.Field("descripcion", _REQUIRED_TEXT, required=True),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("fecha_limite", fields.required_date, required=True),
        crud.Field("estado", partial(fields.enum_member, enum_cls=EstadoTareaCambio)),
        crud.Field("fecha_completada", _optional_date),
        crud.Field("resultado", _TEXT),
    ),
    # The soonest deadline first.
    order_by=(TareaCambio.fecha_limite, TareaCambio.id),
)
TASK_REQUIRED = frozenset(f.name for f in TASK_SPEC.fields if f.required)

_FIELDS = {spec.model: {f.name: f for f in spec.fields} for spec in (SPEC, TASK_SPEC)}
# The ids lists: the record they name, its key, its order and the messages.
_LISTS = {
    PROCESSES: (ProcesoOperacion, ProcesoOperacion.id_proceso, ProcesoOperacion.proceso,
                PROCESS_IDS, UNKNOWN_PROCESSES),
    DOCUMENTS: (Document, Document.id, Document.code, DOCUMENT_IDS, UNKNOWN_DOCUMENTS),
}


# -- changes ---------------------------------------------------------------------------


def _create(session: Session, actor: Actor, data: Mapping[str, Any], *,
            today: date) -> CambioSGC:
    """Propose a change; it starts ``propuesto`` and only the transitions move it."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(CHANGE_WRITABLE, data)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.require_keys(data, CHANGE_REQUIRED)
    values, wanted = _clean(data), _wanted(data)
    _check(session, values, None, today)
    # Both lists are set, even empty: loading one later would autoflush the audit row.
    linked = {key: _records(session, key, wanted.get(key, set())) for key in _LISTS}
    created = CambioSGC(estado=EstadoCambio.propuesto, **values, **linked)
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(WRITE_CONFLICT) from exc
    row.after = _snapshot(created)  # the row is still pending: add the lists
    return created


def _update(session: Session, actor: Actor, change_id: int, data: Mapping[str, Any], *,
            today: date) -> CambioSGC:
    """Apply the given fields as the state allows (CP3); the lists replace the links.

    The change is locked (``FOR UPDATE``) while it changes.
    """
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(CHANGE_WRITABLE, data)
    found = _locked(session, change_id)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    changes = {key: value for key, value in _clean(data).items()
               if getattr(found, key) != value}
    wanted = {key: ids for key, ids in _wanted(data).items() if ids != set(_ids(found, key))}
    touched = changes.keys() | wanted.keys()
    if not touched:
        return found
    _check_editable(found, touched)
    _check(session, changes, found, today)
    linked = {key: _records(session, key, ids) for key, ids in wanted.items()}
    before = _snapshot(found)
    for key, value in (changes | linked).items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, WRITE_CONFLICT)
    return found


def _delete(session: Session, actor: Actor, change_id: int) -> None:
    """Hard-delete a ``propuesto`` change with its tasks and links, keeping audit snapshots."""
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _locked(session, change_id)
    if found.estado is not EstadoCambio.propuesto:
        raise ValidationError(ONLY_PROPOSED_DELETED)
    tasks = session.scalars(
        select(TareaCambio).where(TareaCambio.cambio_id == found.id).order_by(TareaCambio.id)
        .with_for_update().execution_options(populate_existing=True)).all()
    for task in tasks:
        audit.record(session, actor, "delete", task)
        session.delete(task)
    _flush(session, DELETE_CONFLICT)  # first: the tasks' foreign key refuses the change
    audit.record(session, actor, "delete", found, before=_snapshot(found))
    session.delete(found)  # its ``cambio_procesos`` and ``cambio_documentos`` rows go with it
    _flush(session, DELETE_CONFLICT)


def _check_editable(found: CambioSGC, touched: Iterable[str]) -> None:
    """What the change's state lets an update touch (CP3)."""
    if found.estado in READ_ONLY:
        raise ValidationError(READ_ONLY[found.estado])
    if found.estado is EstadoCambio.aprobado:
        fixed = [f.name for f in SPEC.fields if f.name in touched
                 and f.name not in APPROVED_WRITABLE]
        if fixed:
            raise ValidationError(PLAN_FIXED.format(campos=", ".join(f"«{key}»"
                                                                    for key in fixed)))


def _check(session: Session, changes: Mapping[str, Any], found: CambioSGC | None,
           today: date) -> None:
    """The rules a change keeps (CP1), on the values the write leaves.

    ``changes`` holds the clean values that differ from ``found`` (every value
    when creating): a value kept as it was is not judged again.
    """
    final = partial(_final, changes, found)
    if "fecha_propuesta" in changes and changes["fecha_propuesta"] > today:
        raise ValidationError(PROPOSED_IN_FUTURE)
    proposed, planned = final("fecha_propuesta"), final("fecha_prevista")
    if planned is not None and proposed is not None and planned < proposed:
        raise ValidationError(PLANNED_BEFORE_PROPOSAL)
    origin = final("origen")
    for column, (needed, _) in CHANGE_LINKS.items():
        if final(column) is not None and needed is not origin:
            template = LINK_NEEDS_ORIGIN if column in changes else CLEAR_LINK_FIRST
            raise ValidationError(template.format(key=column, origen=needed.value))
    for column in CHANGE_LINKS:
        if changes.get(column) is not None:
            _check_link(session, column, changes[column])
    for f in SPEC.fields:
        if f.check is not None and f.name in changes:
            f.check(session, f.name, changes[f.name], None if found is None else
                    getattr(found, f.name))


def _check_link(session: Session, column: str, value: int) -> None:
    """A newly set link names an existing record, of the right kind and state."""
    model, nothing = _LINK_TARGETS[column]
    if model in _SHARED_TARGETS:
        target = _lock(session, model, value, read=True)
    else:
        target = session.get(model, value) if people.is_db_id(value) else None
    if target is None:
        raise ValidationError(UNKNOWN_LINK.format(key=column, nothing=nothing))
    if model is AccionRevision and target.tipo is not TipoAccionRevision.cambio_sgc:
        raise ValidationError(REVIEW_ACTION_NOT_A_CHANGE)
    if model is CuestionContexto and target.estado is not EstadoCuestion.vigente:
        raise ValidationError(ISSUE_SUPERSEDED)


# -- transitions (decision CP2) --------------------------------------------------------


def _may(actor: Actor, roles: frozenset[RoleEnum]) -> bool:
    return (policy.can(actor, Action.UPDATE, RESOURCE) and actor.role in roles
            and actor.channel != "mcp")


def may_approve(actor: Actor, change: CambioSGC | None = None) -> bool:
    """Whether ``actor`` may approve changes (role, scopes, channel), and ``change`` if given."""
    return _may(actor, APPROVE_ROLES) and (change is None
                                           or change.estado is EstadoCambio.propuesto)


def may_implement(actor: Actor, change: CambioSGC | None = None) -> bool:
    """Whether ``actor`` may record implementations, and of ``change`` when given."""
    return _may(actor, IMPLEMENT_ROLES) and (change is None
                                             or change.estado is EstadoCambio.aprobado)


def may_review_effectiveness(actor: Actor, change: CambioSGC | None = None) -> bool:
    """Whether ``actor`` may review the effectiveness of changes, and of ``change`` if given.

    The owner of a change never reviews it; ``review_effectiveness`` says so.
    """
    return _may(actor, REVIEW_ROLES) and (change is None
                                          or change.estado is EstadoCambio.implantado)


def may_cancel(actor: Actor, change: CambioSGC | None = None) -> bool:
    """Whether ``actor`` may cancel changes, and ``change`` when given."""
    return _may(actor, CANCEL_ROLES) and (change is None or change.estado in OPEN_STATES)


def approve(session: Session, actor: Actor, change_id: int, *, today: date) -> CambioSGC:
    """Approve a ``propuesto`` change on ``today``; administrators, never on ``mcp``.

    The approver is the person linked to the acting user, who may be the owner.

    Raises:
        PermissionDenied: ``may_approve`` refuses the actor.
        NotFound: Unknown change.
        ValidationError: Not ``propuesto`` (``NOT_PROPOSED``); a consideration,
            the planned date or a task that is not cancelled is missing (one
            ``APPROVAL_INCOMPLETE`` naming them all); or the acting user has no
            person (``APPROVER_REQUIRED``).
        ValueError: ``today`` is not a ``date`` (a programming error).
    """
    found, today = _begin(session, actor, change_id, may_approve, today)
    if found.estado is not EstadoCambio.propuesto:
        raise ValidationError(NOT_PROPOSED)
    missing = [label for key, label in CONSIDERATIONS if _blank(getattr(found, key))]
    if found.fecha_prevista is None:
        missing.append(PLANNED_DATE_NEEDED)
    if not _has_tasks(session, found.id, TareaCambio.estado != EstadoTareaCambio.cancelada):
        missing.append(TASK_NEEDED)
    if missing:
        raise ValidationError(APPROVAL_INCOMPLETE.format(faltan=_listed(missing)))
    approver = people.of_user(session, actor.user_id)
    if approver is None:
        raise ValidationError(APPROVER_REQUIRED)
    return _move(session, actor, found, estado=EstadoCambio.aprobado,
                 aprobado_por_id=approver, fecha_aprobacion=today)


def implement(session: Session, actor: Actor, change_id: int, *, fecha: date,
              today: date) -> CambioSGC:
    """Record that an ``aprobado`` change was implemented on ``fecha``.

    Administrators and auditors, never on ``mcp``. Every task must be
    ``completada`` or ``cancelada`` and one ``completada``; ``fecha`` is never
    before the approval nor after ``today``.

    Raises:
        PermissionDenied: ``may_implement`` refuses the actor.
        NotFound: Unknown change.
        ValidationError: Not ``aprobado``, a bad date, an open task
            (``TASKS_OPEN``) or none completed (``NO_COMPLETED_TASK``).
        ValueError: ``today`` is not a ``date`` (a programming error).
    """
    found, today = _begin(session, actor, change_id, may_implement, today)
    if found.estado is not EstadoCambio.aprobado:
        raise ValidationError(NOT_APPROVED)
    fecha = _given_date(fecha, "fecha_implantacion")
    if _has_tasks(session, found.id, TareaCambio.estado.in_(OPEN_TASK_STATES)):
        raise ValidationError(TASKS_OPEN)
    if not _has_tasks(session, found.id, TareaCambio.estado == EstadoTareaCambio.completada):
        raise ValidationError(NO_COMPLETED_TASK)
    if fecha > today:
        raise ValidationError(IMPLEMENTED_IN_FUTURE)
    if fecha < found.fecha_aprobacion:
        raise ValidationError(IMPLEMENTED_BEFORE_APPROVAL)
    return _move(session, actor, found, estado=EstadoCambio.implantado,
                 fecha_implantacion=fecha)


def review_effectiveness(session: Session, actor: Actor, change_id: int, *,
                         eficacia: EficaciaCambio | str, fecha: date, today: date,
                         comentario: str | None = None) -> CambioSGC:
    """Record whether an ``implantado`` change proved effective.

    Administrators and auditors, never on ``mcp``. The reviewer is the person
    linked to the acting user, never the change's owner.

    Args:
        eficacia: ``eficaz`` or ``no_eficaz``, as the member or its name.
        fecha: When it was reviewed; not before the implementation nor after ``today``.
        comentario: Why; required (non-blank) for ``no_eficaz``, stored trimmed.

    Raises:
        PermissionDenied: ``may_review_effectiveness`` refuses the actor.
        NotFound: Unknown change.
        ValidationError: Not ``implantado``, a bad value or date, a missing
            comment, no person for the acting user (``REVIEWER_REQUIRED``) or
            the owner reviewing (``REVIEWER_IS_OWNER``).
        ValueError: ``today`` is not a ``date`` (a programming error).
    """
    found, today = _begin(session, actor, change_id, may_review_effectiveness, today)
    if found.estado is not EstadoCambio.implantado:
        raise ValidationError(NOT_IMPLEMENTED)
    result = fields.enum_member({"eficacia": eficacia}, "eficacia", EficaciaCambio)
    fecha = _given_date(fecha, "fecha_eficacia")
    comment = fields.text({"comentario_eficacia": comentario}, "comentario_eficacia")
    if result is EficaciaCambio.no_eficaz and comment is None:
        raise ValidationError(COMMENT_REQUIRED)
    if fecha < found.fecha_implantacion:
        raise ValidationError(REVIEWED_BEFORE_IMPLEMENTATION)
    if fecha > today:
        raise ValidationError(REVIEWED_IN_FUTURE)
    reviewer = people.of_user(session, actor.user_id)
    if reviewer is None:
        raise ValidationError(REVIEWER_REQUIRED)
    if reviewer == found.responsable_id:
        raise ValidationError(REVIEWER_IS_OWNER)
    return _move(session, actor, found, estado=EstadoCambio.eficacia_revisada, eficacia=result,
                 fecha_eficacia=fecha, revisor_eficacia_id=reviewer,
                 comentario_eficacia=comment)


def cancel(session: Session, actor: Actor, change_id: int, *, motivo: str | None,
           today: date) -> CambioSGC:
    """Cancel a ``propuesto`` or ``aprobado`` change on ``today``, stating why.

    Administrators, never on ``mcp``. The tasks stay as they are, and so does
    an approval.

    Raises:
        PermissionDenied: ``may_cancel`` refuses the actor.
        NotFound: Unknown change.
        ValidationError: Not open (``NOT_CANCELLABLE``) or no reason (``REASON_REQUIRED``).
        ValueError: ``today`` is not a ``date`` (a programming error).
    """
    found, today = _begin(session, actor, change_id, may_cancel, today)
    if found.estado not in OPEN_STATES:
        raise ValidationError(NOT_CANCELLABLE)
    reason = fields.text({"motivo_cancelacion": motivo}, "motivo_cancelacion")
    if reason is None:
        raise ValidationError(REASON_REQUIRED)
    return _move(session, actor, found, estado=EstadoCambio.cancelado,
                 motivo_cancelacion=reason, fecha_cancelacion=today)


def _begin(session: Session, actor: Actor, change_id: Any,
           may: Callable[[Actor], bool], today: Any) -> tuple[CambioSGC, date]:
    """The guard, then the change locked (``FOR UPDATE``) and the adapter's date."""
    if not may(actor):
        raise PermissionDenied()
    today = _today(today)
    return _locked(session, change_id), today


def _move(session: Session, actor: Actor, found: CambioSGC, **values: Any) -> CambioSGC:
    """Write a transition's columns with one audit row (before and after)."""
    before = _snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, WRITE_CONFLICT)
    return found


# -- tasks (decision CP4) --------------------------------------------------------------


def _create_task(session: Session, actor: Actor, data: Mapping[str, Any], *,
                 today: date) -> TareaCambio:
    """Add a task to a ``propuesto`` or ``aprobado`` change; it starts ``pendiente``."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(TASK_SPEC.writable, data)
    if isinstance(data, Mapping):
        fields.require_keys(data, TASK_REQUIRED)
        _require_open(_lock(session, CambioSGC, _task_change_id(data), read=True))
        _check_task(data, None, today)
    return crud.create(TASK_SPEC, session, actor, data)


def _update_task(session: Session, actor: Actor, task_id: int, data: Mapping[str, Any], *,
                 today: date) -> TareaCambio:
    """Apply the given fields; the task keeps its change, which must still be open."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(TASK_SPEC.writable, data)
    found = _task(session, task_id)
    if isinstance(data, Mapping):
        if "cambio_id" in data and _task_change_id(data) != found.cambio_id:
            raise ValidationError(TASK_CHANGE_FIXED)
        change = _lock(session, CambioSGC, found.cambio_id, read=True)
        found = _lock(session, TareaCambio, found.id, read=False)
        if _task_changed(found, data):  # a no-op is left to ``crud``, which writes nothing
            _require_open(change)
            _check_task(data, found, today)
    return crud.update(TASK_SPEC, session, actor, found.id, data)


def _delete_task(session: Session, actor: Actor, task_id: int) -> None:
    """Hard-delete a task of an open change, keeping its snapshot in the audit log."""
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _task(session, task_id)
    _require_open(_lock(session, CambioSGC, found.cambio_id, read=True))
    crud.delete(TASK_SPEC, session, actor, found.id)


def _require_open(change: CambioSGC | None) -> None:
    """The task's change exists and is ``propuesto`` or ``aprobado``."""
    if change is None:
        raise ValidationError(UNKNOWN_CHANGE)
    if change.estado not in OPEN_STATES:
        raise ValidationError(TASKS_CLOSED.format(estado=change.estado.value))


def _check_task(data: Mapping[str, Any], found: TareaCambio | None, today: date) -> None:
    """Completion rules (CP4) on the values the write leaves."""
    final = partial(_task_final, data, found)
    done = (final("estado") or EstadoTareaCambio.pendiente) is EstadoTareaCambio.completada
    completed = final("fecha_completada")
    if done and completed is None:
        raise ValidationError(COMPLETION_REQUIRED)
    if not done and completed is not None:
        raise ValidationError(COMPLETION_ONLY_WHEN_DONE)
    if "fecha_completada" in data and completed is not None and completed > today:
        raise ValidationError(COMPLETION_IN_FUTURE)


def is_overdue(task: TareaCambio, change: CambioSGC, *, today: date) -> bool:
    """Whether ``task`` of ``change`` is pending or in progress after its deadline.

    Only while ``change`` is ``propuesto`` or ``aprobado``; the ``vencida`` filter
    in Python.
    """
    return (change.estado in OPEN_STATES and task.estado in OPEN_TASK_STATES
            and task.fecha_limite < today)


def fuera_de_plazo(change: CambioSGC, *, today: date) -> bool:
    """Whether an ``aprobado`` change's planned date is before ``today`` (the filter in Python)."""
    return (change.estado is EstadoCambio.aprobado and change.fecha_prevista is not None
            and change.fecha_prevista < today)


# -- filters and the registers ---------------------------------------------------------


def _late(today: date) -> Any:
    """SQL: ``aprobado`` with its planned date before ``today``; never NULL."""
    return and_(CambioSGC.estado == EstadoCambio.aprobado, CambioSGC.fecha_prevista.is_not(None),
                CambioSGC.fecha_prevista < today)


def _late_task(today: date) -> Any:
    """SQL: an open task past its deadline in an open change; never NULL."""
    open_changes = select(CambioSGC.id).where(CambioSGC.estado.in_(OPEN_STATES))
    return and_(TareaCambio.estado.in_(OPEN_TASK_STATES), TareaCambio.fecha_limite < today,
                TareaCambio.cambio_id.in_(open_changes))


def _change_conditions(estado: Any = None, origen: Any = None,
                       responsable_id: int | None = None, proceso_id: int | None = None,
                       documento_id: int | None = None, fuera_de_plazo: bool | None = None,
                       texto: str | None = None, today: date | None = None) -> list[Any]:
    """Change filters (AND); ``fuera_de_plazo`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    if estado:
        where.append(CambioSGC.estado == _member(estado, EstadoCambio, "estado"))
    if origen:
        where.append(CambioSGC.origen == _member(origen, OrigenCambio, "origen"))
    if responsable_id is not None:
        where.append(CambioSGC.responsable_id == responsable_id
                     if people.is_db_id(responsable_id) else false())
    for value, relation, column in ((proceso_id, CambioSGC.procesos, ProcesoOperacion.id_proceso),
                                    (documento_id, CambioSGC.documentos, Document.id)):
        if value is not None:
            where.append(relation.any(column == value) if people.is_db_id(value) else false())
    if fuera_de_plazo is not None:
        late = _late(_today(today))
        where.append(late if _flag(fuera_de_plazo, "fuera_de_plazo") else not_(late))
    if texto is not None:
        if not isinstance(texto, str):
            raise ValidationError(BAD_FILTER.format(key="texto"))
        if texto.strip():
            pattern = f"%{texto.strip()}%"
            where.append(or_(CambioSGC.titulo.ilike(pattern), CambioSGC.descripcion.ilike(pattern)))
    return where


def _task_conditions(cambio_id: int | None = None, estado: Any = None,
                     responsable_id: int | None = None, vencida: bool | None = None,
                     today: date | None = None) -> list[Any]:
    """Task filters (AND); ``vencida`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    for column, value in ((TareaCambio.cambio_id, cambio_id),
                          (TareaCambio.responsable_id, responsable_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    if estado:
        where.append(TareaCambio.estado == _member(estado, EstadoTareaCambio, "estado"))
    if vencida is not None:
        late = _late_task(_today(today))
        where.append(late if _flag(vencida, "vencida") else not_(late))
    return where


changes = Register(
    SPEC=SPEC,
    conditions=_change_conditions,
    create=_create,
    update=_update,
    delete=_delete,
)

tasks = Register(
    SPEC=TASK_SPEC,
    conditions=_task_conditions,
    create=_create_task,
    update=_update_task,
    delete=_delete_task,
)


# -- readers ---------------------------------------------------------------------------


def attention(session: Session, actor: Actor, *, today: date) -> dict[str, list[Any]]:
    """What the dashboard flags on ``today`` (decision CP6); read grant.

    - ``pendientes_aprobacion``: ``propuesto`` changes, the earliest proposed first.
    - ``fuera_de_plazo``: ``aprobado`` changes planned before ``today``, the
      earliest planned first.
    - ``tareas_vencidas``: tasks pending or in progress past their deadline in a
      ``propuesto`` or ``aprobado`` change, the earliest deadline first.
    - ``eficacia_pendiente``: ``implantado`` changes awaiting the effectiveness
      review, the earliest implemented first.
    """
    policy.require(actor, Action.READ, RESOURCE)
    today = _today(today)

    def found(model: type, condition: Any, *order: Any) -> list[Any]:
        return list(session.scalars(select(model).where(condition).order_by(*order)))

    return {
        "pendientes_aprobacion": found(CambioSGC, CambioSGC.estado == EstadoCambio.propuesto,
                                       CambioSGC.fecha_propuesta, CambioSGC.id),
        "fuera_de_plazo": found(CambioSGC, _late(today), CambioSGC.fecha_prevista,
                                CambioSGC.id),
        "tareas_vencidas": found(TareaCambio, _late_task(today), TareaCambio.fecha_limite,
                                 TareaCambio.id),
        "eficacia_pendiente": found(CambioSGC, CambioSGC.estado == EstadoCambio.implantado,
                                    CambioSGC.fecha_implantacion, CambioSGC.id),
    }


@dataclass(frozen=True)
class PeriodChanges:
    """The changes a management review of a period takes as input (decision CP6).

    "In the period" means a stage's own date (``fecha_propuesta``,
    ``fecha_aprobacion``, ``fecha_implantacion``, ``fecha_eficacia``,
    ``fecha_cancelacion``) from ``desde`` to ``hasta``, both included.
    """

    propuestos: int
    aprobados: int
    implantados: int
    revisados: int  # effectiveness reviewed in the period ...
    eficaces: int  # ... and found effective
    no_eficaces: int  # ... or not
    cancelados: int
    # Approved by ``hasta``, planned before it and neither implemented nor
    # cancelled by then, the earliest planned first. The planned date is the
    # current one: an earlier value is not kept.
    fuera_de_plazo: list[CambioSGC]
    # Implemented or reviewed in the period, the earliest implemented first; each
    # carries its id, title, state, dates and result (``eficacia``).
    implantados_o_revisados: list[CambioSGC]


def in_period(session: Session, desde: date, hasta: date) -> PeriodChanges:
    """The changes proposed, approved, implemented, reviewed and cancelled in a period.

    No policy check: for the management review, which checks its own grant and
    caps the lists it stores. ``hasta`` is the cut-off at which a change counts
    as late.

    Raises:
        ValueError: ``desde`` or ``hasta`` is not a ``date``, or ``desde`` comes
            after ``hasta`` (programming errors; the review validates its period).
    """
    desde, hasta = _date(desde, "desde"), _date(hasta, "hasta")
    if desde > hasta:
        raise ValueError("desde must not be after hasta")
    change = CambioSGC

    def within(column: Any) -> Any:
        return and_(column >= desde, column <= hasta)  # NULL stays out

    def count(*where: Any) -> int:
        return session.scalar(select(func.count()).select_from(change).where(*where))

    def found(*where: Any, order: tuple) -> list[CambioSGC]:
        return list(session.scalars(select(change).where(*where).order_by(*order)))

    reviewed = within(change.fecha_eficacia)
    open_at_cut_off = and_(
        change.fecha_aprobacion <= hasta, change.fecha_prevista < hasta,
        or_(change.fecha_implantacion.is_(None), change.fecha_implantacion > hasta),
        or_(change.fecha_cancelacion.is_(None), change.fecha_cancelacion > hasta))
    return PeriodChanges(
        propuestos=count(within(change.fecha_propuesta)),
        aprobados=count(within(change.fecha_aprobacion)),
        implantados=count(within(change.fecha_implantacion)),
        revisados=count(reviewed),
        eficaces=count(reviewed, change.eficacia == EficaciaCambio.eficaz),
        no_eficaces=count(reviewed, change.eficacia == EficaciaCambio.no_eficaz),
        cancelados=count(within(change.fecha_cancelacion)),
        fuera_de_plazo=found(open_at_cut_off, order=(change.fecha_prevista, change.id)),
        implantados_o_revisados=found(or_(within(change.fecha_implantacion), reviewed),
                                      order=(change.fecha_implantacion, change.id)),
    )


def for_origin(session: Session, actor: Actor, origen: OrigenCambio | str,
               record_id: Any) -> list[CambioSGC]:
    """The changes that name ``record_id`` as the record behind them, in the register's order.

    ``origen`` (the member or its name) says which kind of record: a review
    action, a nonconformity, an audit, a risk, an opportunity or a context
    issue. Read grant of the changes.

    Raises:
        ValueError: ``origen`` is unknown or ``otro``, which names no record (a
            programming error: the origin pages pass their own).
    """
    policy.require(actor, Action.READ, RESOURCE)
    if isinstance(origen, str) and origen in OrigenCambio.__members__:
        origen = OrigenCambio[origen]
    column = _LINK_OF_ORIGIN.get(origen) if isinstance(origen, OrigenCambio) else None
    if column is None:
        raise ValueError(f"no record behind the origin {origen!r}")
    if not people.is_db_id(record_id):
        return []
    return list(session.scalars(select(CambioSGC).where(getattr(CambioSGC, column) == record_id)
                                .order_by(*SPEC.order_by)))


# -- helpers ---------------------------------------------------------------------------


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """The column values of ``data``, validated by ``SPEC`` (no database checks)."""
    return {f.name: f.clean(data, f.name) for f in SPEC.fields if f.name in data}


def _wanted(data: Mapping[str, Any]) -> dict[str, set[int]]:
    """The ids each list given in ``data`` names (``None`` names none)."""
    wanted: dict[str, set[int]] = {}
    for key, (*_, malformed, _unknown) in _LISTS.items():
        if key not in data:
            continue
        value = data[key]
        if value is None:
            value = []
        if not isinstance(value, (list, tuple)) or not all(
                isinstance(item, int) and not isinstance(item, bool) for item in value):
            raise ValidationError(malformed)
        wanted[key] = set(value)
    return wanted


def _records(session: Session, key: str, ids: set[int]) -> list[Any]:
    """The processes or documents named by ``ids``, by name or code; each must exist."""
    model, column, order, _malformed, unknown = _LISTS[key]
    searchable = [item for item in ids if people.is_db_id(item)]
    found = list(session.scalars(
        select(model).where(column.in_(searchable)).order_by(order, column)
    )) if searchable else []
    missing = sorted(ids - {getattr(record, column.key) for record in found})
    if missing:
        raise ValidationError(unknown.format(ids=", ".join(map(str, missing))))
    return found


def _ids(found: CambioSGC, key: str) -> list[int]:
    return found.proceso_ids if key == PROCESSES else found.documento_ids


def _snapshot(found: CambioSGC) -> dict[str, Any]:
    """Column values plus ``procesos`` and ``documentos``, as stored on the audit rows."""
    return audit.snapshot(found) | {PROCESSES: found.proceso_ids,
                                    DOCUMENTS: found.documento_ids}


def _final(data: Mapping[str, Any], found: Any, key: str) -> Any:
    """``key`` of a change as the write leaves it: the clean given value, else the record's."""
    if key in data:
        return data[key]
    return None if found is None else getattr(found, key)


def _task_final(data: Mapping[str, Any], found: Any, key: str) -> Any:
    """``key`` of a task as the write leaves it: the clean given value, else the record's."""
    if key in data:
        return _FIELDS[TareaCambio][key].clean(data, key)
    return None if found is None else getattr(found, key)


def _task_changed(found: TareaCambio, data: Mapping[str, Any]) -> bool:
    """Whether ``data`` changes ``found``; invalid data counts as a change."""
    try:
        return any(_task_final(data, None, key) != getattr(found, key) for key in data)
    except ValidationError:
        return True


def _task_change_id(data: Mapping[str, Any]) -> int:
    return _FIELDS[TareaCambio]["cambio_id"].clean(data, "cambio_id")


def _has_tasks(session: Session, change_id: int, condition: Any) -> bool:
    return bool(session.scalar(select(exists().where(TareaCambio.cambio_id == change_id,
                                                     condition))))


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _listed(items: list[str]) -> str:
    """The items as a Spanish list: "a, b y c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} y {items[-1]}"


def _given_date(value: Any, key: str) -> date:
    """A transition's date input, named by its column in the message."""
    return fields.required_date({key: value}, key)


def _lock(session: Session, model: type, record_id: Any, *, read: bool) -> Any:
    """The record loaded afresh with its row locked (shared when ``read``), or ``None``."""
    if not people.is_db_id(record_id):
        return None
    return session.scalars(
        select(model).where(model.id == record_id)
        .with_for_update(read=read).execution_options(populate_existing=True)
    ).one_or_none()


def _locked(session: Session, change_id: Any) -> CambioSGC:
    """The change locked ``FOR UPDATE``, or ``NotFound``."""
    found = _lock(session, CambioSGC, change_id, read=False)
    if found is None:
        raise NotFound(CHANGE_NOT_FOUND)
    return found


def _task(session: Session, task_id: Any) -> TareaCambio:
    found = session.get(TareaCambio, task_id) if people.is_db_id(task_id) else None
    if found is None:
        raise NotFound(TASK_NOT_FOUND)
    return found


def _member(value: Any, enum_cls: type, key: str) -> Any:
    """A filter value given as the member or its name."""
    try:
        return fields.enum_member({key: value}, key, enum_cls)
    except ValidationError:
        raise ValidationError(BAD_FILTER.format(key=key)) from None


def _flag(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(BAD_FLAG.format(key=key))
    return value


def _reject_unknown(writable: frozenset[str], data: Any) -> None:
    """Refuse a key outside ``writable`` before any lookup (non-mappings are refused later)."""
    if isinstance(data, Mapping):
        fields.reject_unknown(data, writable)


def _date(value: Any, name: str) -> date:
    """``value`` when it is a ``date`` (not a ``datetime``); else a programming error."""
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{name} must be a date")
    return value


def _today(today: Any) -> date:
    return _date(today, "today")  # the adapter's date, never user input


def _flush(session: Session, message: str | None = None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc
