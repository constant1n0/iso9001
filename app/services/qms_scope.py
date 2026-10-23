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

"""The scope of the quality management system (ISO 9001 clause 4.3, ``organization-context``).

Decisions OC3 and OC4. The scope is versioned (``AlcanceSGC``): the statement
(``declaracion``), the products and services, the sites (``centros``), notes,
and one applicability entry per requirement of the fixed ``CATALOGUE`` (clauses
4 to 10), stored in ``aplicabilidad_alcance``. A requirement either applies or
not; one that does not says why (``JUSTIFICATION_REQUIRED``).

- ``new_draft`` starts the next version (``version`` is the highest plus one) as
  a copy of the current version's texts and applicability, then applies the
  given changes. The first version has nothing to copy: every requirement
  applies and the statement is required (``STATEMENT_REQUIRED``). There is at
  most one draft (``ONE_DRAFT``).
- ``update_draft`` changes the texts and ``aplicabilidad``, a mapping
  ``{apartado: {"aplica": bool, "justificacion": str}}`` that gives only the
  requirements and keys it changes. Only a draft changes: current and obsolete
  versions never do (``IMMUTABLE``).
- ``approve`` is for administrators outside the ``mcp`` channel. The approving
  person is the one linked to the acting user (``APPROVER_REQUIRED`` without
  one); approving the draft one wrote is allowed. The current version becomes
  ``obsoleto`` from ``today`` and the draft ``vigente``, approved ``today``.
- ``delete_draft`` (administrators and auditors, never on the ``mcp`` channel)
  deletes a draft with its applicability; its number is free again.

Every role reads every version (``get``, ``current``, ``draft``, ``history``,
``list_page``, ``applicability``). Drafts are written by administrators and
auditors (policy resource ``ORGANIZATION_CONTEXT``). The applicability table
has no audit row of its own, so every audit row of a version carries
``aplicabilidad`` next to the columns when it changes. Like every service
this module flushes and never commits, an edit that changes nothing writes
nothing, and the version rows that change are locked (``FOR UPDATE``). Two
partial unique indexes keep one draft and one current version when two writes
race; the loser gets ``Conflict``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AlcanceSGC, EstadoAlcance, RoleEnum, aplicabilidad_alcance
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

RESOURCE = Resource.ORGANIZATION_CONTEXT
APPROVE_ROLES = frozenset({RoleEnum.ADMINISTRADOR})
E = EstadoAlcance

# Every requirement of clauses 4 to 10 an organization may declare applicable or
# not, at the level of ``docs/analisis-brechas-iso-9001.md``, with the Spanish
# titles of the standard (UNE-EN ISO 9001) as that analysis words them.
CATALOGUE: tuple[tuple[str, str], ...] = (
    ("4.1", "Comprensión de la organización y de su contexto"),
    ("4.2", "Comprensión de las necesidades y expectativas de las partes interesadas"),
    ("4.3", "Determinación del alcance del sistema de gestión de la calidad"),
    ("4.4", "Sistema de gestión de la calidad y sus procesos"),
    ("5.1", "Liderazgo y compromiso"),
    ("5.2", "Política"),
    ("5.3", "Roles, responsabilidades y autoridades en la organización"),
    ("6.1", "Acciones para abordar riesgos y oportunidades"),
    ("6.2", "Objetivos de la calidad y planificación para lograrlos"),
    ("6.3", "Planificación de los cambios"),
    ("7.1.1", "Recursos: generalidades"),
    ("7.1.2", "Personas"),
    ("7.1.3", "Infraestructura"),
    ("7.1.4", "Ambiente para la operación de los procesos"),
    ("7.1.5", "Recursos de seguimiento y medición"),
    ("7.1.6", "Conocimientos de la organización"),
    ("7.2", "Competencia"),
    ("7.3", "Toma de conciencia"),
    ("7.4", "Comunicación"),
    ("7.5", "Información documentada"),
    ("8.1", "Planificación y control operacional"),
    ("8.2", "Requisitos para los productos y servicios"),
    ("8.3", "Diseño y desarrollo de los productos y servicios"),
    ("8.4", "Control de los procesos, productos y servicios suministrados externamente"),
    ("8.5.1", "Control de la producción y de la provisión del servicio"),
    ("8.5.2", "Identificación y trazabilidad"),
    ("8.5.3", "Propiedad perteneciente a los clientes o proveedores externos"),
    ("8.5.4", "Preservación"),
    ("8.5.5", "Actividades posteriores a la entrega"),
    ("8.5.6", "Control de los cambios"),
    ("8.6", "Liberación de los productos y servicios"),
    ("8.7", "Control de las salidas no conformes"),
    ("9.1", "Seguimiento, medición, análisis y evaluación"),
    ("9.2", "Auditoría interna"),
    ("9.3", "Revisión por la dirección"),
    ("10.1", "Mejora: generalidades"),
    ("10.2", "No conformidad y acción correctiva"),
    ("10.3", "Mejora continua"),
)
_TITLES = dict(CATALOGUE)

TEXT_FIELDS = ("declaracion", "productos_servicios", "centros", "notas")
APPLICABILITY = "aplicabilidad"
DRAFT_WRITABLE = frozenset({*TEXT_FIELDS, APPLICABILITY})
_ENTRY_KEYS = frozenset({"aplica", "justificacion"})

NOT_FOUND = "Versión del alcance no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
STATEMENT_REQUIRED = (
    "La primera versión del alcance necesita su declaración («declaracion»).")
ONE_DRAFT = ("Ya hay un borrador del alcance; modifícalo o elimínalo antes de empezar "
             "otro.")
IMMUTABLE = "Una versión vigente u obsoleta del alcance no se puede modificar."
NOT_A_DRAFT = "Solo se puede aprobar un borrador del alcance."
ONLY_DRAFTS_DELETED = (
    "Solo se puede eliminar un borrador del alcance; las versiones aprobadas se conservan.")
APPROVER_REQUIRED = (
    "Tu usuario no está vinculado a ninguna persona; vincúlalo para aprobar el alcance.")
APPLICABILITY_NOT_A_MAPPING = (
    "El campo «aplicabilidad» debe ser un objeto con un apartado por clave, p. ej. "
    '{"8.3": {"aplica": false, "justificacion": "…"}}.')
UNKNOWN_REQUIREMENTS = (
    "Apartados desconocidos en «aplicabilidad»: {codes}. Se admiten los del catálogo, "
    "de 4.1 a 10.3.")
ENTRY_NOT_A_MAPPING = (
    "El apartado {code} de «aplicabilidad» debe ser un objeto con «aplica» y "
    "«justificacion».")
BAD_FLAG = "«aplica» del apartado {code} debe ser verdadero o falso."
BAD_JUSTIFICATION = "«justificacion» del apartado {code} debe ser texto."
JUSTIFICATION_REQUIRED = "Justifica por qué no aplica el apartado {code} ({title})."
BAD_FILTER = "El filtro «estado» no admite ese valor."
CONFLICT = "Otra versión del alcance ha cambiado a la vez; vuelve a intentarlo."

_READS = crud.Spec(
    model=AlcanceSGC,
    resource=RESOURCE,
    not_found=NOT_FOUND,
    fields=(),  # writes are bespoke (see ``DRAFT_WRITABLE``)
    order_by=(AlcanceSGC.version.desc(), AlcanceSGC.id.desc()),
)

Entries = dict[str, dict[str, Any]]  # {apartado: {"aplica": ..., "justificacion": ...}}


@dataclass(frozen=True)
class Requirement:
    """One requirement of the catalogue and whether it applies to a scope version."""

    apartado: str
    titulo: str
    aplica: bool
    justificacion: str | None


def catalogue() -> tuple[tuple[str, str], ...]:
    """The requirements a scope declares applicable or not: ``(code, title)``, in order."""
    return CATALOGUE


def may_approve(actor: Actor) -> bool:
    """Whether ``actor`` may approve a draft (role, scopes and channel)."""
    return (policy.can(actor, Action.UPDATE, RESOURCE) and actor.role in APPROVE_ROLES
            and actor.channel != "mcp")


# -- reads -----------------------------------------------------------------------------


def get(session: Session, actor: Actor, scope_id: Any) -> AlcanceSGC:
    """One version, or ``NotFound``."""
    policy.require(actor, Action.READ, RESOURCE)
    return _load(session, scope_id)


def current(session: Session, actor: Actor) -> AlcanceSGC | None:
    """The version in force (``vigente``), if any."""
    policy.require(actor, Action.READ, RESOURCE)
    return _in_state(session, E.vigente)


def draft(session: Session, actor: Actor) -> AlcanceSGC | None:
    """The version in preparation (``borrador``), if any."""
    policy.require(actor, Action.READ, RESOURCE)
    return _draft(session)


def history(session: Session, actor: Actor) -> list[AlcanceSGC]:
    """Every version, the newest first."""
    return crud.list_(_READS, session, actor)


def list_page(session: Session, actor: Actor, *, estado: Any = None, page: int = 1,
              per_page: int = crud.DEFAULT_PER_PAGE) -> tuple[list[AlcanceSGC], int]:
    """One page of ``history``, optionally of one ``estado`` (member or name), plus the total."""
    where = [] if not estado else [AlcanceSGC.estado == _state(estado)]
    return crud.list_page(_READS, session, actor, where, page=page, per_page=per_page)


def applicability(session: Session, actor: Actor, scope_id: Any) -> list[Requirement]:
    """The catalogue with whether each requirement applies to a version, in catalogue order."""
    found = get(session, actor, scope_id)
    return [Requirement(code, _TITLES.get(code, code), entry["aplica"], entry["justificacion"])
            for code, entry in applicability_of(session, found.id).items()]


def applicability_of(session: Session, scope_id: int) -> Entries:
    """``{apartado: {"aplica", "justificacion"}}`` of a version, in catalogue order.

    For adapters that already read the version (no policy check of its own).
    """
    table = aplicabilidad_alcance
    rows = session.execute(select(table.c.apartado, table.c.aplica, table.c.justificacion)
                           .where(table.c.alcance_id == scope_id))
    order = {code: index for index, (code, _) in enumerate(CATALOGUE)}
    return {code: {"aplica": aplica, "justificacion": justificacion}
            for code, aplica, justificacion in sorted(
                rows, key=lambda row: (order.get(row[0], len(order)), row[0]))}


# -- writes ----------------------------------------------------------------------------


def new_draft(session: Session, actor: Actor, data: Mapping[str, Any] | None = None
              ) -> AlcanceSGC:
    """Start the next version as a copy of the current one, with ``data``'s changes.

    ``data`` takes the keys of ``update_draft``; without a current version every
    requirement applies and ``declaracion`` is required.

    Raises:
        PermissionDenied: The actor may not write the scope.
        ValidationError: Bad data, a draft already exists (``ONE_DRAFT``) or the
            first version has no statement (``STATEMENT_REQUIRED``).
        Conflict: A concurrent draft won the race (``CONFLICT``).
    """
    policy.require(actor, Action.CREATE, RESOURCE)
    data = {} if data is None else _mapping(data)
    fields.reject_unknown(data, DRAFT_WRITABLE)
    texts = _texts(data)
    if _draft(session) is not None:
        raise ValidationError(ONE_DRAFT)
    base = _in_state(session, E.vigente)
    if base is None:
        values: dict[str, Any] = dict.fromkeys(TEXT_FIELDS)
        entries = {code: {"aplica": True, "justificacion": None} for code, _ in CATALOGUE}
    else:
        values = {key: getattr(base, key) for key in TEXT_FIELDS}
        copied = applicability_of(session, base.id)
        entries = {code: copied.get(code, {"aplica": True, "justificacion": None})
                   for code, _ in CATALOGUE}
    values |= texts
    entries = _applied(data, entries)
    if values["declaracion"] is None:
        raise ValidationError(STATEMENT_REQUIRED)
    last = session.scalar(select(func.max(AlcanceSGC.version))) or 0
    created = AlcanceSGC(version=last + 1, estado=E.borrador, **values)
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:  # a concurrent draft or version number
        raise Conflict(CONFLICT) from exc
    row.after = _snapshot(created, entries)  # the row is still pending: add the entries
    session.execute(insert(aplicabilidad_alcance), [
        {"alcance_id": created.id, "apartado": code, **entry}
        for code, entry in entries.items()])
    return created


def update_draft(session: Session, actor: Actor, scope_id: Any,
                 data: Mapping[str, Any]) -> AlcanceSGC:
    """Change a draft's texts and ``aplicabilidad``; a no-op writes nothing.

    Raises:
        PermissionDenied: The actor may not write the scope.
        NotFound: Unknown version.
        ValidationError: Bad data, or a current or obsolete version (``IMMUTABLE``).
    """
    policy.require(actor, Action.UPDATE, RESOURCE)
    data = _mapping(data)
    fields.reject_unknown(data, DRAFT_WRITABLE)
    found = _locked(session, scope_id)
    if found.estado is not E.borrador:
        raise ValidationError(IMMUTABLE)
    texts = _texts(data)
    entries = applicability_of(session, found.id)
    wanted = _applied(data, entries)
    changes = {key: value for key, value in texts.items() if getattr(found, key) != value}
    changed_entries = {code: entry for code, entry in wanted.items() if entries.get(code) != entry}
    if not changes and not changed_entries:
        return found
    before = _snapshot(found, entries)
    table = aplicabilidad_alcance
    for code, entry in changed_entries.items():
        session.execute(update(table).where(table.c.alcance_id == found.id,
                                            table.c.apartado == code).values(**entry))
    for key, value in changes.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before,
                 after=_snapshot(found, wanted))
    _flush(session)
    return found


def approve(session: Session, actor: Actor, scope_id: Any, *, today: date) -> AlcanceSGC:
    """Put a draft in force from ``today``; the current version becomes obsolete that day.

    Administrators only, never on the ``mcp`` channel. The approving person is
    the one linked to the acting user, who may be the draft's author.

    Raises:
        PermissionDenied: The actor may not approve.
        NotFound: Unknown version.
        ValidationError: Not a draft (``NOT_A_DRAFT``), or the acting user has
            no person (``APPROVER_REQUIRED``).
        ValueError: ``today`` is not a ``date`` (a programming error).
        Conflict: The database refused the change (``CONFLICT``).
    """
    if not may_approve(actor):
        raise PermissionDenied()
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("The approval date must come from the adapter.")
    found = _locked(session, scope_id)
    if found.estado is not E.borrador:
        raise ValidationError(NOT_A_DRAFT)
    approver = people.of_user(session, actor.user_id)  # queries: before any change
    if approver is None:
        raise ValidationError(APPROVER_REQUIRED)
    previous = session.scalars(
        select(AlcanceSGC).where(AlcanceSGC.estado == E.vigente).with_for_update()
        .execution_options(populate_existing=True)).one_or_none()
    if previous is not None:  # first, so the two never hold "vigente" at once
        _change(session, actor, previous, estado=E.obsoleto, fecha_obsolescencia=today)
    _change(session, actor, found, estado=E.vigente, aprobado_por_id=approver,
            fecha_aprobacion=today)
    return found


def delete_draft(session: Session, actor: Actor, scope_id: Any) -> None:
    """Delete a draft and its applicability, keeping both in the audit row.

    Administrators and auditors (the scope's writers), never on the ``mcp`` channel.
    """
    policy.require(actor, Action.UPDATE, RESOURCE)
    if actor.channel == "mcp":
        raise PermissionDenied()  # an agent never deletes (OC4)
    found = _locked(session, scope_id)
    if found.estado is not E.borrador:
        raise ValidationError(ONLY_DRAFTS_DELETED)
    audit.record(session, actor, "delete", found,
                 before=_snapshot(found, applicability_of(session, found.id)))
    table = aplicabilidad_alcance
    session.execute(delete(table).where(table.c.alcance_id == found.id))
    session.delete(found)
    _flush(session)


# -- helpers ---------------------------------------------------------------------------


def _mapping(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    return data


def _texts(data: Mapping[str, Any]) -> dict[str, Any]:
    """The given texts, validated; ``declaracion`` is never blank."""
    return {key: fields.text(data, key, required=key == "declaracion", strip=False)
            for key in TEXT_FIELDS if key in data}


def _applied(data: Mapping[str, Any], entries: Entries) -> Entries:
    """``entries`` with ``data``'s ``aplicabilidad`` applied, every rule checked."""
    if APPLICABILITY not in data:
        return entries
    given = data[APPLICABILITY]
    if not isinstance(given, Mapping):
        raise ValidationError(APPLICABILITY_NOT_A_MAPPING)
    unknown = [code for code in given if code not in _TITLES]
    if unknown:
        raise ValidationError(UNKNOWN_REQUIREMENTS.format(codes=", ".join(map(str, unknown))))
    result = dict(entries)
    for code, change in given.items():
        if not isinstance(change, Mapping) or not set(change) <= _ENTRY_KEYS:
            raise ValidationError(ENTRY_NOT_A_MAPPING.format(code=code))
        entry = dict(result[code])
        if "aplica" in change:
            if not isinstance(change["aplica"], bool):
                raise ValidationError(BAD_FLAG.format(code=code))
            entry["aplica"] = change["aplica"]
        if "justificacion" in change:
            reason = change["justificacion"]
            if reason is not None and not isinstance(reason, str):
                raise ValidationError(BAD_JUSTIFICATION.format(code=code))
            entry["justificacion"] = (reason or "").strip() or None  # blank is none
        if not entry["aplica"] and entry["justificacion"] is None:
            raise ValidationError(JUSTIFICATION_REQUIRED.format(code=code,
                                                                title=_TITLES[code]))
        result[code] = entry
    return result


def _snapshot(found: AlcanceSGC, entries: Entries) -> dict[str, Any]:
    """Column values plus ``aplicabilidad``, as stored on the version's audit rows."""
    return audit.snapshot(found) | {APPLICABILITY: entries}


def _change(session: Session, actor: Actor, found: AlcanceSGC, **values: Any) -> None:
    """Set ``values`` on a version, then stamp, audit and flush it."""
    before = audit.snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    _flush(session)


def _load(session: Session, scope_id: Any) -> AlcanceSGC:
    found = session.get(AlcanceSGC, scope_id) if people.is_db_id(scope_id) else None
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _locked(session: Session, scope_id: Any) -> AlcanceSGC:
    """The version loaded afresh with its row locked (``FOR UPDATE``), or ``NotFound``."""
    found = None
    if people.is_db_id(scope_id):
        found = session.scalars(
            select(AlcanceSGC).where(AlcanceSGC.id == scope_id).with_for_update()
            .execution_options(populate_existing=True)).one_or_none()
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _in_state(session: Session, estado: EstadoAlcance) -> AlcanceSGC | None:
    return session.scalars(select(AlcanceSGC).where(AlcanceSGC.estado == estado)).one_or_none()


def _draft(session: Session) -> AlcanceSGC | None:
    return _in_state(session, E.borrador)


def _state(value: Any) -> EstadoAlcance:
    """A filter value given as the member or its name."""
    try:
        return fields.enum_member({"estado": value}, "estado", EstadoAlcance)
    except ValidationError:
        raise ValidationError(BAD_FILTER) from None


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(CONFLICT) from exc
