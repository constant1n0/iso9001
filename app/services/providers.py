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

"""External providers and their evaluations (ISO 9001 clause 8.4, ``external-providers``).

Decisions EP1 to EP5. Two registers, ``providers`` (``Proveedor``) and
``evaluations`` (``EvaluacionProveedor``), each with ``get``, ``list_``,
``list_page``, ``create``, ``update`` and ``delete`` like ``quality_objectives``.
They share the policy resource ``EXTERNAL_PROVIDERS``: every role reads,
administrators and auditors create, update and record evaluations,
administrators delete, and the ``mcp`` channel never deletes. Services never
read the clock: the adapter passes ``today``, its local date, to the writes of
evaluations, to the decisions and to the lists whose ``evaluacion_vencida``
filter is used.

Providers:

- ``nombre`` is unique (``NAME_TAKEN``) and ``nif``, the tax id, unique when
  given (``NIF_TAKEN``); the tax id is stored upper case without spaces or
  hyphens and must then be 8 to 10 letters or digits. ``email`` is checked and
  lower-cased like a person's; ``tipo_suministro`` is a member name.
- ``procesos`` is a list of process ids that replaces the linked processes
  (``proveedor_procesos``). The table has no audit row of its own, so every
  audit row of a provider carries ``procesos`` next to the columns.
- A new provider is ``pendiente``. ``estado`` and its decision columns
  (``fecha_estado``, ``decidido_por_id``, ``motivo_estado``) are never part of a
  write: ``approve``, ``condition`` and ``block`` are the administrators'
  decisions, refused on the ``mcp`` channel (EP2); each records ``today``, the
  acting user's person (``people.of_user``, ``None`` without one) and the
  reason, required to condition or block. A decision that keeps the state is
  refused. There is no way back to ``pendiente``: a decision is corrected by
  another one.
- A provider with evaluations (``HAS_EVALUATIONS``) or nonconformities
  (``HAS_NONCONFORMITIES``) is never deleted; deleting one removes its process
  links, which its delete audit row keeps.
- Filters: ``nombre`` (substring, any case), ``estado``, ``tipo_suministro``,
  ``proceso_id``, ``con_procesos`` (linked to any process or to none),
  ``evaluacion_vencida`` (``fecha_proxima_evaluacion`` before ``today`` while
  not ``bloqueado``) and ``ultimo_resultado``, the result of the latest
  evaluation (``fecha``, then id), computed in SQL from its scores.

Evaluations score ``calidad``, ``plazo``, ``servicio`` and ``documentacion`` from
1 to 5 (EP3). ``puntuacion`` is their mean with two decimals and ``resultado``
its band: ``apto`` from 3.5, ``condicionado`` from 2.5, ``no_apto`` below; both
are computed, never stored. The date is never after ``today``, ``fecha_proxima``
comes after it, and the evaluator is a person, active when newly chosen. An
evaluation never moves to another provider, and any provider takes them, a
blocked one too. An evaluation never changes the provider's state; when the
provider's latest evaluation is recorded with ``fecha_proxima``, that date
becomes the provider's ``fecha_proxima_evaluacion`` (an older evaluation
recorded later never reschedules it). Afterwards the provider follows the
latest evaluation that has a ``fecha_proxima``: when an update or a delete
changes that date (the latest one cleared, moved back or deleted, or its date
changed), the provider takes the new one, and keeps its own when none remains.
A write that leaves that date alone keeps one edited on the provider. Filters:
``proveedor_id``, ``evaluador_id``; the latest first.

``latest_evaluation``, ``average_score``, ``average_of``, ``performance``,
``performances`` and ``attention`` read what EP-2's pages and dashboard and EP-3's
PDF show; ``overdue`` is the overdue condition the management review applies on
its cut-off. Like every service this
module flushes and never commits, writes an audit row next to each change, and an
update that changes nothing writes nothing. Rows that change are locked
(``FOR UPDATE``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from typing import Any

from sqlalchemy import and_, case, exists, false, func, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    EVALUATION_SCORES, EstadoProveedor, EvaluacionProveedor, NoConformidad, ProcesoOperacion,
    Proveedor, ResultadoEvaluacion, RoleEnum, TipoSuministro,
)
from . import audit, crud, fields, nonconformities, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.EXTERNAL_PROVIDERS
PROCESSES = "procesos"
NAME_MAX, NIF_MAX, CONTACT_MAX, PHONE_MAX = 200, 20, 150, 30  # mirror the columns
NIF_PATTERN = re.compile(r"[A-Z0-9]{8,10}")
SCORE_MIN, SCORE_MAX = 1, 5
APTO_MIN, CONDICIONADO_MIN = Decimal("3.5"), Decimal("2.5")
TWO_PLACES = Decimal("0.01")
# The mean of four whole scores moves in quarters, so each band starts at a total.
APTO_TOTAL = int(APTO_MIN * len(EVALUATION_SCORES))  # 14
CONDICIONADO_TOTAL = int(CONDICIONADO_MIN * len(EVALUATION_SCORES))  # 10
DECIDE_ROLES = frozenset({RoleEnum.ADMINISTRADOR})

PROVIDER_NOT_FOUND = "Proveedor no encontrado."
EVALUATION_NOT_FOUND = "Evaluación de proveedor no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
NAME_TAKEN = "Ya existe un proveedor con ese nombre."
NIF_TAKEN = "Ya existe un proveedor con ese NIF."
DUPLICATE = "Ya existe un proveedor con ese nombre o NIF."
BAD_NIF = "El NIF debe tener de 8 a 10 letras o cifras; se ignoran los espacios y los guiones."
PROCESS_IDS = "El campo «procesos» debe ser una lista de identificadores de proceso."
REASON_REQUIRED = "Indica el motivo de la decisión."
ALREADY_IN_STATE = "El proveedor ya está {estado}."
DECISION_CONFLICT = (
    "No se ha podido registrar la decisión porque los datos han cambiado entretanto; "
    "vuelve a intentarlo.")
HAS_EVALUATIONS = (
    "No se puede eliminar un proveedor que tiene evaluaciones; bloquéalo en su lugar.")
HAS_NONCONFORMITIES = (
    "No se puede eliminar un proveedor citado en no conformidades; bloquéalo en su lugar.")
DELETE_CONFLICT = (
    "No se ha podido eliminar el proveedor porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo.")
UNKNOWN_PROVIDER = "El campo «proveedor_id» no corresponde a ningún proveedor."
PROVIDER_FIXED = "Una evaluación no puede cambiar de proveedor."
EVALUATED_IN_FUTURE = "La fecha de la evaluación no puede ser futura."
NEXT_NOT_AFTER = "La próxima evaluación debe ser posterior a la fecha de la evaluación."
BAD_FILTER = "El filtro «{key}» no admite ese valor."
BAD_FLAG = "El filtro «{key}» debe ser verdadero o falso."


# -- field validators ------------------------------------------------------------------


def _nif(data: Mapping[str, Any], key: str) -> str | None:
    """A tax id upper-cased without spaces or hyphens; blank means none (``BAD_NIF``)."""
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(BAD_NIF)
    compact = re.sub(r"[\s-]", "", value).upper()
    if not compact:
        return None
    if not NIF_PATTERN.fullmatch(compact):
        raise ValidationError(BAD_NIF)
    return compact


def _optional_email(data: Mapping[str, Any], key: str) -> str | None:
    """Blank means no e-mail; otherwise a valid address, trimmed and lower-cased."""
    value = data[key]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return fields.email(data, key)


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


def _score(data: Mapping[str, Any], key: str) -> int:
    """A whole score from ``SCORE_MIN`` to ``SCORE_MAX``."""
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or not (
            SCORE_MIN <= value <= SCORE_MAX):
        raise ValidationError(
            f"El campo «{key}» debe ser un número entero del {SCORE_MIN} al {SCORE_MAX}.")
    return value


_TEXT = partial(fields.text, strip=False)

PROVIDER_SPEC = crud.Spec(
    model=Proveedor,
    resource=RESOURCE,
    not_found=PROVIDER_NOT_FOUND,
    fields=(
        crud.Field("nombre", partial(fields.text, required=True, max_length=NAME_MAX),
                   required=True),
        crud.Field("nif", _nif),
        crud.Field("contacto", partial(fields.text, max_length=CONTACT_MAX)),
        crud.Field("email", _optional_email),
        crud.Field("telefono", partial(fields.text, max_length=PHONE_MAX)),
        crud.Field("tipo_suministro", partial(fields.enum_member, enum_cls=TipoSuministro),
                   required=True),
        crud.Field("descripcion_suministro", _TEXT),
        crud.Field("criterios_seleccion", _TEXT),
        crud.Field("fecha_proxima_evaluacion", _optional_date),
        crud.Field("notas", _TEXT),
    ),
    order_by=(Proveedor.nombre, Proveedor.id),
    conflict=DUPLICATE,
)
# The columns plus ``procesos``; the MCP registry mirrors both.
PROVIDER_WRITABLE = PROVIDER_SPEC.writable | {PROCESSES}
PROVIDER_REQUIRED = frozenset(f.name for f in PROVIDER_SPEC.fields if f.required)

EVALUATION_SPEC = crud.Spec(
    model=EvaluacionProveedor,
    resource=RESOURCE,
    not_found=EVALUATION_NOT_FOUND,
    fields=(
        crud.Field("proveedor_id", partial(fields.integer, required=True), required=True),
        crud.Field("fecha", fields.required_date, required=True),
        crud.Field("evaluador_id", partial(fields.integer, required=True), required=True,
                   check=people.check_reference),
        *(crud.Field(score, _score, required=True) for score in EVALUATION_SCORES),
        crud.Field("comentario", _TEXT),
        crud.Field("fecha_proxima", _optional_date),
    ),
    # The latest first.
    order_by=(EvaluacionProveedor.fecha.desc(), EvaluacionProveedor.id.desc()),
)

_FIELDS = {spec.model: {f.name: f for f in spec.fields}
           for spec in (PROVIDER_SPEC, EVALUATION_SPEC)}


# -- scores and results (decision EP3) -------------------------------------------------


def puntuacion(evaluation: Any) -> Decimal:
    """The mean of an evaluation's four scores, rounded half up to two decimals."""
    return _mean(_score_total(evaluation), 1)


