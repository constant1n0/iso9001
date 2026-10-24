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

"""Context issues of the organization (ISO 9001 clause 4.1, ``organization-context``).

Decisions OC1, OC4 and OC5. One register, ``issues`` (``CuestionContexto``),
with ``get``, ``list_``, ``list_page``, ``create``, ``update`` and ``delete``
like ``quality_objectives``, under the policy resource ``ORGANIZATION_CONTEXT``:
every role reads, administrators and auditors create and update,
administrators delete, and the ``mcp`` channel never deletes. ``create`` and
``update`` take ``today``, the adapter's local date (services never read the
clock), and so do the lists whose ``revision_vencida`` filter is used.

- Choices are member names. ``categoria`` matches ``tipo``
  (``models.CATEGORIAS_POR_TIPO``, ``CATEGORY_MISMATCH``): an external issue is
  political, economic, social, technological, legal or environmental; an
  internal one concerns culture, resources, knowledge, performance or structure.
- ``pertinente`` is required, and an issue that is not relevant says why in
  ``justificacion_pertinencia`` (``JUSTIFICATION_REQUIRED``); a relevant one may
  keep a justification too. ``cambio_climatico``, false unless set, records that
  the issue concerns climate change.
- The owner (``responsable_id``) is optional: a person, active when newly chosen.
- ``fecha_identificacion`` is required and never after ``today``;
  ``fecha_revision``, the next review, is never before it. ``estado`` starts
  ``vigente``; ``superada`` marks an issue that no longer applies.
- An issue that risks or opportunities cite (their ``cuestion_id``) is never
  deleted (``CITED_BY_RISKS``), nor one that a change to the QMS names as its
  origin (``CITED_BY_CHANGES``, CP1 of ``change-planning``). Their ``ON DELETE
  RESTRICT`` foreign keys refuse a citation the checks did not see, and
  PostgreSQL names them, so the message still says why.
- Filters: ``tipo``, ``categoria``, ``pertinente``, ``cambio_climatico``,
  ``estado``, ``responsable_id`` and ``revision_vencida`` (a ``vigente`` issue
  whose next review is before ``today``); by type, category and title.

``linked`` and ``linked_ids`` read the risks and opportunities an issue gives
rise to, ``attention`` what the dashboard flags and ``changes`` what a
management review takes as input 9.3.2 b (OC5). Every rule judges the record as
the write would leave it. An unknown key is refused before anything is looked
up, and an edit that changes nothing writes nothing. Like every service this
module flushes and never commits, writes an audit row next to each change, and
locks the rows that change (``FOR UPDATE``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from typing import Any

from sqlalchemy import and_, exists, false, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    CATEGORIAS_POR_TIPO, CambioSGC, CategoriaCuestion, CuestionContexto, EfectoCuestion,
    EstadoCuestion, Oportunidad, ParteInteresada, Riesgo, TipoContexto,
)
from . import crud, fields, people, policy, risks, stakeholders
from .actor import Actor
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource
from .quality_objectives import Register

RESOURCE = Resource.ORGANIZATION_CONTEXT
TITLE_MAX = 200  # mirrors ``titulo``


def _prose(members: Iterable[Any]) -> str:
    """The members' labels in lower case, as a Spanish list: "a, b o c"."""
    labels = [member.value.lower() for member in members]
    return f"{', '.join(labels[:-1])} o {labels[-1]}"


ISSUE_NOT_FOUND = "Cuestión del contexto no encontrada."
CATEGORY_MISMATCH = (
    "La categoría no corresponde al tipo: una cuestión externa es "
    f"{_prose(CATEGORIAS_POR_TIPO[TipoContexto.externa])}, y una interna, de "
    f"{_prose(CATEGORIAS_POR_TIPO[TipoContexto.interna])}.")
JUSTIFICATION_REQUIRED = (
    "Indica por qué la cuestión no es pertinente («justificacion_pertinencia»).")
IDENTIFIED_IN_FUTURE = "La fecha de identificación no puede ser futura."
REVIEW_BEFORE_IDENTIFICATION = (
    "La fecha de revisión no puede ser anterior a la fecha de identificación.")
CITED_BY_RISKS = (
    "No se puede eliminar una cuestión del contexto a la que se refieren riesgos u "
    "oportunidades; quítala antes de esos registros.")
CITED_BY_CHANGES = (
    "No se puede eliminar una cuestión del contexto a la que se refieren cambios en el SGC.")
DELETE_CONFLICT = (
    "No se ha podido eliminar la cuestión del contexto porque sus datos han cambiado "
    "entretanto; vuelve a intentarlo.")
BAD_FILTER = "El filtro «{key}» no admite ese valor."
BAD_FLAG = "El filtro «{key}» debe ser verdadero o falso."


