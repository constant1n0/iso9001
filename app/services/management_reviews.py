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

"""Management reviews, their inputs and their actions (ISO 9001 clause 9.3, ``management-review``).

Decisions MR1 to MR6. Two registers share the policy resource
``MANAGEMENT_REVIEW``: administrators and auditors read, create and update,
administrators delete, other roles have no access, and the ``mcp`` channel
never deletes. Each offers ``get``, ``list_``, ``list_page``, ``create``,
``update`` and ``delete`` like ``quality_objectives``.

Reviews (``reviews``, ``RevisionDireccion``):

- ``fecha`` (the meeting), ``periodo_desde`` and ``periodo_hasta`` are
  required; the period starts on or before its end, which is on or before the
  meeting. ``asistente_ids`` replaces the attendees (people, active when newly
  chosen); the association table has no audit row of its own, so every audit
  row of a review carries ``asistente_ids``. Comments per input
  (``COMMENT_FIELDS``) and ``conclusiones`` are free text.
- ``estado``, ``datos_entrada``, ``celebrada_por_id`` and ``fecha_cierre`` are
  never written by ``create`` or ``update``: only ``hold`` and ``close`` move a
  review ``borrador`` -> ``celebrada`` -> ``cerrada`` (MR4), with no reopening.
- A draft is editable; a held review takes only comments and conclusions; a
  closed one nothing. An edit that changes nothing writes nothing.
- Only a draft without actions is deleted (MR6); one whose actions a change to
  the QMS cites says so (``ACTIONS_CITED_BY_CHANGES``).

``hold`` and ``close`` are for administrators and never on the ``mcp``
channel. Holding needs a meeting date not after ``today`` and an attendee; it
freezes ``inputs`` into ``datos_entrada`` and records the actor's person, if
any, as ``celebrada_por_id``. Closing needs the conclusions and records
``fecha_cierre``. ``preview`` gives the frozen inputs once held, live ones in a
draft.

Actions (``actions``, ``AccionRevision``, MR3): ``tipo`` and ``estado`` are
member names; ``responsable_id`` is an existing person, active when newly
chosen; ``fecha_completada`` is given exactly when the action is
``completada``, never after ``today`` nor before the review's date. Actions
are added, edited and deleted while their review is a draft or held; once it
is closed only ``estado``, ``fecha_completada`` and ``resultado`` change and
none is deleted. An action a change to the QMS cites is never deleted
(``ACTION_CITED_BY_CHANGES``, CP1 of ``change-planning``). An action never moves
to another review. It is overdue
(``is_overdue``, the ``vencida`` filter, ``overdue_actions``, ``action_counts``)
when pending or in progress after its deadline.

Every date that judges something (``today``) comes from the adapter: services
never read the clock. Like every service this module flushes and never
commits, writes an audit row next to each change, and locks a review row
before it changes (``FOR UPDATE``) or before its actions do (``FOR SHARE``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from typing import Any

from sqlalchemy import and_, case, exists, false, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AccionCorrectiva, AccionRevision, AlcanceSGC, Auditoria, AuditoriaIndicador, CambioSGC,
    Capacitacion, EstadoAccionRevision, EstadoAlcance, EstadoAuditoriaEnum, EstadoNoConformidad,
    EstadoObjetivo, EstadoProveedor, EstadoRevisionDireccion, EvaluacionProveedor,
    GravedadNoConformidad, HallazgoAuditoria, MedicionObjetivo, Mejora, NivelRiesgo,
    NoConformidad, ObjetivoCalidad, Oportunidad, OrigenNoConformidad, ParteInteresada, Person,
    Proveedor, ResultadoEvaluacion, ResultadoVerificacion, RevisionDireccion, Riesgo, RoleEnum,
    SatisfaccionCliente, TipoAccionRevision, TipoHallazgo,
)
from . import (
    audit, context_issues, crud, fields, people, policy, providers, qms_changes,
    quality_objectives, risks,
)
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.MANAGEMENT_REVIEW
COMMENT_FIELDS = (
    "comentario_acciones_previas", "comentario_contexto", "comentario_desempeno",
    "comentario_recursos", "comentario_riesgos", "comentario_mejora",
)
HELD_WRITABLE = frozenset(COMMENT_FIELDS) | {"conclusiones"}  # what a held review takes
REVIEW_WRITABLE = HELD_WRITABLE | {"fecha", "periodo_desde", "periodo_hasta", "asistente_ids"}
REVIEW_REQUIRED = frozenset({"fecha", "periodo_desde", "periodo_hasta"})
FOLLOW_UP = frozenset({"estado", "fecha_completada", "resultado"})  # once the review is closed
OPEN_STATES = (EstadoAccionRevision.pendiente, EstadoAccionRevision.en_curso)
TRANSITION_ROLES = frozenset({RoleEnum.ADMINISTRADOR})
YEAR_MIN, YEAR_MAX = 1, 9999  # ``date`` years
LIST_LIMIT = 10  # items in each list of ``inputs``
SHORT_TEXT = 120  # characters of a text in ``inputs``

REVIEW_NOT_FOUND = "Revisión por la dirección no encontrada."
ACTION_NOT_FOUND = "Acción de la revisión no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
PERIOD_ORDER = "El inicio del periodo revisado no puede ser posterior a su fin."
PERIOD_AFTER_MEETING = "El periodo revisado debe terminar en la fecha de la reunión o antes."
ATTENDEE_IDS = "El campo «asistente_ids» debe ser una lista de identificadores de persona."
ONLY_COMMENTS = "Una revisión celebrada solo admite cambios en los comentarios y las conclusiones."
REVIEW_CLOSED = "Una revisión cerrada no se puede modificar."
DELETE_NOT_DRAFT = "Solo se puede eliminar una revisión en borrador."
HAS_ACTIONS = "No se puede eliminar una revisión que tiene acciones; elimina antes sus acciones."
ACTIONS_CITED_BY_CHANGES = (
    "No se puede eliminar una revisión con acciones a las que se refieren cambios en el SGC."
)
ACTION_CITED_BY_CHANGES = (
    "No se puede eliminar una acción de la revisión a la que se refieren cambios en el SGC."
)
DELETE_CONFLICT = (
    "No se ha podido eliminar la revisión porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo."
)
NOT_DRAFT = "Solo se puede celebrar una revisión en borrador."
MEETING_IN_FUTURE = "No se puede celebrar una revisión cuya fecha es futura."
NO_ATTENDEES = "Indica al menos un asistente antes de celebrar la revisión."
NOT_HELD = "Solo se puede cerrar una revisión celebrada."
CONCLUSIONS_REQUIRED = "Escribe las conclusiones antes de cerrar la revisión."
TRANSITION_CONFLICT = (
    "No se ha podido cambiar el estado de la revisión porque sus datos han cambiado "
    "entretanto; vuelve a intentarlo."
)
BAD_STATE = "El estado indicado no es válido."
BAD_TYPE = "El tipo de acción indicado no es válido."
OVERDUE_FLAG = "El filtro «vencida» debe ser verdadero o falso."
UNKNOWN_REVIEW = "El campo «revision_id» no corresponde a ninguna revisión por la dirección."
REVIEW_FIXED = "Una acción no puede cambiar de revisión."
REVIEW_CLOSED_FOR_ACTIONS = "La revisión está cerrada: no se pueden añadir ni eliminar acciones."
FOLLOW_UP_ONLY = (
    "La revisión está cerrada: de sus acciones solo cambian el estado, la fecha de "
    "finalización y el resultado."
)
COMPLETION_REQUIRED = "Indica la fecha de finalización de una acción completada."
COMPLETION_ONLY_WHEN_DONE = "Solo una acción completada tiene fecha de finalización."
COMPLETION_IN_FUTURE = "La fecha de finalización no puede ser futura."
COMPLETION_BEFORE_REVIEW = "La fecha de finalización no puede ser anterior a la de la revisión."
ACTIONS_COMPLETED_BEFORE = ("La revisión tiene acciones finalizadas antes de esa fecha; "
                            "su fecha no puede ser posterior a la de esas acciones.")


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


_REVIEW_READS = crud.Spec(
    model=RevisionDireccion,
    resource=RESOURCE,
    not_found=REVIEW_NOT_FOUND,
    fields=(),  # writes are bespoke (see ``REVIEW_WRITABLE``)
    order_by=(RevisionDireccion.fecha.desc(), RevisionDireccion.id.desc()),
)

ACTION_SPEC = crud.Spec(
    model=AccionRevision,
    resource=RESOURCE,
    not_found=ACTION_NOT_FOUND,
    fields=(
        crud.Field("revision_id", partial(fields.integer, required=True), required=True),
        crud.Field("tipo", partial(fields.enum_member, enum_cls=TipoAccionRevision),
                   required=True),
        crud.Field("descripcion", partial(fields.text, required=True), required=True),
        crud.Field("responsable_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        crud.Field("fecha_limite", fields.required_date, required=True),
        crud.Field("estado", partial(fields.enum_member, enum_cls=EstadoAccionRevision)),
        crud.Field("fecha_completada", _optional_date),
        crud.Field("resultado", partial(fields.text, strip=False)),
    ),
    # The soonest deadline first.
    order_by=(AccionRevision.fecha_limite, AccionRevision.id),
)

_ACTION_FIELDS = {f.name: f for f in ACTION_SPEC.fields}


def _actions_foreign_key() -> str:
    """Name of the actions' foreign key to reviews, as the model declares it."""
    (name,) = {fk.constraint.name for fk in AccionRevision.__table__.foreign_keys
               if fk.column.table is RevisionDireccion.__table__}
    return name