def _score_total(evaluation: Any) -> int:
    return sum(getattr(evaluation, score) for score in EVALUATION_SCORES)


def _mean(total: int, evaluations: int) -> Decimal:
    """The mean score of ``evaluations`` whose scores add up to ``total``, two decimals."""
    return (Decimal(total) / (evaluations * len(EVALUATION_SCORES))).quantize(
        TWO_PLACES, ROUND_HALF_UP)


def average_of(evaluations: Iterable[Any]) -> Decimal | None:
    """The mean score of every one of ``evaluations`` (two decimals), or ``None`` without any.

    Pooled: each evaluation weighs the same, whichever provider it rates.
    """
    found = list(evaluations)
    return _mean(sum(map(_score_total, found)), len(found)) if found else None


def resultado(score: Decimal | int | float) -> ResultadoEvaluacion:
    """The band of a score: ``apto`` from 3.5, ``condicionado`` from 2.5, else ``no_apto``."""
    if score >= APTO_MIN:
        return ResultadoEvaluacion.apto
    if score >= CONDICIONADO_MIN:
        return ResultadoEvaluacion.condicionado
    return ResultadoEvaluacion.no_apto


def _total(model: type = EvaluacionProveedor) -> Any:
    """The SQL sum of an evaluation's four scores."""
    calidad, plazo, servicio, documentacion = (getattr(model, s) for s in EVALUATION_SCORES)
    return calidad + plazo + servicio + documentacion