# -- field validators ------------------------------------------------------------------


def _required_flag(data: Mapping[str, Any], key: str) -> bool:
    """A real boolean for a column that is never NULL."""
    value = data[key]
    if not isinstance(value, bool):
        raise ValidationError(f"El campo «{key}» debe ser verdadero o falso.")
    return value


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


SPEC = crud.Spec(
    model=CuestionContexto,
    resource=RESOURCE,
    not_found=ISSUE_NOT_FOUND,
    fields=(
        crud.Field("tipo", partial(fields.enum_member, enum_cls=TipoContexto), required=True),
        crud.Field("categoria", partial(fields.enum_member, enum_cls=CategoriaCuestion),
                   required=True),
        crud.Field("titulo", partial(fields.text, required=True, max_length=TITLE_MAX),
                   required=True),
        crud.Field("descripcion", partial(fields.text, required=True, strip=False),
                   required=True),
        crud.Field("efecto", partial(fields.enum_member, enum_cls=EfectoCuestion),
                   required=True),
        crud.Field("pertinente", _required_flag, required=True),
        crud.Field("justificacion_pertinencia", partial(fields.text, strip=False)),
        crud.Field("cambio_climatico", _required_flag),
        crud.Field("responsable_id", fields.integer, check=people.check_reference),
        crud.Field("fecha_identificacion", fields.required_date, required=True),
        crud.Field("fecha_revision", _optional_date),
        crud.Field("estado", partial(fields.enum_member, enum_cls=EstadoCuestion)),
        crud.Field("notas", partial(fields.text, strip=False)),
    ),
    # By type (externa first), category and title.
    order_by=(CuestionContexto.tipo, CuestionContexto.categoria, CuestionContexto.titulo,
              CuestionContexto.id),
)

_FIELDS = {f.name: f for f in SPEC.fields}


# -- writes ----------------------------------------------------------------------------


def _create(session: Session, actor: Actor, data: Mapping[str, Any], *,
            today: date) -> CuestionContexto:
    """Create an issue; it starts ``vigente`` unless ``estado`` says otherwise."""
    policy.require(actor, Action.CREATE, RESOURCE)
    today = _today(today)
    _reject_unknown(data)
    _check_issue(data, None, today)
    return crud.create(SPEC, session, actor, data)


def _update(session: Session, actor: Actor, issue_id: int, data: Mapping[str, Any], *,
            today: date) -> CuestionContexto:
    """Apply the given fields; the issue is locked (``FOR UPDATE``) while it changes."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    today = _today(today)
    _reject_unknown(data)
    found = _locked(session, issue_id)
    if _changed(found, data):  # a no-op is left to ``crud``, which writes nothing
        _check_issue(data, found, today)
    return crud.update(SPEC, session, actor, found.id, data)


def _delete(session: Session, actor: Actor, issue_id: int) -> None:
    """Hard-delete an issue no risk, opportunity or change cites, keeping its audit snapshot.

    A citation added after the checks meets a foreign key at the flush;
    PostgreSQL names it, so the message still says why.
    """
    policy.require(actor, Action.DELETE, RESOURCE)
    found = _locked(session, issue_id)
    if _is_cited(session, found.id):
        raise Conflict(CITED_BY_RISKS)
    if _cited_by_changes(session, found.id):
        raise Conflict(CITED_BY_CHANGES)
    try:
        crud.delete(SPEC, session, actor, found.id)
    except Conflict as exc:
        cause = exc.__cause__
        message = _CITING_KEYS.get(_violated_constraint(cause), DELETE_CONFLICT)
        raise Conflict(message) from cause


def _check_issue(data: Any, found: CuestionContexto | None, today: date) -> None:
    """The rules an issue keeps (OC1), on the values the write leaves.

    Data ``crud`` refuses anyway (not a mapping, unknown keys) is left to it, and
    so are missing required keys: a rule needs both of its values.
    """
    if not _well_formed(data):
        return
    final = partial(_final, data, found)
    if "tipo" in data or "categoria" in data:
        tipo, categoria = final("tipo"), final("categoria")
        if tipo is not None and categoria is not None and (
                categoria not in CATEGORIAS_POR_TIPO[tipo]):
            raise ValidationError(CATEGORY_MISMATCH)
    if ("pertinente" in data or "justificacion_pertinencia" in data) and (
            final("pertinente") is False and final("justificacion_pertinencia") is None):
        raise ValidationError(JUSTIFICATION_REQUIRED)
    if "fecha_identificacion" in data and final("fecha_identificacion") > today:
        raise ValidationError(IDENTIFIED_IN_FUTURE)
    identified, review = final("fecha_identificacion"), final("fecha_revision")
    if review is not None and identified is not None and review < identified:
        raise ValidationError(REVIEW_BEFORE_IDENTIFICATION)


# -- reads -----------------------------------------------------------------------------


def overdue(today: date) -> Any:
    """SQL: a ``vigente`` issue whose next review is before ``today``; never NULL."""
    return and_(CuestionContexto.estado == EstadoCuestion.vigente,
                CuestionContexto.fecha_revision.is_not(None),
                CuestionContexto.fecha_revision < today)


def revision_vencida(issue: CuestionContexto, *, today: date) -> bool:
    """Whether a ``vigente`` issue's next review (``fecha_revision``) is before ``today``."""
    return (issue.estado is EstadoCuestion.vigente and issue.fecha_revision is not None
            and issue.fecha_revision < _today(today))