ACTIONS_FK = _actions_foreign_key()


# -- reviews ---------------------------------------------------------------------------


def _create_review(session: Session, actor: Actor, data: Mapping[str, Any]) -> RevisionDireccion:
    """Create a draft review; the meeting date and the period are required."""
    policy.require(actor, Action.CREATE, RESOURCE)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.require_keys(data, REVIEW_REQUIRED)
    values, attendees = _clean_review(session, data)
    _check_period(values, None)
    created = RevisionDireccion(estado=EstadoRevisionDireccion.borrador,
                                asistentes=attendees or [], **values)
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict() from exc
    row.after = _snapshot(created)  # the row is still pending: add the attendees
    return created


def _update_review(session: Session, actor: Actor, review_id: int,
                   data: Mapping[str, Any]) -> RevisionDireccion:
    """Apply the given fields as the review's state allows; ``asistente_ids`` replaces them."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    found = _lock_review(session, review_id)
    values, attendees = _clean_review(session, data, found)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if attendees is not None and {person.id for person in attendees} == set(found.asistente_ids):
        attendees = None
    if not changes and attendees is None:
        return found
    touched = set(changes) | ({"asistente_ids"} if attendees is not None else set())
    if found.estado is EstadoRevisionDireccion.cerrada:
        raise ValidationError(REVIEW_CLOSED)
    if found.estado is EstadoRevisionDireccion.celebrada and touched - HELD_WRITABLE:
        raise ValidationError(ONLY_COMMENTS)
    _check_period(changes, found)
    if "fecha" in changes:
        _check_completed_actions(session, found.id, changes["fecha"])
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    if attendees is not None:
        found.asistentes = attendees
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, None)
    return found


def _check_completed_actions(session: Session, review_id: int, fecha: date) -> None:
    """Keep every completion date on or after the review's date (MR3) when the date moves."""
    if session.scalar(select(exists().where(
            AccionRevision.revision_id == review_id,
            AccionRevision.fecha_completada < fecha))):
        raise ValidationError(ACTIONS_COMPLETED_BEFORE)


def _delete_review(session: Session, actor: Actor, review_id: int) -> None:
    """Hard-delete a draft without actions and its attendees, keeping an audit snapshot.

    An action added after the check meets the foreign key at the flush.
    """
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _lock_review(session, review_id)
    if found.estado is not EstadoRevisionDireccion.borrador:
        raise ValidationError(DELETE_NOT_DRAFT)
    if _actions_cited_by_changes(session, found.id):
        raise Conflict(ACTIONS_CITED_BY_CHANGES)
    if _has_actions(session, found.id):
        raise Conflict(HAS_ACTIONS)
    audit.record(session, actor, "delete", found, before=_snapshot(found))
    session.delete(found)
    try:
        session.flush()
    except IntegrityError as exc:
        mine = _violated_constraint(exc) == ACTIONS_FK
        raise Conflict(HAS_ACTIONS if mine else DELETE_CONFLICT) from exc