def _latest_total() -> Any:
    """Correlated subquery: the score total of the provider's latest evaluation, or NULL."""
    return (select(_total()).where(EvaluacionProveedor.proveedor_id == Proveedor.id)
            .order_by(EvaluacionProveedor.fecha.desc(), EvaluacionProveedor.id.desc())
            .limit(1).correlate(Proveedor).scalar_subquery())


def _result_condition(member: ResultadoEvaluacion) -> Any:
    """Providers whose latest evaluation gives ``member``; never one without evaluations."""
    total = _latest_total()
    if member is ResultadoEvaluacion.apto:
        return total >= APTO_TOTAL
    if member is ResultadoEvaluacion.condicionado:
        return and_(total >= CONDICIONADO_TOTAL, total < APTO_TOTAL)
    return total < CONDICIONADO_TOTAL


# -- providers -------------------------------------------------------------------------


def _create_provider(session: Session, actor: Actor, data: Mapping[str, Any]) -> Proveedor:
    """Create a ``pendiente`` provider; ``nombre`` and ``tipo_suministro`` are required."""
    policy.require(actor, Action.CREATE, RESOURCE)
    columns, linked = _split(session, data)
    fields.require_keys(columns, PROVIDER_REQUIRED)
    values = _clean(columns)
    _ensure_free(session, values)
    created = Proveedor(estado=EstadoProveedor.pendiente, procesos=linked or [], **values)
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:  # a concurrent duplicate
        raise Conflict(DUPLICATE) from exc
    row.after = _snapshot(created)  # the row is still pending: add the processes
    return created


