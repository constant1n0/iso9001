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

"""Audit (``Auditoria``) service, following ``nonconformities``.

Authorization comes from ``policy`` (resource ``AUDITS``); validation mirrors
``AuditoriaForm``. Services flush and never commit. ``auditor_id`` cites the
lead auditor (``personas``, see ``people.check_reference``); ``auditor`` keeps the
legacy free-text name. ``auditor`` is required unless a person is cited: a
write that leaves it blank takes the person's name (``people.fill_name``), so
it is not in ``REQUIRED_ON_CREATE`` (which the MCP registry mirrors) but
``create`` requires it once that name is filled in.

Planning fields (decisions AP2 and AP3 of ``audit-programme``), all optional:

- ``programa_id`` puts the audit in a programme (``audit_programmes``); the
  audit's date must fall in the programme's year. The programme row is read
  with a shared lock so its year cannot change meanwhile.
- ``alcance`` and ``criterios`` are free text; ``responsable_area_id`` cites the
  audited-area owner.
- ``equipo_ids`` is a list of people that replaces the team. The association
  table has no audit row of its own, so every audit row carries ``equipo_ids``
  next to the columns; a record exposes ``equipo`` and ``equipo_ids``.
- Independence: neither the lead auditor nor any team member may be the
  audited-area owner (``ValidationError``).

People must exist; an inactive person is refused only when newly chosen.

Findings (``audit_findings``, decision AP4) hold links to their
nonconformities, so an audit with findings cannot be deleted: ``delete``
raises ``Conflict`` asking to delete them first, and the foreign key
(``ON DELETE RESTRICT``) refuses one recorded after that check. When the
database refuses a delete, the message mentions findings only if the refusal
names that foreign key (``FINDINGS_FK``, read from the model; PostgreSQL names
it, SQLite names no constraint); otherwise it is ``DELETE_CONFLICT``. Nor is an
audit that a change to the QMS names as its origin deleted
(``CITED_BY_CHANGES``, CP1 of ``change-planning``; ``CHANGES_FK`` likewise).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Auditoria, CambioSGC, EstadoAuditoriaEnum, HallazgoAuditoria, Person, ProgramaAuditoria,
)
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

UNKNOWN_PROGRAMME = "El campo «programa_id» no corresponde a ningún programa de auditoría."
TEAM_IDS = "El campo «equipo_ids» debe ser una lista de identificadores de persona."
LEAD_OWNS_AREA = "El auditor jefe no puede ser el responsable del área auditada."
TEAM_OWNS_AREA = "Ningún miembro del equipo auditor puede ser el responsable del área auditada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
HAS_FINDINGS = (
    "No se puede eliminar una auditoría que tiene hallazgos; elimina antes sus hallazgos."
)
DELETE_CONFLICT = (
    "No se ha podido eliminar la auditoría porque sus datos han cambiado entretanto; "
    "vuelve a intentarlo."
)
CITED_BY_CHANGES = "No se puede eliminar una auditoría a la que se refieren cambios en el SGC."


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to audits, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is Auditoria.__table__}
    return name


FINDINGS_FK = _foreign_key(HallazgoAuditoria)
CHANGES_FK = _foreign_key(CambioSGC)


def get(session: Session, actor: Actor, audit_id: int) -> Auditoria:
    """Return one audit or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.AUDITS)
    return _load(session, audit_id)


def _load(session: Session, audit_id: int) -> Auditoria:
    found = session.get(Auditoria, audit_id)
    if found is None:
        raise NotFound("Auditoría no encontrada.")
    return found


