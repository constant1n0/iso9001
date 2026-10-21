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

"""Interested parties (``ParteInteresada``) on the generic CRUD helper.

Validation mirrors ``ParteInteresadaForm``; every role may use the register.
Decision OC2 of ``organization-context`` (ISO 9001 clause 4.2) adds the context
fields: ``tipo`` (``interna`` or ``externa``, a member name), ``pertinente``
(``None`` means "sin evaluar", as on the rows that predate it), the owner
``responsable_id`` (a person, active when newly chosen), ``fecha_revision``,
the next review, and ``requisitos_climaticos`` (false unless set). The texts
stay as they were and ``nombre`` stays unique.

``create`` and ``update`` take ``today``, the adapter's local date: a write
that sets ``fecha_revision`` to a new date needs it, and that date is never
before it (``REVIEW_IN_PAST``). A date already stored is kept as it is, so a
party whose review is overdue stays editable, and a write that leaves the date
alone may omit ``today``. ``revision_vencida`` and ``overdue`` tell a review
date before ``today``.

An interested party that risks or opportunities cite cannot be deleted
(``Conflict`` with ``CITED_BY_RISKS``): ``delete`` checks it, and the
``ON DELETE RESTRICT`` foreign keys, which SQLite only enforces on request,
refuse a citation added after the check. Any other conflict keeps its own
message.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Oportunidad, ParteInteresada, Riesgo, TipoContexto
from . import crud, fields, people, policy
from .actor import Actor
from .errors import Conflict, ValidationError
from .policy import Action, Resource

NOMBRE_MAX = 50  # mirrors ParteInteresada.nombre and the web form
REVIEW_IN_PAST = (
    "La fecha de la próxima revisión («fecha_revision») no puede ser anterior a hoy.")


def _optional_member(data: Mapping[str, Any], key: str, enum_cls: type) -> Any:
    """A member given by name, or ``None``."""
    return None if data[key] is None else fields.enum_member(data, key, enum_cls)


def _optional_date(data: Mapping[str, Any], key: str) -> date | None:
    """A ``date`` (not a ``datetime`` or text), or ``None``."""
    value = data[key]
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    raise ValidationError(f"El campo «{key}» debe ser una fecha.")


def _flag(data: Mapping[str, Any], key: str) -> bool:
    """A real boolean for a column that is never NULL."""
    value = data[key]
    if not isinstance(value, bool):
        raise ValidationError(f"El campo «{key}» debe ser verdadero o falso.")
    return value


SPEC = crud.Spec(
    model=ParteInteresada,
    resource=Resource.INTERESTED_PARTIES,
    not_found="Parte interesada no encontrada.",
    fields=(
        crud.Field("nombre", partial(fields.text, required=True, max_length=NOMBRE_MAX), required=True),
        crud.Field("necesidades_expectativas", partial(fields.text, strip=False)),
        crud.Field("requisitos_identificados", partial(fields.text, strip=False)),
        crud.Field("objetivo_estrategico", partial(fields.text, strip=False)),
        crud.Field("tipo", partial(_optional_member, enum_cls=TipoContexto)),
        crud.Field("pertinente", fields.boolean),
        crud.Field("responsable_id", fields.integer, check=people.check_reference),
        crud.Field("fecha_revision", _optional_date),
        crud.Field("requisitos_climaticos", _flag),
    ),
    order_by=(ParteInteresada.nombre, ParteInteresada.id_interesado),
    conflict="Ya existe una parte interesada con ese nombre.",
)

CITED_BY_RISKS = (
    "No se puede eliminar una parte interesada a la que se refieren riesgos u "
    "oportunidades; quítala antes de esos registros."
)

get = partial(crud.get, SPEC)


def create(session: Session, actor: Actor, data: Mapping[str, Any], *,
           today: date | None = None) -> ParteInteresada:
    """Create an interested party; only ``nombre`` is required.

    ``today`` is needed when ``fecha_revision`` is given (see the module docstring).
    """
    policy.require(actor, Action.CREATE, SPEC.resource)
    _check_review(data, None, today)
    return crud.create(SPEC, session, actor, data)


def update(session: Session, actor: Actor, record_id: int, data: Mapping[str, Any], *,
           today: date | None = None) -> ParteInteresada:
    """Apply the given fields; an update that changes nothing writes nothing.

    ``today`` is needed when ``fecha_revision`` changes (see the module docstring).
    """
    policy.require(actor, Action.UPDATE, SPEC.resource)
    found = session.get(ParteInteresada, record_id) if people.is_db_id(record_id) else None
    if found is not None:  # an unknown id is ``crud``'s ``NotFound``
        _check_review(data, found, today)
    return crud.update(SPEC, session, actor, record_id, data)


def delete(session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete an interested party no risk or opportunity cites, keeping an audit snapshot.

    A cited party raises ``Conflict`` (``CITED_BY_RISKS``); so does one cited
    after the check, when the flush breaks one of those foreign keys and the
    database names it (PostgreSQL). Any other ``Conflict``, or one SQLite
    does not name, is raised as it came.
    """
    policy.require(actor, Action.DELETE, SPEC.resource)
    found = crud.get(SPEC, session, actor, record_id)
    if _cited_by_risks(session, found.id_interesado):
        raise Conflict(CITED_BY_RISKS)
    try:
        crud.delete(SPEC, session, actor, found.id_interesado)
    except Conflict as exc:
        if _violated_constraint(exc.__cause__) in RISKS_FKS:
            raise Conflict(CITED_BY_RISKS) from exc.__cause__
        raise


def list_(session: Session, actor: Actor) -> list[ParteInteresada]:
    """All interested parties in name order."""
    return crud.list_(SPEC, session, actor)


list_page = partial(crud.list_page, SPEC)  # one page in the ``list_`` order plus the total


def overdue(today: date) -> Any:
    """SQL: the next review is before ``today``; never NULL."""
    return and_(ParteInteresada.fecha_revision.is_not(None),
                ParteInteresada.fecha_revision < _today(today))


def revision_vencida(party: ParteInteresada, *, today: date) -> bool:
    """Whether the party's next review (``fecha_revision``) is before ``today``."""
    return party.fecha_revision is not None and party.fecha_revision < _today(today)


def _check_review(data: Any, found: ParteInteresada | None, today: Any) -> None:
    """A new ``fecha_revision`` is never before ``today``; data ``crud`` refuses is left to it."""
    if (not isinstance(data, Mapping) or "fecha_revision" not in data
            or not set(data) <= SPEC.writable):
        return
    review = _optional_date(data, "fecha_revision")
    if review is None or (found is not None and review == found.fecha_revision):
        return
    if review < _today(today):
        raise ValidationError(REVIEW_IN_PAST)


def _today(today: Any) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ValueError("today must be a date")  # a programming error, not user input
    return today


def _cited_by_risks(session: Session, party_id: int) -> bool:
    return any(session.scalar(select(exists().where(column == party_id)))
               for column in (Riesgo.parte_interesada_id, Oportunidad.parte_interesada_id))


def _foreign_key(model: type) -> str:
    """Name of ``model``'s foreign key to interested parties, as the model declares it."""
    (name,) = {fk.constraint.name for fk in model.__table__.foreign_keys
               if fk.column.table is ParteInteresada.__table__}
    return name


RISKS_FKS = frozenset({_foreign_key(Riesgo), _foreign_key(Oportunidad)})


def _violated_constraint(exc: BaseException | None) -> str | None:
    """The constraint the database names in ``exc`` (psycopg2 ``diag``), if any."""
    if not isinstance(exc, IntegrityError):
        return None
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