def _update_provider(session: Session, actor: Actor, record_id: int,
                     data: Mapping[str, Any]) -> Proveedor:
    """Apply the given fields; ``procesos`` replaces the linked processes."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    _reject_unknown(PROVIDER_WRITABLE, data)
    found = _locked(session, Proveedor, record_id, PROVIDER_NOT_FOUND)
    columns, linked = _split(session, data)
    values = _clean(columns)
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if linked is not None and {p.id_proceso for p in linked} == set(found.proceso_ids):
        linked = None
    if not changes and linked is None:
        return found
    _ensure_free(session, changes, own_id=found.id)
    before = _snapshot(found)
    for key, value in changes.items():
        setattr(found, key, value)
    if linked is not None:
        found.procesos = linked
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, DUPLICATE)
    return found


def _delete_provider(session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete a provider without evaluations or nonconformities, with its links.

    One evaluated or cited after the check meets a foreign key at the flush;
    PostgreSQL names it, so the message still says why.
    """
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _locked(session, Proveedor, record_id, PROVIDER_NOT_FOUND)
    if _has_evaluations(session, found.id):
        raise Conflict(HAS_EVALUATIONS)
    if _has_nonconformities(session, found.id):
        raise Conflict(HAS_NONCONFORMITIES)
    audit.record(session, actor, "delete", found, before=_snapshot(found))
    session.delete(found)  # its ``proveedor_procesos`` rows go with it
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(_DELETE_MESSAGES.get(_violated_constraint(exc), DELETE_CONFLICT)) from exc


def _split(session: Session, data: Any) -> tuple[dict[str, Any], list[ProcesoOperacion] | None]:
    """The column keys of ``data`` and the processes (``None`` without ``procesos``)."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, PROVIDER_WRITABLE)
    columns = {key: value for key, value in data.items() if key != PROCESSES}
    if PROCESSES not in data:
        return columns, None
    return columns, _processes(session, data[PROCESSES])


def _clean(columns: Mapping[str, Any]) -> dict[str, Any]:
    """The column values of ``columns``, validated by ``PROVIDER_SPEC``."""
    return {f.name: f.clean(columns, f.name) for f in PROVIDER_SPEC.fields if f.name in columns}


def _processes(session: Session, value: Any) -> list[ProcesoOperacion]:
    """The processes named by ``value`` (``None`` means none); each must exist."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise ValidationError(PROCESS_IDS)
    wanted = set(value)
    searchable = [item for item in wanted if people.is_db_id(item)]
    found = list(session.scalars(
        select(ProcesoOperacion).where(ProcesoOperacion.id_proceso.in_(searchable))
        .order_by(ProcesoOperacion.proceso, ProcesoOperacion.id_proceso)
    )) if searchable else []
    missing = sorted(wanted - {process.id_proceso for process in found})
    if missing:
        raise ValidationError(
            f"Procesos desconocidos en «procesos»: {', '.join(map(str, missing))}.")
    return found


def _ensure_free(session: Session, values: Mapping[str, Any], own_id: int | None = None) -> None:
    """Raise ``Conflict`` when another provider has the name or the tax id."""
    for key, column, message in (("nombre", Proveedor.nombre, NAME_TAKEN),
                                 ("nif", Proveedor.nif, NIF_TAKEN)):
        if values.get(key) is None:
            continue
        query = select(Proveedor.id).where(column == values[key])
        if own_id is not None:
            query = query.where(Proveedor.id != own_id)
        if session.scalar(query) is not None:
            raise Conflict(message)


def _snapshot(found: Proveedor) -> dict[str, Any]:
    """Column values plus ``procesos``, as stored on the provider's audit rows."""
    return audit.snapshot(found) | {PROCESSES: found.proceso_ids}


def overdue(today: date) -> Any:
    """SQL: due for evaluation before ``today`` and not blocked; never NULL.

    ``evaluacion_vencida`` in Python; the management review judges it on its cut-off.
    """
    return and_(Proveedor.estado != EstadoProveedor.bloqueado,
                Proveedor.fecha_proxima_evaluacion.is_not(None),
                Proveedor.fecha_proxima_evaluacion < today)


