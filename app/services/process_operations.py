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

"""Processes (``ProcesoOperacion``, ISO 9001 clauses 4.4 and 8.1): the JSON register and its pages.

A process has a unique name (a duplicate raises ``Conflict``), a type
(``TipoProceso`` by name; blank or ``None`` means none, as on rows that predate
it), an owner (``responsable_id``, checked by ``people.check_reference``), its
inputs, outputs, resources, operating criteria (``criterio_calidad``) and
acceptance criteria, the legacy supplier flag and nonconformity text, and the
processes it interacts with (decision PR1 of ``process-role-registers``).

``relacionados`` is a list of process ids that replaces the current set; every
id must exist and none may be the process itself. The relation is mutual: each
pair is stored once in ``proceso_relaciones``, lower id first, and
``related_ids`` and ``related`` read it both ways. The table has no audit row
of its own, so the create and update rows of a process carry ``relacionados``
next to the columns. ``list_`` and ``list_page`` filter by ``tipo`` and
``responsable_id``; ``cited_by`` gathers the quality objectives, risks and
opportunities citing a process (PR2), and ``counts`` how many of them and of
related processes each process has, for the list. Writes flush and never
commit, and an update that changes nothing writes nothing.

A process that quality objectives cite cannot be deleted (``Conflict`` with
``CITED_BY_OBJECTIVES``), nor one that risks or opportunities cite
(``CITED_BY_RISKS``), nor one related to other processes (``HAS_RELATIONS``):
``delete`` checks all three, and the ``ON DELETE RESTRICT`` foreign keys, which
SQLite only enforces on request, refuse a citation or relation added after the
check. Any other conflict keeps its own message.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from sqlalchemy import and_, exists, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    ObjetivoCalidad, Oportunidad, ProcesoOperacion, Riesgo, TipoProceso, proceso_relaciones,
)
from . import audit, crud, fields, people, policy, quality_objectives, risks
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

PROCESO_MAX = 100  # mirrors ProcesoOperacion.proceso
RELATED = "relacionados"
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
RELATED_IDS = "El campo «relacionados» debe ser una lista de identificadores de proceso."
RELATED_ITSELF = "Un proceso no puede relacionarse consigo mismo."
BAD_TYPE_FILTER = "El filtro «tipo» no es válido."


def _optional_type(data: Mapping[str, Any], key: str) -> TipoProceso | None:
    """A ``TipoProceso`` given as the member or its name; blank or ``None`` means none."""
    if data[key] is None or data[key] == "":
        return None
    return fields.enum_member(data, key, TipoProceso)


_TEXT = partial(fields.text, strip=False)

SPEC = crud.Spec(
    model=ProcesoOperacion,
    resource=Resource.PROCESS_OPERATIONS,
    not_found="Proceso de operación no encontrado.",
    fields=(
        crud.Field("proceso", partial(fields.text, required=True, max_length=PROCESO_MAX), required=True),
        crud.Field("tipo", _optional_type),
        crud.Field("responsable_id", fields.integer, check=people.check_reference),
        crud.Field("entradas", _TEXT),
        crud.Field("salidas", _TEXT),
        crud.Field("recursos", _TEXT),
        crud.Field("criterio_calidad", _TEXT),
        crud.Field("criterios_aceptacion", _TEXT),
        crud.Field("control_proveedor", fields.boolean),
        crud.Field("no_conformidad", _TEXT),
    ),
    order_by=(ProcesoOperacion.id_proceso,),
    conflict="Ya existe un proceso con ese nombre.",
)
# The columns plus ``relacionados``; the MCP registry mirrors both.
WRITABLE_FIELDS = SPEC.writable | {RELATED}
REQUIRED_ON_CREATE = frozenset(f.name for f in SPEC.fields if f.required)

CITED_BY_OBJECTIVES = (
    "No se puede eliminar un proceso al que se refieren objetivos de calidad; "
    "quítalo antes de esos objetivos."
)
CITED_BY_RISKS = (
    "No se puede eliminar un proceso al que se refieren riesgos u oportunidades; "
    "quítalo antes de esos registros."
)
HAS_RELATIONS = (
    "No se puede eliminar un proceso relacionado con otros procesos; "
    "quita antes esas relaciones."
)

get = partial(crud.get, SPEC)


@dataclass(frozen=True)
class Citations:
    """The records citing a process, each list in its own register's order."""

    objetivos: list[ObjetivoCalidad]
    riesgos: list[Riesgo]
    oportunidades: list[Oportunidad]