def _conditions(tipo: Any = None, categoria: Any = None, pertinente: bool | None = None,
                cambio_climatico: bool | None = None, estado: Any = None,
                responsable_id: int | None = None, revision_vencida: bool | None = None,
                today: date | None = None) -> list[Any]:
    """Issue filters (AND); ``revision_vencida`` needs ``today``, the adapter's date."""
    where: list[Any] = []
    for column, value, enum_cls, key in (
            (CuestionContexto.tipo, tipo, TipoContexto, "tipo"),
            (CuestionContexto.categoria, categoria, CategoriaCuestion, "categoria"),
            (CuestionContexto.estado, estado, EstadoCuestion, "estado")):
        if value:
            where.append(column == _member(value, enum_cls, key))
    for column, value, key in ((CuestionContexto.pertinente, pertinente, "pertinente"),
                               (CuestionContexto.cambio_climatico, cambio_climatico,
                                "cambio_climatico")):
        if value is not None:
            where.append(column.is_(_flag(value, key)))
    if responsable_id is not None:
        where.append(CuestionContexto.responsable_id == responsable_id
                     if people.is_db_id(responsable_id) else false())
    if revision_vencida is not None:
        late = overdue(_today(today))
        where.append(late if _flag(revision_vencida, "revision_vencida") else not_(late))
    return where


issues = Register(
    SPEC=SPEC,
    conditions=_conditions,
    create=_create,
    update=_update,
    delete=_delete,
)


def linked(session: Session, actor: Actor, issue_id: int) -> dict[str, list[Any]]:
    """The risks (``riesgos``) and opportunities (``oportunidades``) the issue gives rise to.

    Each in its register's order. Read grants of the issues and of the risks.
    """
    found = issues.get(session, actor, issue_id)
    policy.require(actor, Action.READ, Resource.RISKS_OPPORTUNITIES)
    return {
        "riesgos": list(session.scalars(select(Riesgo).where(Riesgo.cuestion_id == found.id)
                                        .order_by(*risks.RISK_SPEC.order_by))),
        "oportunidades": list(session.scalars(
            select(Oportunidad).where(Oportunidad.cuestion_id == found.id)
            .order_by(*risks.OPPORTUNITY_SPEC.order_by))),
    }


def linked_ids(session: Session, issue_id: int) -> dict[str, list[int]]:
    """The ids of the risks and opportunities citing the issue, ascending.

    For adapters that already read the issue (no policy check of its own).
    """
    return {key: list(session.scalars(select(model.id).where(model.cuestion_id == issue_id)
                                      .order_by(model.id)))
            for key, model in (("riesgos", Riesgo), ("oportunidades", Oportunidad))}


def attention(session: Session, actor: Actor, *, today: date) -> dict[str, list[Any]]:
    """What the dashboard flags on ``today`` (decision OC5), the earliest review first.

    - ``cuestiones_revision_vencida``: ``vigente`` issues whose next review is
      before ``today``.
    - ``partes_revision_vencida``: interested parties whose next review is before
      ``today``, whatever their relevance.

    Read grants of the issues and of the interested parties.
    """
    policy.require(actor, Action.READ, RESOURCE)
    policy.require(actor, Action.READ, Resource.INTERESTED_PARTIES)
    today = _today(today)
    return {
        "cuestiones_revision_vencida": list(session.scalars(
            select(CuestionContexto).where(overdue(today))
            .order_by(CuestionContexto.fecha_revision, CuestionContexto.id))),
        "partes_revision_vencida": list(session.scalars(
            select(ParteInteresada).where(stakeholders.overdue(today))
            .order_by(ParteInteresada.fecha_revision, ParteInteresada.id_interesado))),
    }