def evaluacion_vencida(provider: Proveedor, *, today: date) -> bool:
    """Whether a provider that is not blocked was due for evaluation before ``today``."""
    return (provider.estado is not EstadoProveedor.bloqueado
            and provider.fecha_proxima_evaluacion is not None
            and provider.fecha_proxima_evaluacion < today)


def _provider_conditions(nombre: str | None = None, estado: Any = None,
                         tipo_suministro: Any = None, proceso_id: int | None = None,
                         evaluacion_vencida: bool | None = None, ultimo_resultado: Any = None,
                         today: date | None = None,
                         con_procesos: bool | None = None) -> list[Any]:
    """Provider filters (AND); ``evaluacion_vencida`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    if nombre:
        where.append(Proveedor.nombre.ilike(f"%{nombre}%"))
    if estado:
        where.append(Proveedor.estado == _member(estado, EstadoProveedor, "estado"))
    if tipo_suministro:
        where.append(Proveedor.tipo_suministro == _member(tipo_suministro, TipoSuministro,
                                                          "tipo_suministro"))
    if proceso_id is not None:
        where.append(Proveedor.procesos.any(ProcesoOperacion.id_proceso == proceso_id)
                     if people.is_db_id(proceso_id) else false())
    if con_procesos is not None:
        linked = Proveedor.procesos.any()
        where.append(linked if _flag(con_procesos, "con_procesos") else not_(linked))
    if evaluacion_vencida is not None:
        late = overdue(_today(today))
        where.append(late if _flag(evaluacion_vencida, "evaluacion_vencida") else not_(late))
    if ultimo_resultado:
        where.append(_result_condition(_member(ultimo_resultado, ResultadoEvaluacion,
                                               "ultimo_resultado")))
    return where


# -- state decisions (decision EP2) ----------------------------------------------------


def may_decide(actor: Actor) -> bool:
    """Whether ``actor`` may approve, condition or block providers (role, scope, channel)."""
    return (policy.can(actor, Action.UPDATE, RESOURCE) and actor.role in DECIDE_ROLES
            and actor.channel != "mcp")


def approve(session: Session, actor: Actor, provider_id: int, *, today: date,
            motivo: str | None = None) -> Proveedor:
    """Approve a provider; an optional ``motivo`` is kept, a blank one is none."""
    return _decide(session, actor, provider_id, EstadoProveedor.aprobado, motivo, today=today,
                   reason_required=False)


def condition(session: Session, actor: Actor, provider_id: int, motivo: str | None, *,
              today: date) -> Proveedor:
    """Approve a provider under conditions, stating them in ``motivo`` (required)."""
    return _decide(session, actor, provider_id, EstadoProveedor.condicionado, motivo,
                   today=today)


def block(session: Session, actor: Actor, provider_id: int, motivo: str | None, *,
          today: date) -> Proveedor:
    """Block a provider, stating why in ``motivo`` (required)."""
    return _decide(session, actor, provider_id, EstadoProveedor.bloqueado, motivo, today=today)


def _decide(session: Session, actor: Actor, provider_id: int, estado: EstadoProveedor,
            motivo: Any, *, today: date, reason_required: bool = True) -> Proveedor:
    """Move a provider to ``estado`` with the date, the deciding person and the reason.

    Raises:
        PermissionDenied: Not an administrator, or the ``mcp`` channel.
        NotFound: Unknown provider.
        ValidationError: A required reason is missing, or the state would not change.
        ValueError: ``today`` is not a ``date`` (a programming error).
        Conflict: The database refused the decision (``DECISION_CONFLICT``).
    """
    policy.require(actor, Action.UPDATE, RESOURCE)
    if not may_decide(actor):
        raise PermissionDenied()
    today = _today(today)
    found = _locked(session, Proveedor, provider_id, PROVIDER_NOT_FOUND)
    if motivo is not None and not isinstance(motivo, str):
        raise ValidationError(REASON_REQUIRED)
    reason = motivo.strip() if motivo else None
    if not reason and reason_required:
        raise ValidationError(REASON_REQUIRED)
    if found.estado is estado:
        raise ValidationError(ALREADY_IN_STATE.format(estado=estado.value.lower()))
    decider = people.of_user(session, actor.user_id)  # queries: before any change
    before = _snapshot(found)
    found.estado = estado
    found.fecha_estado = today
    found.decidido_por_id = decider
    found.motivo_estado = reason or None
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session, DECISION_CONFLICT)
    return found


# -- evaluations -----------------------------------------------------------------------


def _create_evaluation(session: Session, actor: Actor, data: Mapping[str, Any], *,
                       today: date) -> EvaluacionProveedor:
    """Record an evaluation of an existing provider, read with its row locked."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(EVALUATION_SPEC.writable, data)
    provider = None
    if _well_formed(data) and "proveedor_id" in data:
        provider = _provider_of(session, data)
        _check_evaluation(data, None, today)
    created = crud.create(EVALUATION_SPEC, session, actor, data)
    _reschedule(session, actor, provider, created)
    return created