def _clean_review(session: Session, data: Any, current: RevisionDireccion | None = None
                  ) -> tuple[dict[str, Any], list[Person] | None]:
    """Validated column values and the attendees (``None`` when ``asistente_ids`` is absent)."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, REVIEW_WRITABLE)
    values: dict[str, Any] = {}
    for key in ("fecha", "periodo_desde", "periodo_hasta"):
        if key in data:
            values[key] = fields.required_date(data, key)
    for key in (*COMMENT_FIELDS, "conclusiones"):
        if key in data:
            values[key] = fields.text(data, key, strip=False)
    attendees = None
    if "asistente_ids" in data:
        attendees = _attendees(session, data["asistente_ids"],
                               set(current.asistente_ids) if current is not None else set())
    return values, attendees


def _attendees(session: Session, value: Any, current: set[int]) -> list[Person]:
    """The people named by ``value``: known ids (duplicates ignored), active when new."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise ValidationError(ATTENDEE_IDS)
    wanted = set(value)
    searchable = [item for item in wanted if people.is_db_id(item)]
    found = list(session.scalars(
        select(Person).where(Person.id.in_(searchable)).order_by(Person.nombre, Person.id)
    )) if searchable else []
    missing = sorted(wanted - {person.id for person in found})
    if missing:
        raise ValidationError(
            f"Personas desconocidas en «asistente_ids»: {', '.join(map(str, missing))}.")
    inactive = [person.nombre for person in found
                if not person.activo and person.id not in current]
    if inactive:
        raise ValidationError(
            f"Personas desactivadas en «asistente_ids»: {', '.join(inactive)}; "
            "elige personas activas.")
    return found


def _check_period(values: Mapping[str, Any], found: RevisionDireccion | None) -> None:
    """The period starts by its end, which is by the meeting, as the write leaves them."""

    def final(key: str) -> Any:
        return values[key] if key in values else getattr(found, key, None)

    desde, hasta, fecha = final("periodo_desde"), final("periodo_hasta"), final("fecha")
    if desde > hasta:
        raise ValidationError(PERIOD_ORDER)
    if hasta > fecha:
        raise ValidationError(PERIOD_AFTER_MEETING)


def _review_conditions(estado: EstadoRevisionDireccion | str | None = None,
                       anio: int | None = None) -> list[Any]:
    """Review filters (AND): the state (member or name) and the year of the meeting."""
    where: list[Any] = []
    if estado:
        where.append(RevisionDireccion.estado == _member(estado, EstadoRevisionDireccion,
                                                         BAD_STATE))
    if anio is not None:
        if isinstance(anio, int) and not isinstance(anio, bool) and YEAR_MIN <= anio <= YEAR_MAX:
            where.append(RevisionDireccion.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
        else:
            where.append(false())
    return where


def _snapshot(review: RevisionDireccion) -> dict[str, Any]:
    """Column values plus ``asistente_ids``, as stored on the review's audit rows."""
    return audit.snapshot(review) | {"asistente_ids": review.asistente_ids}


# -- holding and closing (MR4) ---------------------------------------------------------


def hold(session: Session, actor: Actor, review_id: int, *, today: date) -> RevisionDireccion:
    """Hold a draft review, freezing its inputs; administrators only, never through ``mcp``.

    Args:
        today: The adapter's local date; the meeting must not be after it.

    Raises:
        PermissionDenied: The actor may not hold reviews.
        NotFound: Unknown review.
        ValidationError: Not a draft, a meeting after ``today`` or no attendee.
        ValueError: ``today`` is not a ``date`` (a programming error).
        Conflict: The database refused the change.
    """
    _require_transition(actor)
    today = _today(today)
    found = _lock_review(session, review_id)
    if found.estado is not EstadoRevisionDireccion.borrador:
        raise ValidationError(NOT_DRAFT)
    if found.fecha > today:
        raise ValidationError(MEETING_IN_FUTURE)
    if not found.asistentes:
        raise ValidationError(NO_ATTENDEES)
    frozen = _inputs(session, found.periodo_desde, found.periodo_hasta, today=today,
                     review=found)
    _transition(session, actor, found, estado=EstadoRevisionDireccion.celebrada,
                datos_entrada=frozen, celebrada_por_id=people.of_user(session, actor.user_id))
    return found


def close(session: Session, actor: Actor, review_id: int, *, today: date) -> RevisionDireccion:
    """Close a held review with conclusions on ``today``; administrators only, never ``mcp``.

    Raises ``PermissionDenied``, ``NotFound``, ``ValidationError`` (not held, or
    no conclusions), ``ValueError`` (``today`` not a ``date``) or ``Conflict``.
    """
    _require_transition(actor)
    today = _today(today)
    found = _lock_review(session, review_id)
    if found.estado is not EstadoRevisionDireccion.celebrada:
        raise ValidationError(NOT_HELD)
    if not (found.conclusiones or "").strip():
        raise ValidationError(CONCLUSIONS_REQUIRED)
    _transition(session, actor, found, estado=EstadoRevisionDireccion.cerrada,
                fecha_cierre=today)
    return found


def _require_transition(actor: Actor) -> None:
    policy.require(actor, Action.UPDATE, RESOURCE)
    if actor.role not in TRANSITION_ROLES or actor.channel == "mcp":
        raise PermissionDenied()


def _transition(session: Session, actor: Actor, found: RevisionDireccion,
                **changes: Any) -> None:
    """Apply ``changes`` with stamps and an audit row."""
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, TRANSITION_CONFLICT)


# -- actions (MR3) ---------------------------------------------------------------------


def _create_action(session: Session, actor: Actor, data: Mapping[str, Any], *,
                   today: date) -> AccionRevision:
    """Add an action to a review that is not closed; it starts ``pendiente``."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(data)
    if _well_formed(data) and "revision_id" in data:
        review = _actions_review(session,
                                 _ACTION_FIELDS["revision_id"].clean(data, "revision_id"))
        if review.estado is EstadoRevisionDireccion.cerrada:
            raise ValidationError(REVIEW_CLOSED_FOR_ACTIONS)
        _check_completion(review, *_completion(data, None), today=today)
    return crud.create(ACTION_SPEC, session, actor, data)


def _update_action(session: Session, actor: Actor, action_id: int, data: Mapping[str, Any], *,
                   today: date) -> AccionRevision:
    """Apply the given fields; once the review is closed, only the follow-up ones."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(data)
    found = session.get(AccionRevision, action_id) if people.is_db_id(action_id) else None
    if found is None:
        raise NotFound(ACTION_NOT_FOUND)
    if _well_formed(data):
        if ("revision_id" in data and _ACTION_FIELDS["revision_id"].clean(
                data, "revision_id") != found.revision_id):
            raise ValidationError(REVIEW_FIXED)
        changed = _changed(found, data)
        if changed:  # a no-op is left to ``crud``, which writes nothing
            review = _actions_review(session, found.revision_id)
            if review.estado is EstadoRevisionDireccion.cerrada and changed - FOLLOW_UP:
                raise ValidationError(FOLLOW_UP_ONLY)
            if changed & {"estado", "fecha_completada"}:
                _check_completion(review, *_completion(data, found), today=today)
    return crud.update(ACTION_SPEC, session, actor, found.id, data)