def _conditions(tipo: TipoProceso | str | None = None,
                responsable_id: int | None = None) -> list[Any]:
    """The list filters; they combine with AND."""
    where: list[Any] = []
    if tipo is not None:
        try:
            member = fields.enum_member({"tipo": tipo}, "tipo", TipoProceso)
        except ValidationError:
            raise ValidationError(BAD_TYPE_FILTER) from None
        where.append(ProcesoOperacion.tipo == member)
    if responsable_id is not None:
        where.append(ProcesoOperacion.responsable_id == responsable_id
                     if people.is_db_id(responsable_id) else false())
    return where


def list_(session: Session, actor: Actor, *, tipo: TipoProceso | str | None = None,
          responsable_id: int | None = None) -> list[ProcesoOperacion]:
    """Every process matching the filters, in id order."""
    policy.require(actor, Action.READ, SPEC.resource)
    return crud.list_(SPEC, session, actor, _conditions(tipo, responsable_id))


def list_page(session: Session, actor: Actor, *, tipo: TipoProceso | str | None = None,
              responsable_id: int | None = None, page: int = 1,
              per_page: int = crud.DEFAULT_PER_PAGE) -> tuple[list[ProcesoOperacion], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    policy.require(actor, Action.READ, SPEC.resource)
    return crud.list_page(SPEC, session, actor, _conditions(tipo, responsable_id),
                          page=page, per_page=per_page)


def related_ids(session: Session, process_id: int) -> list[int]:
    """Ids of the processes related to ``process_id``, ascending, in either direction."""
    pairs = proceso_relaciones.c
    rows = session.execute(select(pairs.proceso_id, pairs.relacionado_id).where(
        or_(pairs.proceso_id == process_id, pairs.relacionado_id == process_id)))
    return sorted(second if first == process_id else first for first, second in rows)


def related(session: Session, actor: Actor, process_id: int) -> list[ProcesoOperacion]:
    """The processes related to one (``NotFound`` if unknown), by name then id."""
    found = get(session, actor, process_id)
    ids = related_ids(session, found.id_proceso)
    if not ids:
        return []
    return list(session.scalars(
        select(ProcesoOperacion).where(ProcesoOperacion.id_proceso.in_(ids))
        .order_by(ProcesoOperacion.proceso, ProcesoOperacion.id_proceso)))


def cited_by(session: Session, actor: Actor, process_id: int) -> Citations:
    """The quality objectives, risks and opportunities citing a process (PR2).

    ``NotFound`` for an unknown process; needs the read grant of processes,
    quality objectives and risks and opportunities.
    """
    found = get(session, actor, process_id)
    own = found.id_proceso
    return Citations(
        objetivos=crud.list_(quality_objectives.OBJECTIVE_SPEC, session, actor,
                             [ObjetivoCalidad.proceso_id == own]),
        riesgos=crud.list_(risks.RISK_SPEC, session, actor, [Riesgo.proceso_id == own]),
        oportunidades=crud.list_(risks.OPPORTUNITY_SPEC, session, actor,
                                 [Oportunidad.proceso_id == own]),
    )


@dataclass(frozen=True)
class Counts:
    """How many related processes and citing records each process has, by process id."""

    relacionados: Counter[int]
    objetivos: Counter[int]
    riesgos: Counter[int]  # risks and opportunities together


def counts(session: Session, actor: Actor) -> Counts:
    """The list's counts for every process, in five grouped queries.

    Needs the read grant of processes, quality objectives and risks and
    opportunities, like ``cited_by``.
    """
    for resource in (SPEC.resource, Resource.QUALITY_OBJECTIVES, Resource.RISKS_OPPORTUNITIES):
        policy.require(actor, Action.READ, resource)

    def by(column: Any) -> Counter[int]:
        return Counter(dict(session.execute(
            select(column, func.count()).where(column.is_not(None)).group_by(column)).all()))

    pairs = proceso_relaciones.c
    return Counts(
        relacionados=by(pairs.proceso_id) + by(pairs.relacionado_id),
        objetivos=by(ObjetivoCalidad.proceso_id),
        riesgos=by(Riesgo.proceso_id) + by(Oportunidad.proceso_id),
    )


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> ProcesoOperacion:
    """Create a process; only ``proceso`` is required."""
    policy.require(actor, Action.CREATE, SPEC.resource)
    columns, wanted = _split(session, data)
    fields.require_keys(columns, REQUIRED_ON_CREATE)
    created = ProcesoOperacion(**_clean(session, columns))
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(SPEC.conflict) from exc
    wanted = wanted or set()
    row.after = _snapshot(created, wanted)  # still pending: set it before any other flush
    _relate(session, created.id_proceso, wanted, set())
    return created


def update(session: Session, actor: Actor, record_id: int,
           data: Mapping[str, Any]) -> ProcesoOperacion:
    """Apply the given fields; ``relacionados`` replaces the related processes."""
    policy.require(actor, Action.UPDATE, SPEC.resource)
    found = _load(session, record_id)
    columns, wanted = _split(session, data, found.id_proceso)
    values = _clean(session, columns, found)
    current = set(related_ids(session, found.id_proceso))
    changes = {key: value for key, value in values.items() if getattr(found, key) != value}
    if wanted == current:
        wanted = None
    if not changes and wanted is None:
        return found
    before = _snapshot(found, current)
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before,
                 after=_snapshot(found, current if wanted is None else wanted))
    if wanted is not None:
        _relate(session, found.id_proceso, wanted, current)
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(SPEC.conflict) from exc
    return found