def list_page(
    session: Session,
    actor: Actor,
    *,
    area: str | None = None,
    auditor: str | None = None,
    estado: EstadoAuditoriaEnum | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    programa_id: int | None = None,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[Auditoria], int]:
    """One page of audits in id order plus the total matching the filters."""
    policy.require(actor, Action.READ, Resource.AUDITS)
    conditions = []
    if programa_id is not None:
        conditions.append(Auditoria.programa_id == programa_id)
    if area:
        conditions.append(Auditoria.area_auditada.ilike(f"%{area}%"))
    if auditor:
        conditions.append(Auditoria.auditor.ilike(f"%{auditor}%"))
    if estado:
        conditions.append(Auditoria.estado == estado)
    if fecha_inicio:
        conditions.append(Auditoria.fecha >= fecha_inicio)
    if fecha_fin:
        conditions.append(Auditoria.fecha <= fecha_fin)
    page, per_page = crud.page_bounds(page, per_page, default_per_page=10)
    total = session.scalar(select(func.count()).select_from(Auditoria).where(*conditions))
    query = (
        select(Auditoria)
        .where(*conditions)
        .order_by(Auditoria.id)
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return list(session.scalars(query)), total


# Audits that will not be held any more: they are no longer "upcoming".
_NOT_TO_BE_HELD = (EstadoAuditoriaEnum.COMPLETADA, EstadoAuditoriaEnum.CANCELADA)


def upcoming(
    session: Session, actor: Actor, *, today: date, within_days: int
) -> list[Auditoria]:
    """Audits still to be held from ``today`` to ``within_days`` days ahead, by date.

    Both ends are included; completed and cancelled audits are left out.
    ``today`` is the adapter's date. Read grant of ``AUDITS``.
    """
    policy.require(actor, Action.READ, Resource.AUDITS)
    query = (
        select(Auditoria)
        .where(
            Auditoria.fecha.between(today, today + timedelta(days=within_days)),
            Auditoria.estado.not_in(_NOT_TO_BE_HELD),
        )
        .order_by(Auditoria.fecha, Auditoria.id)
    )
    return list(session.scalars(query))


AREA_MAX = 50  # mirrors Auditoria.area_auditada and the web form
AUDITOR_MAX = 50  # mirrors Auditoria.auditor and the web form
WRITABLE_FIELDS = frozenset({
    "area_auditada", "fecha", "auditor", "auditor_id", "resultado", "accion_correctiva",
    "estado", "programa_id", "alcance", "criterios", "responsable_area_id", "equipo_ids",
})
REQUIRED_ON_CREATE = frozenset({"area_auditada", "fecha", "resultado"})
NAME_OR_PERSON = "auditor"  # required on create unless ``auditor_id`` fills it


def _clean(
    session: Session, data: Mapping[str, Any], current: Auditoria | None = None
) -> tuple[dict[str, Any], list[Person] | None]:
    """Validated column values and the team (``None`` when ``equipo_ids`` is absent).

    ``current`` is the audit being updated (``None`` when creating). The
    programme year and independence rules run on the result (``_check``).
    """
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    fields.reject_unknown(data, WRITABLE_FIELDS)
    clean: dict[str, Any] = {}
    if "area_auditada" in data:
        clean["area_auditada"] = fields.text(
            data, "area_auditada", required=True, max_length=AREA_MAX
        )
    if "fecha" in data:
        clean["fecha"] = fields.required_date(data, "fecha")
    if "auditor" in data:
        clean["auditor"] = fields.text(data, "auditor", required=True, max_length=AUDITOR_MAX)
    if "resultado" in data:
        clean["resultado"] = fields.text(data, "resultado", required=True)
    if "accion_correctiva" in data:
        clean["accion_correctiva"] = fields.text(data, "accion_correctiva", strip=False)
    if "estado" in data:
        clean["estado"] = fields.enum_member(data, "estado", EstadoAuditoriaEnum)
    for key in ("alcance", "criterios"):
        if key in data:
            clean[key] = fields.text(data, key, strip=False)
    if "programa_id" in data:
        clean["programa_id"] = fields.integer(data, "programa_id")
    # Last: these query the database.
    for key in ("auditor_id", "responsable_area_id"):
        if key in data:
            clean[key] = people.reference(session, data, key, getattr(current, key, None))
    team = None
    if "equipo_ids" in data:
        team = _team(session, data["equipo_ids"],
                     set(current.equipo_ids) if current is not None else set())
    return clean, team


def _team(session: Session, value: Any, current: set[int]) -> list[Person]:
    """The people named by ``value``: known ids (duplicates ignored), active when new."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise ValidationError(TEAM_IDS)
    wanted = set(value)
    searchable = [item for item in wanted if people.is_db_id(item)]
    found = list(session.scalars(
        select(Person).where(Person.id.in_(searchable)).order_by(Person.nombre, Person.id)
    )) if searchable else []
    missing = sorted(wanted - {person.id for person in found})
    if missing:
        raise ValidationError(
            f"Personas desconocidas en «equipo_ids»: {', '.join(map(str, missing))}."
        )
    inactive = [person.nombre for person in found
                if not person.activo and person.id not in current]
    if inactive:
        raise ValidationError(
            f"Personas desactivadas en «equipo_ids»: {', '.join(inactive)}; "
            "elige personas activas."
        )
    return found


def _check(
    session: Session, values: Mapping[str, Any], team: list[Person] | None,
    current: Auditoria | None = None,
) -> None:
    """The rules across fields, on the values the audit will hold after the write."""

    def final(key: str) -> Any:
        return values[key] if key in values else getattr(current, key, None)

    owner = final("responsable_area_id")
    if owner is not None:
        if final("auditor_id") == owner:
            raise ValidationError(LEAD_OWNS_AREA)
        members = ({person.id for person in team} if team is not None
                   else set(current.equipo_ids) if current is not None else set())
        if owner in members:
            raise ValidationError(TEAM_OWNS_AREA)
    programme_id = final("programa_id")
    if programme_id is not None and ("programa_id" in values or "fecha" in values):
        anio = _programme_year(session, programme_id)
        fecha = final("fecha")
        if fecha is not None and fecha.year != anio:
            raise ValidationError(
                f"La fecha de la auditoría debe estar en {anio}, el año de su programa."
            )


def _programme_year(session: Session, programme_id: int) -> int:
    """The programme's year, its row read with a shared lock (``FOR SHARE``)."""
    anio = None
    if people.is_db_id(programme_id):
        anio = session.scalar(
            select(ProgramaAuditoria.anio).where(ProgramaAuditoria.id == programme_id)
            .with_for_update(read=True)
        )
    if anio is None:
        raise ValidationError(UNKNOWN_PROGRAMME)
    return anio


def _snapshot(found: Auditoria) -> dict[str, Any]:
    """Column values plus ``equipo_ids``, as stored on the audit's audit rows."""
    return audit.snapshot(found) | {"equipo_ids": found.equipo_ids}


def _fill_auditor(
    session: Session, data: Mapping[str, Any], current: Auditoria | None = None
) -> Mapping[str, Any]:
    """``data`` with a blank ``auditor`` taken from the linked person."""
    return people.fill_name(session, data, current, link="auditor_id", text="auditor",
                            max_length=AUDITOR_MAX)


def _flush(session: Session, message: str | None = None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _has_findings(session: Session, audit_id: int) -> bool:
    return bool(session.scalar(
        select(exists().where(HallazgoAuditoria.auditoria_id == audit_id))))


def _cited_by_changes(session: Session, audit_id: int) -> bool:
    return bool(session.scalar(select(exists().where(CambioSGC.auditoria_id == audit_id))))


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> Auditoria:
    """Create an audit; area, date, result and the auditor (name or person) are required."""
    policy.require(actor, Action.CREATE, Resource.AUDITS)
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    data = _fill_auditor(session, data)
    fields.require_keys(data, REQUIRED_ON_CREATE | {NAME_OR_PERSON})
    values, team = _clean(session, data)
    _check(session, values, team)
    created = Auditoria(**({"estado": EstadoAuditoriaEnum.PENDIENTE} | values),
                        equipo=team or [])
    stamp_created(created, actor)
    session.add(created)
    try:
        row = audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict() from exc
    row.after = _snapshot(created)  # the row is still pending: add the team
    return created


def update(
    session: Session, actor: Actor, audit_id: int, data: Mapping[str, Any]
) -> Auditoria:
    """Apply the given fields; ``equipo_ids`` replaces the team.

    A call that changes nothing writes nothing.
    """
    policy.require(actor, Action.UPDATE, Resource.AUDITS)
    found = _load(session, audit_id)
    values, team = _clean(session, _fill_auditor(session, data, found), current=found)
    if team is not None and {person.id for person in team} == set(found.equipo_ids):
        team = None
    if team is None and all(getattr(found, key) == value for key, value in values.items()):
        return found
    _check(session, values, team, found)
    before = _snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    if team is not None:
        found.equipo = team
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before, after=_snapshot(found))
    _flush(session)
    return found


def delete(session: Session, actor: Actor, audit_id: int) -> None:
    """Hard-delete an audit without findings that no change to the QMS cites.

    Its full snapshot stays in the audit log.
    """
    policy.require(actor, Action.DELETE, Resource.AUDITS)
    found = _load(session, audit_id)
    if _has_findings(session, found.id):
        raise Conflict(HAS_FINDINGS)
    if _cited_by_changes(session, found.id):
        raise Conflict(CITED_BY_CHANGES)
    audit.record(session, actor, "delete", found, before=_snapshot(found))
    session.delete(found)
    try:
        session.flush()
    except IntegrityError as exc:  # e.g. a finding recorded after the check
        message = {FINDINGS_FK: HAS_FINDINGS, CHANGES_FK: CITED_BY_CHANGES}.get(
            _violated_constraint(exc), DELETE_CONFLICT)
        raise Conflict(message) from exc