def _update_evaluation(session: Session, actor: Actor, evaluation_id: int,
                       data: Mapping[str, Any], *, today: date) -> EvaluacionProveedor:
    """Apply the given fields; the evaluation keeps its provider."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(EVALUATION_SPEC.writable, data)
    found = _locked(session, EvaluacionProveedor, evaluation_id, EVALUATION_NOT_FOUND)
    provider = ruling = None
    if _well_formed(data):
        if "proveedor_id" in data and _final(data, found, "proveedor_id") != found.proveedor_id:
            raise ValidationError(PROVIDER_FIXED)
        if _changed(found, data):  # a no-op is left to ``crud``, which writes nothing
            _check_evaluation(data, found, today)
            if any(key in data and _final(data, None, key) != getattr(found, key)
                   for key in ("fecha", "fecha_proxima")):
                provider = _locked(session, Proveedor, found.proveedor_id, PROVIDER_NOT_FOUND)
                ruling = _ruling_date(session, provider.id)
    updated = crud.update(EVALUATION_SPEC, session, actor, found.id, data)
    _follow(session, actor, provider, ruling)
    return updated


def _delete_evaluation(session: Session, actor: Actor, evaluation_id: int) -> None:
    """Hard-delete an evaluation, keeping its snapshot in the audit log; see ``_follow``."""
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _locked(session, EvaluacionProveedor, evaluation_id, EVALUATION_NOT_FOUND)
    provider = _locked(session, Proveedor, found.proveedor_id, PROVIDER_NOT_FOUND)
    ruling = _ruling_date(session, provider.id)
    crud.delete(EVALUATION_SPEC, session, actor, found.id)
    _follow(session, actor, provider, ruling)


def _provider_of(session: Session, data: Mapping[str, Any]) -> Proveedor:
    """The evaluated provider, locked (``FOR UPDATE``): the write may reschedule it."""
    provider_id = _final(data, None, "proveedor_id")
    found = _locked(session, Proveedor, provider_id, None)
    if found is None:
        raise ValidationError(UNKNOWN_PROVIDER)
    return found


def _check_evaluation(data: Mapping[str, Any], found: EvaluacionProveedor | None,
                      today: date) -> None:
    """The dates an evaluation keeps, on the values the write leaves."""
    evaluated, following = _final(data, found, "fecha"), _final(data, found, "fecha_proxima")
    if "fecha" in data and evaluated is not None and evaluated > today:
        raise ValidationError(EVALUATED_IN_FUTURE)
    if following is not None and evaluated is not None and following <= evaluated:
        raise ValidationError(NEXT_NOT_AFTER)


def _reschedule(session: Session, actor: Actor, provider: Proveedor | None,
                evaluation: EvaluacionProveedor) -> None:
    """Copy ``fecha_proxima`` to the provider when ``evaluation`` is its latest one."""
    following = evaluation.fecha_proxima
    if (provider is None or following is None
            or latest_evaluation(session, provider.id) is not evaluation):
        return
    _set_next(session, actor, provider, following)


def _ruling_date(session: Session, provider_id: int) -> date | None:
    """``fecha_proxima`` of the provider's latest evaluation that has one, or ``None``."""
    return session.scalar(
        select(EvaluacionProveedor.fecha_proxima)
        .where(EvaluacionProveedor.proveedor_id == provider_id,
               EvaluacionProveedor.fecha_proxima.is_not(None))
        .order_by(EvaluacionProveedor.fecha.desc(), EvaluacionProveedor.id.desc()).limit(1))