def _delete_action(session: Session, actor: Actor, action_id: int) -> None:
    """Hard-delete an action of a review that is not closed, keeping an audit snapshot.

    One a change to the QMS cites is refused; a citation added after the check
    meets the foreign key at the flush.
    """
    policy.require(actor, Action.DELETE, RESOURCE)
    found = crud.get(ACTION_SPEC, session, actor, action_id)
    if _actions_review(session, found.revision_id).estado is EstadoRevisionDireccion.cerrada:
        raise ValidationError(REVIEW_CLOSED_FOR_ACTIONS)
    if _cited_by_changes(session, found.id):
        raise Conflict(ACTION_CITED_BY_CHANGES)
    crud.delete(ACTION_SPEC, session, actor, found.id)


def _completion(data: Mapping[str, Any], found: AccionRevision | None
                ) -> tuple[EstadoAccionRevision, date | None]:
    """The state and completion date the write leaves (a new action starts pending)."""
    estado = (_ACTION_FIELDS["estado"].clean(data, "estado") if "estado" in data
              else found.estado if found is not None else EstadoAccionRevision.pendiente)
    fecha = (_optional_date(data, "fecha_completada") if "fecha_completada" in data
             else found.fecha_completada if found is not None else None)
    return estado, fecha


def _check_completion(review: RevisionDireccion, estado: EstadoAccionRevision,
                      fecha: date | None, *, today: date) -> None:
    """A completed action has a completion date between its review's date and ``today``."""
    done = estado is EstadoAccionRevision.completada
    if done and fecha is None:
        raise ValidationError(COMPLETION_REQUIRED)
    if not done and fecha is not None:
        raise ValidationError(COMPLETION_ONLY_WHEN_DONE)
    if fecha is not None and fecha > today:
        raise ValidationError(COMPLETION_IN_FUTURE)
    if fecha is not None and fecha < review.fecha:
        raise ValidationError(COMPLETION_BEFORE_REVIEW)


def _changed(found: AccionRevision, data: Mapping[str, Any]) -> set[str]:
    """Keys of ``data`` that change the action; an invalid value counts as a change."""
    changed = set()
    for key in data:
        try:
            if _ACTION_FIELDS[key].clean(data, key) == getattr(found, key):
                continue
        except ValidationError:
            pass
        changed.add(key)
    return changed


def _actions_review(session: Session, review_id: Any) -> RevisionDireccion:
    """The action's review, read with a shared lock (``FOR SHARE``) so its state holds."""
    found = _locked(session, review_id, read=True)
    if found is None:
        raise ValidationError(UNKNOWN_REVIEW)
    return found


def _overdue(today: date) -> Any:
    return and_(AccionRevision.estado.in_(OPEN_STATES), AccionRevision.fecha_limite < today)


