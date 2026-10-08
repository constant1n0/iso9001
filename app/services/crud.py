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

"""Spec-driven CRUD for the plain registers (training, surveys, ...).

A register is described by one ``Spec``: its model, policy resource, writable
fields with their validators, ordering and the message for a unique-constraint
conflict. The functions here follow the nonconformity contract: policy first,
field whitelist, ``NotFound``/``ValidationError``/``Conflict``, attribution
stamps, audit rows with changed fields only, no-op updates write nothing, and
they flush but never commit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit, fields, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100  # largest page a caller can request; bigger sizes are clamped
MAX_PAGE = 100_000  # keeps the offset far below the database integer range


def page_bounds(page: int, per_page: int, *, default_per_page: int = DEFAULT_PER_PAGE) -> tuple[int, int]:
    """Sane ``(page, per_page)``: page within 1..MAX_PAGE, size 1..MAX_PER_PAGE.

    A size below 1 falls back to ``default_per_page``; a larger one is clamped.
    """
    per_page = min(per_page if per_page >= 1 else default_per_page, MAX_PER_PAGE)
    return min(max(page, 1), MAX_PAGE), per_page


@dataclass(frozen=True)
class Field:
    """One writable column: ``clean(data, name)`` validates and normalizes it.

    ``required`` means the key must be present when creating; it is separate
    from the validator, which decides what a present value may be. ``check``,
    for rules that need the database, runs once every field is clean as
    ``check(session, name, value, current)``; ``current`` is the record's
    value, ``None`` when creating.
    """

    name: str
    clean: Callable[[Mapping[str, Any], str], Any]
    required: bool = False
    check: Callable[[Session, str, Any, Any], None] | None = None


@dataclass(frozen=True)
class Spec:
    """Everything the helper needs to know about one register."""

    model: type
    resource: Resource
    not_found: str  # message for ``NotFound``
    fields: tuple[Field, ...]
    order_by: tuple[Any, ...]  # SQL expressions; end with the primary key
    conflict: str | None = None  # message when a unique constraint is hit

    @property
    def writable(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)


def get(spec: Spec, session: Session, actor: Actor, record_id: int) -> Any:
    """Return one record or raise ``NotFound``."""
    policy.require(actor, Action.READ, spec.resource)
    return _load(spec, session, record_id)


def _load(spec: Spec, session: Session, record_id: int) -> Any:
    found = session.get(spec.model, record_id)
    if found is None:
        raise NotFound(spec.not_found)
    return found


def list_(spec: Spec, session: Session, actor: Actor,
          where: Sequence[Any] = ()) -> list[Any]:
    """All records matching the ``where`` conditions, in the spec's order."""
    policy.require(actor, Action.READ, spec.resource)
    query = select(spec.model).where(*where).order_by(*spec.order_by)
    return list(session.scalars(query))


def list_page(spec: Spec, session: Session, actor: Actor, where: Sequence[Any] = (),
              *, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> tuple[list[Any], int]:
    """One page plus the total matching; see ``page_bounds`` for how paging is bounded."""
    policy.require(actor, Action.READ, spec.resource)
    page, per_page = page_bounds(page, per_page)
    total = session.scalar(select(func.count()).select_from(spec.model).where(*where))
    query = (
        select(spec.model).where(*where).order_by(*spec.order_by)
        .limit(per_page).offset((page - 1) * per_page)
    )
    return list(session.scalars(query)), total


def _clean(spec: Spec, session: Session, data: Mapping[str, Any],
           found: Any = None) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values.

    ``found`` is the record being updated (``None`` when creating).
    """
    if not isinstance(data, Mapping):
        raise ValidationError("Los datos deben ser un objeto con campos.")
    fields.reject_unknown(data, spec.writable)
    values = {f.name: f.clean(data, f.name) for f in spec.fields if f.name in data}
    for f in spec.fields:
        if f.check is not None and f.name in values:
            current = None if found is None else getattr(found, f.name)
            f.check(session, f.name, values[f.name], current)
    return values


def _flush(session: Session, message: str | None = None) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def create(spec: Spec, session: Session, actor: Actor, data: Mapping[str, Any]) -> Any:
    """Create a record; every ``required`` field must be present."""
    policy.require(actor, Action.CREATE, spec.resource)
    if not isinstance(data, Mapping):
        raise ValidationError("Los datos deben ser un objeto con campos.")
    fields.require_keys(data, frozenset(f.name for f in spec.fields if f.required))
    created = spec.model(**_clean(spec, session, data))
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(spec.conflict) from exc
    return created


def update(spec: Spec, session: Session, actor: Actor, record_id: int,
           data: Mapping[str, Any]) -> Any:
    """Apply the given fields; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, spec.resource)
    found = _load(spec, session, record_id)
    values = _clean(spec, session, data, found)
    before = audit.snapshot(found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    audit.record(session, actor, "update", found, before=before)
    _flush(session, spec.conflict)
    return found


def delete(spec: Spec, session: Session, actor: Actor, record_id: int) -> None:
    """Hard-delete a record, keeping its full snapshot in the audit log."""
    policy.require(actor, Action.DELETE, spec.resource)
    found = _load(spec, session, record_id)
    audit.record(session, actor, "delete", found)
    session.delete(found)
    _flush(session)