def _follow(session: Session, actor: Actor, provider: Proveedor | None,
            ruling: date | None) -> None:
    """After an update or a delete: a changed ruling date (``_ruling_date``) reschedules.

    ``ruling`` is the date before the write; none remaining keeps the provider's.
    """
    if provider is None:
        return
    following = _ruling_date(session, provider.id)
    if following is not None and following != ruling:
        _set_next(session, actor, provider, following)


def _set_next(session: Session, actor: Actor, provider: Proveedor, following: date) -> None:
    """Set the provider's next evaluation date, with its audit row."""
    if provider.fecha_proxima_evaluacion == following:
        return
    before = _snapshot(provider)
    provider.fecha_proxima_evaluacion = following
    stamp_updated(provider, actor)
    audit.record(session, actor, "update", provider, before=before, after=_snapshot(provider))
    _flush(session)


def _evaluation_conditions(proveedor_id: int | None = None,
                           evaluador_id: int | None = None) -> list[Any]:
    """Evaluation filters (AND); an id that cannot be stored matches nothing."""
    where: list[Any] = []
    for column, value in ((EvaluacionProveedor.proveedor_id, proveedor_id),
                          (EvaluacionProveedor.evaluador_id, evaluador_id)):
        if value is not None:
            where.append(column == value if people.is_db_id(value) else false())
    return where


# -- the registers ---------------------------------------------------------------------

providers = Register(
    SPEC=PROVIDER_SPEC,
    conditions=_provider_conditions,
    create=_create_provider,
    update=_update_provider,
    delete=_delete_provider,
)

evaluations = Register(
    SPEC=EVALUATION_SPEC,
    conditions=_evaluation_conditions,
    create=_create_evaluation,
    update=_update_evaluation,
    delete=_delete_evaluation,
)


# -- performance and attention ---------------------------------------------------------


def latest_evaluation(session: Session, provider_id: int) -> EvaluacionProveedor | None:
    """The provider's latest evaluation (by ``fecha``, then id), or ``None``.

    For adapters that already read the provider (no policy check of its own).
    """
    return session.scalars(
        select(EvaluacionProveedor).where(EvaluacionProveedor.proveedor_id == provider_id)
        .order_by(EvaluacionProveedor.fecha.desc(), EvaluacionProveedor.id.desc()).limit(1)
    ).first()


def average_score(session: Session, provider_id: int) -> Decimal | None:
    """The mean of all the provider's evaluation scores (two decimals), or ``None``.

    For adapters that already read the provider (no policy check of its own).
    """
    count, total = session.execute(
        select(func.count(), func.sum(_total()))
        .where(EvaluacionProveedor.proveedor_id == provider_id)).one()
    return _mean(total, count) if count else None


@dataclass(frozen=True)
class Performance:
    """How a provider performs (ISO 9001 clause 9.1.3 f): its evaluations and nonconformities."""

    evaluaciones: int
    puntuacion_media: Decimal | None  # over every evaluation
    ultima_puntuacion: Decimal | None
    ultimo_resultado: ResultadoEvaluacion | None
    ultima_fecha: date | None  # of the latest evaluation
    no_conformidades: int
    no_conformidades_abiertas: int


def performance(session: Session, actor: Actor, provider_id: int) -> Performance:
    """The provider's evaluations and nonconformities in numbers (read grants of both)."""
    found = providers.get(session, actor, provider_id)
    return performances(session, actor, [found.id])[found.id]


def performances(session: Session, actor: Actor,
                 provider_ids: Iterable[int]) -> dict[int, Performance]:
    """``Performance`` of each of ``provider_ids`` in two queries, for a list page.

    Every id given that can be stored is a key, with zeros and ``None`` when
    nothing evaluates or cites it. Read grants of providers and nonconformities.
    """
    policy.require(actor, Action.READ, RESOURCE)
    policy.require(actor, Action.READ, Resource.NONCONFORMITIES)
    wanted = {value for value in provider_ids if people.is_db_id(value)}
    if not wanted:
        return {}
    evaluated: dict[int, list[EvaluacionProveedor]] = {value: [] for value in wanted}
    for evaluation in session.scalars(
            select(EvaluacionProveedor).where(EvaluacionProveedor.proveedor_id.in_(wanted))
            .order_by(*EVALUATION_SPEC.order_by)):
        evaluated[evaluation.proveedor_id].append(evaluation)  # the latest first
    still_open = case((NoConformidad.estado.in_(nonconformities.OPEN_STATES), 1), else_=0)
    cited = {provider_id: (total, opened or 0) for provider_id, total, opened in session.execute(
        select(NoConformidad.proveedor_id, func.count(), func.sum(still_open))
        .where(NoConformidad.proveedor_id.in_(wanted)).group_by(NoConformidad.proveedor_id))}
    found = {}
    for provider_id, evaluations in evaluated.items():
        score = puntuacion(evaluations[0]) if evaluations else None
        total, opened = cited.get(provider_id, (0, 0))
        found[provider_id] = Performance(
            evaluaciones=len(evaluations),
            puntuacion_media=average_of(evaluations),
            ultima_puntuacion=score, ultimo_resultado=None if score is None else resultado(score),
            ultima_fecha=evaluations[0].fecha if evaluations else None,
            no_conformidades=total, no_conformidades_abiertas=int(opened),
        )
    return found