def _action_conditions(revision_id: int | None = None,
                       estado: EstadoAccionRevision | str | None = None,
                       responsable_id: int | None = None,
                       tipo: TipoAccionRevision | str | None = None,
                       vencida: bool | None = None, today: date | None = None) -> list[Any]:
    """Action filters (AND); ``vencida`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    for column, value in ((AccionRevision.revision_id, revision_id),
                          (AccionRevision.responsable_id, responsable_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    if estado:
        where.append(AccionRevision.estado == _member(estado, EstadoAccionRevision, BAD_STATE))
    if tipo:
        where.append(AccionRevision.tipo == _member(tipo, TipoAccionRevision, BAD_TYPE))
    if vencida is not None:
        if not isinstance(vencida, bool):
            raise ValidationError(OVERDUE_FLAG)
        overdue = _overdue(_today(today))
        where.append(overdue if vencida else not_(overdue))
    return where


def is_overdue(action: AccionRevision, *, today: date) -> bool:
    """Whether ``action`` is pending or in progress after its deadline (MR3)."""
    return action.estado in OPEN_STATES and action.fecha_limite < today


def overdue_actions(session: Session, actor: Actor, *, today: date) -> list[AccionRevision]:
    """Overdue actions for the dashboard, soonest deadline first; read grant."""
    policy.require(actor, Action.READ, RESOURCE)
    return list(session.scalars(select(AccionRevision).where(_overdue(_today(today)))
                                .order_by(*ACTION_SPEC.order_by)))


def action_counts(session: Session, actor: Actor, review_ids: Any, *,
                  today: date) -> dict[int, dict[str, int]]:
    """``total``, ``abiertas`` (pending or in progress) and ``vencidas`` actions per review.

    One grouped query for the list screen; every id given is a key, with zeros
    when the review has no actions. Read grant; ``today`` is the adapter's date.
    """
    policy.require(actor, Action.READ, RESOURCE)
    overdue = _overdue(_today(today))
    wanted = {value for value in review_ids if people.is_db_id(value)}
    counts = {review_id: {"total": 0, "abiertas": 0, "vencidas": 0} for review_id in wanted}
    if wanted:
        rows = session.execute(select(
            AccionRevision.revision_id, func.count(),
            func.sum(case((AccionRevision.estado.in_(OPEN_STATES), 1), else_=0)),
            func.sum(case((overdue, 1), else_=0)),
        ).where(AccionRevision.revision_id.in_(wanted)).group_by(AccionRevision.revision_id))
        for review_id, total, open_, late in rows:
            counts[review_id] = {"total": total, "abiertas": int(open_), "vencidas": int(late)}
    return counts


reviews = Register(
    SPEC=_REVIEW_READS,
    conditions=_review_conditions,
    create=_create_review,
    update=_update_review,
    delete=_delete_review,
)

actions = Register(
    SPEC=ACTION_SPEC,
    conditions=_action_conditions,
    create=_create_action,
    update=_update_action,
    delete=_delete_action,
)


# -- inputs (MR2, ISO 9001 clause 9.3.2) -----------------------------------------------


def inputs(session: Session, actor: Actor, desde: date, hasta: date, *, today: date,
           review: RevisionDireccion | None = None) -> dict[str, Any]:
    """The inputs of a review of ``desde``..``hasta`` drawn from the registers (JSON-safe).

    Dates are ISO text, numbers of the objectives decimal text, enum keys
    member names, and every list holds at most ``LIST_LIMIT`` items with texts
    cut to ``SHORT_TEXT`` characters. "In the period" means a date from
    ``desde`` to ``hasta``, both included; timestamps (``created_at``,
    ``updated_at``) count by UTC day. ``fecha_corte`` is ``hasta``, or
    ``today`` when earlier: what stands "at the end of the period" is judged
    on it. Sections:

    - ``acciones_previas`` (9.3.2 a): actions of the reviews dated before
      ``review`` (without one, dated by ``hasta``). ``abiertas`` were open on
      the cut-off: pending or in progress now (``por_estado``), or completed
      after it (``completadas_despues``); ``vencidas`` of those were past their
      deadline then; ``completadas_en_periodo`` were completed in the period.
      ``lista`` holds the open ones, soonest deadline first.
    - ``contexto`` (9.3.2 b): interested parties, risks and opportunities
      created (``nuevas``/``nuevos``) or else updated (``modificadas``/
      ``modificados``) in the period, listed in ``lista``; ``total`` counts
      every current record, and so do ``por_nivel`` (risks, decision RO7) and
      ``por_prioridad`` (opportunities), with ``sin_valorar`` for the records
      not assessed yet. ``altos_abiertos`` counts the open risks of level
      ``alto`` or ``critico``, listed with their owners in
      ``altos_abiertos_lista``, the highest first. Reviews held before RO-2
      froze a single ``riesgos_oportunidades`` count by type instead.
      ``cuestiones`` (OC5 of ``organization-context``, ``context_issues.changes``)
      counts the context issues created (``nuevas``) or else updated
      (``modificadas``) in the period, listed in ``lista`` with ``cambio``
      (``nueva`` or ``modificada``), and those superseded in it (``superadas``);
      ``vigentes``, ``pertinentes``, ``cambio_climatico`` and
      ``cambio_climatico_pertinentes`` count the issues in force now. ``alcance``
      gives the ``version`` of the QMS scope in force now and its
      ``fecha_aprobacion`` (both ``None`` without one). Reviews held before OC-4
      froze neither.
    - ``satisfaccion`` (9.3.2 c 1): surveys in the period and their average
      score (``None`` without surveys).
    - ``objetivos`` (9.3.2 c 2): objectives whose period overlaps the review's,
      except cancelled ones, judged on their measurements up to the cut-off
      (``quality_objectives.summary``): ``en_meta``, ``fuera_de_meta`` and
      ``sin_medicion``, ``medicion_vencida``, ``por_estado`` (current state)
      and ``fuera_de_meta_lista``.
    - ``indicadores`` (9.3.2 c 3): process indicator records dated in the period.
    - ``no_conformidades`` (9.3.2 c 4): nonconformities detected in the period
      by severity and origin (``sin_indicar`` when unset), those closed
      (``cerrada``) in the period, and corrective actions whose effectiveness
      was verified in the period, by result.
    - ``auditorias`` (9.3.2 c 6): audits dated in the period, those completed,
      and the findings of the audits dated in the period by type.
    - ``mejoras`` (9.3.2 f): improvements implemented in the period.
    - ``capacitaciones`` (9.3.2 d): training sessions in the period, their
      hours and topics.
    - ``proveedores`` (9.3.2 c, 9.1.3 f, task EP-3 of ``external-providers``):
      ``por_estado`` counts every provider by its current state (no history is
      kept); ``evaluaciones_vencidas`` the providers not blocked whose current
      next evaluation date is before the cut-off. ``evaluaciones`` counts the
      evaluations dated in the period, their mean score (``providers.average_of``,
      decimal text, ``None`` without any) and their results. ``no_aptos``
      counts the providers with a ``no_apto`` evaluation in the period, listed
      by name in ``no_aptos_lista`` with the latest such evaluation and their
      current state. ``no_conformidades`` counts the nonconformities with the
      ``proveedor`` origin detected in the period, those naming no provider
      (``sin_proveedor``) and, in ``por_proveedor``, the most cited providers
      first, then by name. Reviews held before EP-3 froze no such section.
    - ``cambios`` (9.3.2 c, decision CP6 of ``change-planning``,
      ``qms_changes.in_period``): the changes to the QMS proposed
      (``propuestos``), approved (``aprobados``), implemented (``implantados``),
      reviewed for effectiveness (``revisados``, of them ``eficaces`` and
      ``no_eficaces``) and cancelled (``cancelados``) in the period, each counted
      by the date of that stage. ``fuera_de_plazo`` counts the changes approved
      and past their planned date at the cut-off, neither implemented nor
      cancelled by then, listed in ``fuera_de_plazo_lista`` with their owner, the
      most overdue first. ``implantados_o_revisados`` counts the changes
      implemented or reviewed in the period, listed in
      ``implantados_o_revisados_lista`` with their current state, dates and
      result (``eficacia``), the latest implemented or reviewed first. Each count
      next to its list tells when the list was cut. Reviews held before CP-4
      froze no such section.

    Read grant of ``MANAGEMENT_REVIEW``; ``today`` is the adapter's date.
    """
    policy.require(actor, Action.READ, RESOURCE)
    today = _today(today)
    desde = fields.required_date({"periodo_desde": desde}, "periodo_desde")
    hasta = fields.required_date({"periodo_hasta": hasta}, "periodo_hasta")
    if desde > hasta:
        raise ValidationError(PERIOD_ORDER)
    return _inputs(session, desde, hasta, today=today, review=review)


def preview_of(session: Session, review: RevisionDireccion, *, today: date) -> dict[str, Any]:
    """The frozen inputs of a held review, or a draft's live ``inputs``; no policy check.

    For adapters that already read the review (the MCP ``extras`` of a review).
    """
    if review.estado is not EstadoRevisionDireccion.borrador:
        return review.datos_entrada
    return _inputs(session, review.periodo_desde, review.periodo_hasta, today=_today(today),
                   review=review)


def preview(session: Session, actor: Actor, review: RevisionDireccion, *,
            today: date) -> dict[str, Any]:
    """``preview_of`` with the read grant of ``MANAGEMENT_REVIEW``."""
    policy.require(actor, Action.READ, RESOURCE)
    return preview_of(session, review, today=today)


def _inputs(session: Session, desde: date, hasta: date, *, today: date,
            review: RevisionDireccion | None) -> dict[str, Any]:
    cut_off = min(hasta, today)
    return audit.json_safe({
        "periodo": {"desde": desde, "hasta": hasta},
        "fecha_corte": cut_off,
        "acciones_previas": _earlier_actions(session, desde, hasta, cut_off, review),
        "contexto": {
            "cuestiones": _context_issues(session, desde, hasta),
            "partes_interesadas": _changed_parties(session, desde, hasta),
            "alcance": _scope_in_force(session),
            "riesgos": _risks(session, desde, hasta),
            "oportunidades": _opportunities(session, desde, hasta),
        },
        "satisfaccion": _satisfaction(session, desde, hasta),
        "objetivos": _objectives(session, desde, hasta, cut_off),
        "indicadores": _indicators(session, desde, hasta),
        "no_conformidades": _nonconformities(session, desde, hasta),
        "auditorias": _audits(session, desde, hasta),
        "mejoras": _improvements(session, desde, hasta),
        "capacitaciones": _training(session, desde, hasta),
        "proveedores": _providers(session, desde, hasta, cut_off),
        "cambios": _qms_changes(session, desde, hasta, cut_off),
    })


def _earlier_actions(session: Session, desde: date, hasta: date, cut_off: date,
                     review: RevisionDireccion | None) -> dict[str, Any]:
    earlier = [RevisionDireccion.fecha < (hasta + timedelta(days=1) if review is None
                                          else review.fecha)]
    if review is not None and review.id is not None:
        earlier.append(RevisionDireccion.id != review.id)
    rows = session.execute(
        select(AccionRevision, RevisionDireccion.fecha, Person.nombre)
        .join(RevisionDireccion, AccionRevision.revision_id == RevisionDireccion.id)
        .join(Person, AccionRevision.responsable_id == Person.id)
        .where(*earlier).order_by(*ACTION_SPEC.order_by)).all()
    done = EstadoAccionRevision.completada
    open_ = [row for row in rows if row[0].estado in OPEN_STATES
             or (row[0].estado is done and row[0].fecha_completada > cut_off)]
    return {
        "abiertas": len(open_),
        "por_estado": {state.name: sum(row[0].estado is state for row in open_)
                       for state in OPEN_STATES},
        "completadas_despues": sum(row[0].estado is done for row in open_),
        "vencidas": sum(row[0].fecha_limite < cut_off for row in open_),
        "completadas_en_periodo": sum(row[0].estado is done
                                      and desde <= row[0].fecha_completada <= hasta
                                      for row in rows),
        "lista": [{
            "id": action.id, "revision_id": action.revision_id, "revision_fecha": meeting,
            "tipo": action.tipo.name, "descripcion": _short(action.descripcion),
            "responsable": owner, "fecha_limite": action.fecha_limite,
            "estado": action.estado.name, "vencida": action.fecha_limite < cut_off,
        } for action, meeting, owner in open_[:LIST_LIMIT]],
    }


def _changes(model: Any, desde: date, hasta: date) -> tuple[Any, Any]:
    """Conditions for the rows of ``model`` created, or else updated, in the period."""
    start, end = _moments(desde, hasta, aware=True)
    created = and_(model.created_at >= start, model.created_at < end)
    updated = and_(model.updated_at >= start, model.updated_at < end,
                   or_(model.created_at.is_(None), model.created_at < start))
    return created, updated


def _changed_parties(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    created, updated = _changes(ParteInteresada, desde, hasta)
    return {
        "total": _count(session, ParteInteresada),
        "nuevas": _count(session, ParteInteresada, created),
        "modificadas": _count(session, ParteInteresada, updated),
        "lista": list(session.scalars(
            select(ParteInteresada.nombre).where(or_(created, updated))
            .order_by(ParteInteresada.nombre).limit(LIST_LIMIT))),
    }


def _context_issues(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    found = context_issues.changes(session, desde, hasta)
    listed = [*((issue, "nueva") for issue in found.nuevas),
              *((issue, "modificada") for issue in found.modificadas)]
    return {
        "nuevas": len(found.nuevas),
        "modificadas": len(found.modificadas),
        "superadas": len(found.superadas),
        "vigentes": found.vigentes,
        "pertinentes": found.pertinentes,
        "cambio_climatico": found.cambio_climatico,
        "cambio_climatico_pertinentes": found.cambio_climatico_pertinentes,
        "lista": [{"id": issue.id, "titulo": _short(issue.titulo), "tipo": issue.tipo.name,
                   "categoria": issue.categoria.name, "pertinente": issue.pertinente,
                   "cambio_climatico": issue.cambio_climatico, "estado": issue.estado.name,
                   "cambio": change} for issue, change in listed[:LIST_LIMIT]],
    }


def _scope_in_force(session: Session) -> dict[str, Any]:
    found = session.scalar(select(AlcanceSGC).where(AlcanceSGC.estado == EstadoAlcance.vigente))
    return {"version": None if found is None else found.version,
            "fecha_aprobacion": None if found is None else found.fecha_aprobacion}


def _risks(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    created, updated = _changes(Riesgo, desde, hasta)
    product = Riesgo.probabilidad * Riesgo.impacto
    high = and_(Riesgo.estado.not_in(risks.CLOSED_RISK_STATES),
                product >= risks.LEVEL_RANGES[NivelRiesgo.alto][0])
    listed = session.scalars(select(Riesgo).where(or_(created, updated))
                             .order_by(Riesgo.id).limit(LIST_LIMIT))
    highest = session.execute(
        select(Riesgo, Person.nombre).outerjoin(Person, Riesgo.responsable_id == Person.id)
        .where(high).order_by(product.desc(), Riesgo.id).limit(LIST_LIMIT))
    return {
        "total": _count(session, Riesgo),
        "por_nivel": _count_by_band(session, product, risks.LEVEL_RANGES),
        "nuevos": _count(session, Riesgo, created),
        "modificados": _count(session, Riesgo, updated),
        "lista": [_risk_item(risk) for risk in listed],
        "altos_abiertos": _count(session, Riesgo, high),
        "altos_abiertos_lista": [_risk_item(risk) | {"responsable": owner}
                                 for risk, owner in highest],
    }


def _risk_item(risk: Riesgo) -> dict[str, Any]:
    return {"id": risk.id, "titulo": _short(risk.titulo),
            "nivel": risk.nivel.name if risk.nivel else "sin_valorar", "estado": risk.estado.name}


def _opportunities(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    created, updated = _changes(Oportunidad, desde, hasta)
    listed = session.scalars(select(Oportunidad).where(or_(created, updated))
                             .order_by(Oportunidad.id).limit(LIST_LIMIT))
    return {
        "total": _count(session, Oportunidad),
        "por_prioridad": _count_by_band(session, Oportunidad.beneficio * Oportunidad.viabilidad,
                                        risks.PRIORITY_RANGES),
        "nuevas": _count(session, Oportunidad, created),
        "modificadas": _count(session, Oportunidad, updated),
        "lista": [{"id": found.id, "titulo": _short(found.titulo),
                   "prioridad": found.prioridad.name if found.prioridad else "sin_valorar",
                   "estado": found.estado.name} for found in listed],
    }


def _satisfaction(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    surveys, average = session.execute(
        select(func.count(), func.avg(SatisfaccionCliente.puntuacion))
        .where(SatisfaccionCliente.fecha_encuesta.between(desde, hasta))).one()
    return {"encuestas": surveys, "media": None if average is None else round(float(average), 2)}


def _objectives(session: Session, desde: date, hasta: date, cut_off: date) -> dict[str, Any]:
    found = list(session.scalars(select(ObjetivoCalidad).where(
        ObjetivoCalidad.estado != EstadoObjetivo.cancelado,
        ObjetivoCalidad.fecha_inicio <= hasta, ObjetivoCalidad.fecha_limite >= desde,
    ).order_by(ObjetivoCalidad.fecha_limite, ObjetivoCalidad.id)))
    measured: dict[int, list[MedicionObjetivo]] = {objective.id: [] for objective in found}
    if measured:
        for measurement in session.scalars(select(MedicionObjetivo).where(
                MedicionObjetivo.objetivo_id.in_(list(measured)),
                MedicionObjetivo.fecha <= cut_off).order_by(MedicionObjetivo.fecha,
                                                             MedicionObjetivo.id)):
            measured[measurement.objetivo_id].append(measurement)
    judged = [(objective, quality_objectives.summary(objective, measured[objective.id],
                                                     today=cut_off)) for objective in found]
    missed = [(objective, state) for objective, state in judged if state["cumple"] is False]
    return {
        "total": len(found),
        "en_meta": sum(state["cumple"] is True for _, state in judged),
        "fuera_de_meta": len(missed),
        "sin_medicion": sum(state["cumple"] is None for _, state in judged),
        "medicion_vencida": sum(bool(state["medicion_vencida"]) for _, state in judged),
        "por_estado": {member.name: sum(objective.estado is member for objective in found)
                       for member in EstadoObjetivo if member is not EstadoObjetivo.cancelado},
        "fuera_de_meta_lista": [{
            "titulo": objective.titulo, "ultimo_valor": state["ultimo_valor"],
            "ultima_fecha": state["ultima_fecha"], "meta": objective.meta,
            "sentido": objective.sentido.value,
        } for objective, state in missed[:LIST_LIMIT]],
    }


def _indicators(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    start, end = _moments(desde, hasta, aware=False)
    dated = and_(AuditoriaIndicador.fecha_auditoria >= start,
                 AuditoriaIndicador.fecha_auditoria < end)
    listed = session.scalars(select(AuditoriaIndicador).where(dated).order_by(
        AuditoriaIndicador.fecha_auditoria, AuditoriaIndicador.id_auditoria).limit(LIST_LIMIT))
    return {
        "total": _count(session, AuditoriaIndicador, dated),
        "lista": [{
            "area": found.area_auditoria, "fecha": found.fecha_auditoria.date(),
            "indicador": _short(found.indicador_desempeno), "resultado": _short(found.resultado),
        } for found in listed],
    }


def _nonconformities(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    detected = NoConformidad.fecha_detectada.between(desde, hasta)
    severity = _count_by(session, NoConformidad.gravedad, GravedadNoConformidad, detected,
                         unset=True)
    verified = _count_by(session, AccionCorrectiva.resultado_verificacion,
                         ResultadoVerificacion,
                         AccionCorrectiva.fecha_verificacion.between(desde, hasta))
    return {
        "abiertas_en_periodo": sum(severity.values()),
        "por_gravedad": severity,
        "por_origen": _count_by(session, NoConformidad.origen, OrigenNoConformidad, detected,
                                unset=True),
        "cerradas_en_periodo": _count(session, NoConformidad,
                                      NoConformidad.estado == EstadoNoConformidad.cerrada,
                                      NoConformidad.fecha_cierre.between(desde, hasta)),
        "acciones_correctivas": {"eficaces": verified["eficaz"],
                                 "no_eficaces": verified["no_eficaz"]},
    }


def _audits(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    dated = Auditoria.fecha.between(desde, hasta)
    completed = session.execute(
        select(Auditoria.id, Auditoria.area_auditada, Auditoria.fecha)
        .where(dated, Auditoria.estado == EstadoAuditoriaEnum.COMPLETADA)
        .order_by(Auditoria.fecha, Auditoria.id)).all()
    return {
        "en_periodo": _count(session, Auditoria, dated),
        "completadas": len(completed),
        "hallazgos_por_tipo": _count_by(
            session, HallazgoAuditoria.tipo, TipoHallazgo, dated,
            join=(Auditoria, HallazgoAuditoria.auditoria_id == Auditoria.id)),
        "completadas_lista": [{"id": audit_id, "area": area, "fecha": fecha}
                              for audit_id, area, fecha in completed[:LIST_LIMIT]],
    }


def _improvements(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    dated = Mejora.fecha_implementacion.between(desde, hasta)
    listed = session.scalars(select(Mejora).where(dated).order_by(
        Mejora.fecha_implementacion, Mejora.id_mejora).limit(LIST_LIMIT))
    return {
        "total": _count(session, Mejora, dated),
        "lista": [{"id": found.id_mejora, "descripcion": _short(found.descripcion),
                   "fecha": found.fecha_implementacion} for found in listed],
    }


def _training(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    dated = Capacitacion.fecha.between(desde, hasta)
    sessions, hours = session.execute(
        select(func.count(), func.coalesce(func.sum(Capacitacion.duracion_horas), 0))
        .where(dated)).one()
    return {
        "total": sessions,
        "horas": int(hours),
        "temas": list(session.scalars(select(Capacitacion.tema).where(dated).distinct()
                                      .order_by(Capacitacion.tema).limit(LIST_LIMIT))),
    }


def _providers(session: Session, desde: date, hasta: date, cut_off: date) -> dict[str, Any]:
    rated = session.execute(
        select(EvaluacionProveedor, Proveedor)
        .join(Proveedor, EvaluacionProveedor.proveedor_id == Proveedor.id)
        .where(EvaluacionProveedor.fecha.between(desde, hasta))
        .order_by(EvaluacionProveedor.fecha, EvaluacionProveedor.id)).all()
    results = {member: 0 for member in ResultadoEvaluacion}
    unsuitable: dict[int, tuple[Proveedor, EvaluacionProveedor, Any]] = {}
    for evaluation, provider in rated:  # the earliest first: the latest no_apto stays
        score = providers.puntuacion(evaluation)
        results[providers.resultado(score)] += 1
        if providers.resultado(score) is ResultadoEvaluacion.no_apto:
            unsuitable[provider.id] = (provider, evaluation, score)
    listed = sorted(unsuitable.values(), key=lambda found: (found[0].nombre, found[0].id))
    detected = and_(NoConformidad.origen == OrigenNoConformidad.proveedor,
                    NoConformidad.fecha_detectada.between(desde, hasta))
    cited = func.count(NoConformidad.id).label("total")
    most_cited = session.execute(
        select(Proveedor.id, Proveedor.nombre, cited)
        .join(NoConformidad, NoConformidad.proveedor_id == Proveedor.id).where(detected)
        .group_by(Proveedor.id, Proveedor.nombre)
        .order_by(cited.desc(), Proveedor.nombre, Proveedor.id).limit(LIST_LIMIT)).all()
    return {
        "por_estado": _count_by(session, Proveedor.estado, EstadoProveedor),
        "evaluaciones_vencidas": _count(session, Proveedor, providers.overdue(cut_off)),
        "evaluaciones": {
            "total": len(rated),
            "media": providers.average_of(evaluation for evaluation, _ in rated),
            "por_resultado": {member.name: count for member, count in results.items()},
        },
        "no_aptos": len(listed),
        "no_aptos_lista": [{
            "id": provider.id, "nombre": _short(provider.nombre), "fecha": evaluation.fecha,
            "puntuacion": score, "estado": provider.estado.name,
        } for provider, evaluation, score in listed[:LIST_LIMIT]],
        "no_conformidades": {
            "total": _count(session, NoConformidad, detected),
            "sin_proveedor": _count(session, NoConformidad, detected,
                                    NoConformidad.proveedor_id.is_(None)),
            "por_proveedor": [{"id": provider_id, "nombre": _short(nombre), "total": total}
                              for provider_id, nombre, total in most_cited],
        },
    }


def _qms_changes(session: Session, desde: date, hasta: date, cut_off: date) -> dict[str, Any]:
    found = qms_changes.in_period(session, desde, hasta, cut_off=cut_off)
    # The latest stage first: a change reviewed in the period was implemented before.
    done = sorted(found.implantados_o_revisados, reverse=True,
                  key=lambda change: (change.fecha_eficacia or change.fecha_implantacion,
                                      change.id))
    return {
        "propuestos": found.propuestos,
        "aprobados": found.aprobados,
        "implantados": found.implantados,
        "revisados": found.revisados,
        "eficaces": found.eficaces,
        "no_eficaces": found.no_eficaces,
        "cancelados": found.cancelados,
        "fuera_de_plazo": len(found.fuera_de_plazo),
        "fuera_de_plazo_lista": [{
            "id": change.id, "titulo": _short(change.titulo),
            "responsable": change.responsable.nombre, "fecha_aprobacion": change.fecha_aprobacion,
            "fecha_prevista": change.fecha_prevista, "estado": change.estado.name,
        } for change in found.fuera_de_plazo[:LIST_LIMIT]],
        "implantados_o_revisados": len(done),
        "implantados_o_revisados_lista": [{
            "id": change.id, "titulo": _short(change.titulo), "estado": change.estado.name,
            "fecha_implantacion": change.fecha_implantacion,
            "fecha_eficacia": change.fecha_eficacia,
            "eficacia": change.eficacia.name if change.eficacia else None,
        } for change in done[:LIST_LIMIT]],
    }


# -- helpers ---------------------------------------------------------------------------


def _count(session: Session, model: Any, *where: Any) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where))


def _count_by(session: Session, column: Any, enum_cls: Any, *where: Any,
              join: tuple[Any, Any] | None = None, unset: bool = False) -> dict[str, int]:
    """Rows matching ``where`` counted by ``column``, for every member of ``enum_cls``.

    Keys are member names in the enum's order, with zero when no row has them;
    ``unset`` adds ``sin_indicar`` for rows where the column is NULL. ``join`` is
    an optional ``(target, onclause)`` the conditions need.
    """
    query = select(column, func.count())
    if join is not None:
        query = query.join(*join)
    found = dict(session.execute(query.where(*where).group_by(column)).all())
    counts = {member.name: found.get(member, 0) for member in enum_cls}
    if unset:
        counts["sin_indicar"] = found.get(None, 0)
    return counts


def _count_by_band(session: Session, product: Any, ranges: dict) -> dict[str, int]:
    """Every record counted by the band of ``product`` (``ranges``, in band order), plus
    ``sin_valorar`` for the records whose product is NULL (not assessed, RO4)."""
    band = case(*((product.between(low, high), member.name)
                  for member, (low, high) in ranges.items()), else_="sin_valorar")
    found = dict(session.execute(select(band, func.count()).group_by(band)).all())
    return {name: found.get(name, 0) for name in (*(m.name for m in ranges), "sin_valorar")}


def _moments(desde: date, hasta: date, *, aware: bool) -> tuple[datetime, datetime]:
    """The first instant of ``desde`` and of the day after ``hasta`` (UTC when ``aware``)."""
    zone = timezone.utc if aware else None
    return (datetime.combine(desde, time.min, zone),
            datetime.combine(hasta + timedelta(days=1), time.min, zone))


def _short(value: str | None) -> str | None:
    """``value`` cut to ``SHORT_TEXT`` characters, ending in "…" when cut."""
    if value is None or len(value) <= SHORT_TEXT:
        return value
    return value[:SHORT_TEXT - 1] + "…"


def _member(value: Any, enum_cls: Any, message: str) -> Any:
    """A filter value given as the member or its name; anything else is ``message``."""
    try:
        return fields.enum_member({"value": value}, "value", enum_cls)
    except ValidationError:
        raise ValidationError(message) from None


def _has_actions(session: Session, review_id: int) -> bool:
    return bool(session.scalar(select(exists().where(AccionRevision.revision_id == review_id))))


def _cited_by_changes(session: Session, action_id: int) -> bool:
    return bool(session.scalar(select(exists().where(
        CambioSGC.accion_revision_id == action_id))))


def _actions_cited_by_changes(session: Session, review_id: int) -> bool:
    return bool(session.scalar(select(exists().where(
        CambioSGC.accion_revision_id == AccionRevision.id,
        AccionRevision.revision_id == review_id))))


def _locked(session: Session, review_id: Any, *, read: bool) -> RevisionDireccion | None:
    """The review loaded afresh with its row locked (shared when ``read``), or ``None``."""
    if not people.is_db_id(review_id):
        return None
    return session.scalars(
        select(RevisionDireccion).where(RevisionDireccion.id == review_id)
        .with_for_update(read=read).execution_options(populate_existing=True)
    ).one_or_none()


def _lock_review(session: Session, review_id: Any) -> RevisionDireccion:
    """The review locked for a change (``FOR UPDATE``), or ``NotFound``."""
    found = _locked(session, review_id, read=False)
    if found is None:
        raise NotFound(REVIEW_NOT_FOUND)
    return found


def _well_formed(data: Any) -> bool:
    """Whether ``data`` is a mapping of the actions' writable keys (else ``crud`` refuses it)."""
    return isinstance(data, Mapping) and set(data) <= ACTION_SPEC.writable


def _reject_unknown(data: Any) -> None:
    """Refuse a key outside the actions' fields before any lookup."""
    if isinstance(data, Mapping):
        fields.reject_unknown(data, ACTION_SPEC.writable)


def _today(today: Any) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("today must be a date")  # a programming error, not user input
    return today


def _flush(session: Session, message: str | None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