def delete(session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete a process that nothing cites or relates to, keeping an audit snapshot.

    A process cited by quality objectives raises ``Conflict``
    (``CITED_BY_OBJECTIVES``), one cited by risks or opportunities
    ``CITED_BY_RISKS`` and one with relations ``HAS_RELATIONS``; so does one
    cited or related after the check, when the flush breaks one of those
    foreign keys and the database names it (PostgreSQL). A failed flush leaves
    the session unusable until rolled back, so nothing can be checked again:
    any other ``Conflict``, or one SQLite does not name, is raised as it came.
    The audit row holds the columns: a deletable process has no relations.
    """
    policy.require(actor, Action.DELETE, SPEC.resource)
    found = crud.get(SPEC, session, actor, record_id)
    if _cited_by_objectives(session, found.id_proceso):
        raise Conflict(CITED_BY_OBJECTIVES)
    if _cited_by_risks(session, found.id_proceso):
        raise Conflict(CITED_BY_RISKS)
    if _has_relations(session, found.id_proceso):
        raise Conflict(HAS_RELATIONS)
    try:
        crud.delete(SPEC, session, actor, found.id_proceso)
    except Conflict as exc:
        message = _CITED_BY.get(_violated_constraint(exc.__cause__))
        if message is not None:
            raise Conflict(message) from exc.__cause__
        raise


def _load(session: Session, record_id: int) -> ProcesoOperacion:
    found = session.get(ProcesoOperacion, record_id)
    if found is None:
        raise NotFound(SPEC.not_found)
    return found


def _split(session: Session, data: Any,
           own_id: int | None = None) -> tuple[dict[str, Any], set[int] | None]:
    """The column keys of ``data`` and the related ids (``None`` without ``relacionados``)."""
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)
    columns = {key: value for key, value in data.items() if key != RELATED}
    if RELATED not in data:
        return columns, None
    return columns, _related_ids(session, data[RELATED], own_id)


def _clean(session: Session, data: Mapping[str, Any],
           found: ProcesoOperacion | None = None) -> dict[str, Any]:
    """The column values of ``data``, validated by ``SPEC`` as ``crud`` does."""
    values = {f.name: f.clean(data, f.name) for f in SPEC.fields if f.name in data}
    for f in SPEC.fields:
        if f.check is not None and f.name in values:
            current = None if found is None else getattr(found, f.name)
            f.check(session, f.name, values[f.name], current)
    return values


def _related_ids(session: Session, value: Any, own_id: int | None) -> set[int]:
    """The processes named by ``value`` (``None`` means none); each must exist."""
    if value is None:
        return set()
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise ValidationError(RELATED_IDS)
    wanted = set(value)
    if own_id is not None and own_id in wanted:
        raise ValidationError(RELATED_ITSELF)
    searchable = [item for item in wanted if people.is_db_id(item)]
    found = set(session.scalars(select(ProcesoOperacion.id_proceso).where(
        ProcesoOperacion.id_proceso.in_(searchable)))) if searchable else set()
    missing = sorted(wanted - found)
    if missing:
        raise ValidationError(
            f"Procesos desconocidos en «relacionados»: {', '.join(map(str, missing))}.")
    return wanted


def _relate(session: Session, process_id: int, wanted: set[int], current: set[int]) -> None:
    """Drop the pairs ``wanted`` no longer holds and add the new ones, lower id first."""
    pairs = proceso_relaciones.c
    removed, added = current - wanted, wanted - current
    try:
        if removed:
            session.execute(proceso_relaciones.delete().where(or_(
                and_(pairs.proceso_id == process_id, pairs.relacionado_id.in_(removed)),
                and_(pairs.relacionado_id == process_id, pairs.proceso_id.in_(removed)))))
        if added:
            session.execute(proceso_relaciones.insert(), [
                {"proceso_id": min(process_id, other), "relacionado_id": max(process_id, other)}
                for other in sorted(added)])
    except IntegrityError as exc:  # e.g. a process deleted, or the pair added, meanwhile
        raise Conflict() from exc


def _snapshot(found: ProcesoOperacion, related: set[int]) -> dict[str, Any]:
    """Column values plus ``relacionados``, as stored on the process's audit rows."""
    return audit.snapshot(found) | {RELATED: sorted(related)}


def _cited_by_objectives(session: Session, process_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(ObjetivoCalidad.proceso_id == process_id))))


def _cited_by_risks(session: Session, process_id: int) -> bool:
    return any(session.scalar(select(exists().where(column == process_id)))
               for column in (Riesgo.proceso_id, Oportunidad.proceso_id))


def _has_relations(session: Session, process_id: int) -> bool:
    pairs = proceso_relaciones.c
    return bool(session.scalar(select(exists().where(
        or_(pairs.proceso_id == process_id, pairs.relacionado_id == process_id)))))


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to processes, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is ProcesoOperacion.__table__}
    return name


OBJECTIVES_FK = _foreign_key(ObjetivoCalidad)
RISKS_FKS = frozenset({_foreign_key(Riesgo), _foreign_key(Oportunidad)})
RELATIONS_FKS = frozenset(fk.constraint.name for fk in proceso_relaciones.foreign_keys)
# The message for each foreign key whose violation means "still cited" or "still related".
_CITED_BY = {OBJECTIVES_FK: CITED_BY_OBJECTIVES, **dict.fromkeys(RISKS_FKS, CITED_BY_RISKS),
             **dict.fromkeys(RELATIONS_FKS, HAS_RELATIONS)}


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