def attention(session: Session, actor: Actor, *, today: date) -> dict[str, list[Proveedor]]:
    """What the dashboard flags on ``today`` (decision EP6, read grant).

    - ``evaluaciones_vencidas``: providers not blocked whose next evaluation was
      before ``today``, the earliest first.
    - ``bloqueados_con_procesos``: blocked providers still linked to processes.
    - ``aprobados_no_aptos``: approved providers whose latest evaluation is
      ``no_apto``.

    The last two by name.
    """
    policy.require(actor, Action.READ, RESOURCE)
    today = _today(today)

    def found(*where: Any, order: tuple = PROVIDER_SPEC.order_by) -> list[Proveedor]:
        return list(session.scalars(select(Proveedor).where(*where).order_by(*order)))

    return {
        "evaluaciones_vencidas": found(overdue(today), order=(
            Proveedor.fecha_proxima_evaluacion, Proveedor.id)),
        "bloqueados_con_procesos": found(Proveedor.estado == EstadoProveedor.bloqueado,
                                         Proveedor.procesos.any()),
        "aprobados_no_aptos": found(Proveedor.estado == EstadoProveedor.aprobado,
                                    _result_condition(ResultadoEvaluacion.no_apto)),
    }


# -- helpers ---------------------------------------------------------------------------


def _final(data: Mapping[str, Any], found: Any, key: str) -> Any:
    """``key`` of an evaluation as the write leaves it: the clean given value, else the record's."""
    if key in data:
        return _FIELDS[EvaluacionProveedor][key].clean(data, key)
    return None if found is None else getattr(found, key)


def _changed(found: EvaluacionProveedor, data: Mapping[str, Any]) -> bool:
    """Whether ``data`` changes ``found``; invalid data counts as a change."""
    try:
        return any(_final(data, None, key) != getattr(found, key) for key in data)
    except ValidationError:
        return True


def _has_evaluations(session: Session, provider_id: int) -> bool:
    return bool(session.scalar(select(exists().where(
        EvaluacionProveedor.proveedor_id == provider_id))))


def _has_nonconformities(session: Session, provider_id: int) -> bool:
    return bool(session.scalar(select(exists().where(NoConformidad.proveedor_id == provider_id))))


def _locked(session: Session, model: type, record_id: Any, not_found: str | None) -> Any:
    """The record loaded afresh with its row locked (``FOR UPDATE``).

    An unknown id raises ``NotFound(not_found)``, or returns ``None`` without a message.
    """
    found = None
    if people.is_db_id(record_id):
        found = session.scalars(
            select(model).where(model.id == record_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
    if found is None and not_found is not None:
        raise NotFound(not_found)
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


def _well_formed(data: Any) -> bool:
    """Whether ``data`` is a mapping of an evaluation's writable keys (else ``crud`` refuses it)."""
    return isinstance(data, Mapping) and set(data) <= EVALUATION_SPEC.writable


def _reject_unknown(writable: frozenset[str], data: Any) -> None:
    """Refuse a key outside ``writable`` before any lookup (``crud`` refuses non-mappings)."""
    if isinstance(data, Mapping):
        fields.reject_unknown(data, writable)


def _today(today: Any) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("today must be a date")  # a programming error, not user input
    return today


def _flush(session: Session, message: str | None = None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to providers, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is Proveedor.__table__}
    return name


# The message for each foreign key whose violation means "still evaluated" or "still cited".
_DELETE_MESSAGES = {_foreign_key(EvaluacionProveedor): HAS_EVALUATIONS,
                    _foreign_key(NoConformidad): HAS_NONCONFORMITIES}