@dataclass(frozen=True)
class ContextChanges:
    """The context issues a management review of a period takes as input 9.3.2 b (OC5).

    "In the period" means from ``desde`` to ``hasta``, both included, with the
    record stamps (``created_at``, ``updated_at``) counted by UTC day, as the
    management review counts the other registers. The lists are in the
    register's order (type, category, title); the counts describe the register
    now, whatever the period.
    """

    nuevas: list[CuestionContexto]  # created in the period
    # Created before the period (or with no creation stamp) and changed in it.
    modificadas: list[CuestionContexto]
    # ``superada`` now, with the latest change in the period: an issue is
    # superseded by an update, so this is when it was, unless it changed again
    # later. They also appear in ``nuevas`` or ``modificadas``.
    superadas: list[CuestionContexto]
    vigentes: int  # every ``vigente`` issue
    pertinentes: int  # ``vigente`` and relevant
    cambio_climatico: int  # ``vigente`` and concerning climate change, relevant or not
    cambio_climatico_pertinentes: int  # ``vigente``, relevant and concerning climate change


def changes(session: Session, desde: date, hasta: date) -> ContextChanges:
    """The issues new, changed and superseded from ``desde`` to ``hasta`` (``ContextChanges``).

    No policy check: for the management review, which checks its own grant.

    Raises:
        ValueError: ``desde`` or ``hasta`` is not a ``date``, or ``desde`` comes
            after ``hasta`` (programming errors; the review validates its period).
    """
    desde, hasta = _date(desde, "desde"), _date(hasta, "hasta")
    if desde > hasta:
        raise ValueError("desde must not be after hasta")
    start = datetime.combine(desde, time.min, timezone.utc)
    end = datetime.combine(hasta + timedelta(days=1), time.min, timezone.utc)
    issue = CuestionContexto
    updated = and_(issue.updated_at >= start, issue.updated_at < end)
    current = issue.estado == EstadoCuestion.vigente
    relevant, climate = issue.pertinente.is_(True), issue.cambio_climatico.is_(True)

    def found(*where: Any) -> list[CuestionContexto]:
        return list(session.scalars(select(issue).where(*where).order_by(*SPEC.order_by)))

    def count(*where: Any) -> int:
        return session.scalar(select(func.count()).select_from(issue).where(*where))

    return ContextChanges(
        nuevas=found(issue.created_at >= start, issue.created_at < end),
        modificadas=found(updated, or_(issue.created_at.is_(None), issue.created_at < start)),
        superadas=found(updated, issue.estado == EstadoCuestion.superada),
        vigentes=count(current),
        pertinentes=count(current, relevant),
        cambio_climatico=count(current, climate),
        cambio_climatico_pertinentes=count(current, relevant, climate),
    )


# -- helpers ---------------------------------------------------------------------------


def _final(data: Mapping[str, Any], found: Any, key: str) -> Any:
    """``key`` as the write leaves it: the clean given value, else the record's own."""
    if key in data:
        return _FIELDS[key].clean(data, key)
    return None if found is None else getattr(found, key)


def _changed(found: CuestionContexto, data: Any) -> bool:
    """Whether ``data`` changes ``found``; invalid data counts as a change."""
    if not _well_formed(data):
        return True
    try:
        return any(_FIELDS[key].clean(data, key) != getattr(found, key) for key in data)
    except ValidationError:
        return True


def _is_cited(session: Session, issue_id: int) -> bool:
    return any(session.scalar(select(exists().where(column == issue_id)))
               for column in (Riesgo.cuestion_id, Oportunidad.cuestion_id))


def _cited_by_changes(session: Session, issue_id: int) -> bool:
    return bool(session.scalar(select(exists().where(CambioSGC.cuestion_id == issue_id))))


def _locked(session: Session, issue_id: Any) -> CuestionContexto:
    """The issue loaded afresh with its row locked (``FOR UPDATE``), or ``NotFound``."""
    found = None
    if people.is_db_id(issue_id):
        found = session.scalars(
            select(CuestionContexto).where(CuestionContexto.id == issue_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise NotFound(ISSUE_NOT_FOUND)
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
    """Whether ``data`` is a mapping of writable keys (otherwise ``crud`` refuses it)."""
    return isinstance(data, Mapping) and set(data) <= SPEC.writable


def _reject_unknown(data: Any) -> None:
    """Refuse a key outside the spec before any lookup (``crud`` refuses non-mappings)."""
    if isinstance(data, Mapping):
        fields.reject_unknown(data, SPEC.writable)


def _date(value: Any, name: str) -> date:
    """``value`` when it is a ``date`` (not a ``datetime``); else a programming error."""
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{name} must be a date")
    return value


def _today(today: Any) -> date:
    return _date(today, "today")  # the adapter's date, never user input


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to the context issues, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is CuestionContexto.__table__}
    return name


# The message for each foreign key whose violation means "still cited".
_CITING_KEYS = {_foreign_key(Riesgo): CITED_BY_RISKS, _foreign_key(Oportunidad): CITED_BY_RISKS,
                _foreign_key(CambioSGC): CITED_BY_CHANGES}
