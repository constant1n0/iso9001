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

"""Document service (ISO 9001 clause 7.5, ``document-control``).

A document keeps its identity (title, unique code, category), its owner and
next review date (decision DC5) and, once withdrawn, the withdrawal record
(DC6); its text lives in numbered revisions (``document_revisions``).
``create`` also creates draft revision 1 from ``content``, ``author_id`` and an
optional ``change_summary``; ``update`` changes the document's own fields.

Policy resource ``DOCUMENTS`` (DC4): every role reads, administrators and
auditors create and update, and nobody deletes: documents are withdrawn
(``document_revisions.withdraw``), never deleted, so there is no ``delete``.
Administrators and auditors see every document; other roles only documents
with a revision in force. A withdrawn document is read-only, and updates lock
its row like every workflow write.

``code`` is unique: a duplicate raises ``Conflict``. Once any revision has
been published (it is or was in force) the code is locked (``CODE_LOCKED``).
``owner_id`` cites an active person (``people.reference``); it is required on
create and cannot be cleared, though documents that predate it keep none until
edited.

Listing filters (``list_``, ``list_page``) run in the database and combine
with AND: ``category`` (member or name), ``owner_id``, ``status`` (one of
``STATUSES``) and ``overdue_on`` (documents in use whose next review date is
before that date). Periodic review (DC5): ``due_for_review`` lists the
documents in use whose review falls due by a date the adapter gives.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import exists, false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Document, DocumentCategory, DocumentRevision, EstadoRevision
from . import audit, crud, document_revisions, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource


def _in_force() -> Any:
    """The condition that a document has a revision in force."""
    return exists().where(DocumentRevision.document_id == Document.id,
                          DocumentRevision.estado == EstadoRevision.vigente)


def _visible(actor: Actor) -> list[Any]:
    """Conditions limiting documents to those ``actor`` may read (DC4)."""
    if document_revisions.may_see_drafts(actor):
        return []
    return [_in_force()]


def get(session: Session, actor: Actor, document_id: int) -> Document:
    """Return one document the actor may read or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    found = None
    if people.is_db_id(document_id):
        found = session.scalar(
            select(Document).where(Document.id == document_id, *_visible(actor)))
    if found is None:
        raise NotFound("Documento no encontrado.")
    return found


# ``code`` is unique; the id only keeps the order total should that ever change.
_ORDER = (Document.code, Document.id)


# The list's statuses: in force, in use but never published, withdrawn.
STATUSES = ("vigente", "sin_publicar", "de_baja")
BAD_CATEGORY = "La categoría no es válida."
BAD_OWNER = "El propietario debe ser un número entero."
BAD_STATUS = "El estado no es válido."
BAD_OVERDUE_ON = "La fecha de las revisiones vencidas debe ser una fecha."


def _is_date(value: Any) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _in_use() -> Any:
    return Document.withdrawn_at.is_(None)


def _status_conditions(status: str) -> list[Any]:
    if status == "de_baja":
        return [Document.withdrawn_at.is_not(None)]
    return [_in_use(), _in_force() if status == "vigente" else ~_in_force()]


def _conditions(
    category: DocumentCategory | str | None, owner_id: int | None, status: str | None,
    overdue_on: date | None,
) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if category is not None:
        try:
            member = fields.enum_member({"category": category}, "category", DocumentCategory)
        except ValidationError:
            raise ValidationError(BAD_CATEGORY) from None
        where.append(Document.category == member)
    if owner_id is not None:
        if not isinstance(owner_id, int) or isinstance(owner_id, bool):
            raise ValidationError(BAD_OWNER)
        where.append(Document.owner_id == owner_id if people.is_db_id(owner_id) else false())
    if status is not None:
        if status not in STATUSES:
            raise ValidationError(BAD_STATUS)
        where.extend(_status_conditions(status))
    if overdue_on is not None:
        if not _is_date(overdue_on):
            raise ValidationError(BAD_OVERDUE_ON)
        where.extend((_in_use(), Document.next_review_date < overdue_on))
    return where


def list_(
    session: Session, actor: Actor, *, category: DocumentCategory | str | None = None,
    owner_id: int | None = None, status: str | None = None, overdue_on: date | None = None,
) -> list[Document]:
    """The documents the actor may read, ordered by code; filters combine with AND."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    where = _visible(actor) + _conditions(category, owner_id, status, overdue_on)
    return list(session.scalars(select(Document).where(*where).order_by(*_ORDER)))


def list_page(
    session: Session, actor: Actor, *, category: DocumentCategory | str | None = None,
    owner_id: int | None = None, status: str | None = None, overdue_on: date | None = None,
    page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[Document], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    page, per_page = crud.page_bounds(page, per_page)
    where = _visible(actor) + _conditions(category, owner_id, status, overdue_on)
    total = session.scalar(select(func.count()).select_from(Document).where(*where))
    query = (select(Document).where(*where).order_by(*_ORDER)
             .limit(per_page).offset((page - 1) * per_page))
    return list(session.scalars(query)), total


def owner_ids(session: Session, actor: Actor) -> list[int]:
    """The people owning at least one document the actor may read (the owner filter)."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return list(session.scalars(
        select(Document.owner_id).where(Document.owner_id.is_not(None), *_visible(actor))
        .distinct()))


def due_for_review(
    session: Session, actor: Actor, *, today: date, within_days: int = 0
) -> list[Document]:
    """Documents in use, readable by the actor, whose review is due by ``today + within_days``.

    Soonest review first. ``today`` is the adapter's date (never a
    ``datetime``) and ``within_days`` a non-negative integer; anything else is
    a bug in the adapter (``ValueError``).
    """
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    if not _is_date(today):
        raise ValueError("The review date must be a date from the adapter.")
    if not isinstance(within_days, int) or isinstance(within_days, bool) or within_days < 0:
        raise ValueError("within_days must be a non-negative integer.")
    limit = today + timedelta(days=within_days)
    return list(session.scalars(
        select(Document)
        .where(*_visible(actor), _in_use(), Document.next_review_date <= limit)
        .order_by(Document.next_review_date, *_ORDER)))


# Length limits mirror the Document columns and the web form.
TITLE_MAX, CODE_MAX = 150, 50
WRITABLE_FIELDS = frozenset({"title", "code", "category", "owner_id", "next_review_date"})
REQUIRED_ON_CREATE = frozenset({"title", "code", "category", "owner_id"})
DUPLICATE_CODE = "Ya existe un documento con ese código."
CODE_LOCKED = "El código de un documento no se puede cambiar una vez publicada una revisión."
OWNER_REQUIRED = "El propietario del documento es obligatorio."


def _clean(
    session: Session, data: Mapping[str, Any], current: Document | None = None
) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values."""
    fields.reject_unknown(data, WRITABLE_FIELDS)
    clean: dict[str, Any] = {}
    if "title" in data:
        clean["title"] = fields.text(data, "title", required=True, max_length=TITLE_MAX)
    if "code" in data:
        clean["code"] = fields.text(data, "code", required=True, max_length=CODE_MAX)
    if "category" in data:
        clean["category"] = fields.enum_member(data, "category", DocumentCategory)
    if "next_review_date" in data:
        value = data["next_review_date"]
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise ValidationError("La fecha de la próxima revisión debe ser una fecha.")
        clean["next_review_date"] = value
    if "owner_id" in data:  # last: it queries the database
        if data["owner_id"] is None:
            raise ValidationError(OWNER_REQUIRED)
        clean["owner_id"] = people.reference(
            session, data, "owner_id", current.owner_id if current is not None else None)
    return clean


def _ensure_code_free(session: Session, code: str, own_id: int | None = None) -> None:
    query = select(Document.id).where(Document.code == code)
    if own_id is not None:
        query = query.where(Document.id != own_id)
    if session.scalar(query) is not None:
        raise Conflict(DUPLICATE_CODE)


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> Document:
    """Create a document and its draft revision 1.

    ``title``, ``code``, ``category``, ``owner_id``, ``content`` and
    ``author_id`` are required; ``next_review_date`` and ``change_summary``
    are optional.
    """
    policy.require(actor, Action.CREATE, Resource.DOCUMENTS)
    if not isinstance(data, Mapping):
        raise ValidationError("Los datos deben ser un objeto con campos.")
    revision_keys = document_revisions.DRAFT_FIELDS
    fields.reject_unknown(data, WRITABLE_FIELDS | revision_keys)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values = _clean(session, {k: v for k, v in data.items() if k not in revision_keys})
    first = document_revisions.clean_first(
        session, {k: v for k, v in data.items() if k in revision_keys})
    _ensure_code_free(session, values["code"])
    created = Document(**values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_CODE) from exc
    document_revisions.add_first(session, actor, created, first)
    return created


def update(
    session: Session, actor: Actor, document_id: int, data: Mapping[str, Any]
) -> Document:
    """Apply the given fields; a call that changes nothing writes nothing.

    A withdrawn document is read-only, and the code of a document with a
    published revision cannot change (``ValidationError``).
    """
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    found = document_revisions.lock_document(session, document_id)
    if found.withdrawn_at is not None:
        raise ValidationError(document_revisions.WITHDRAWN)
    values = _clean(session, data, current=found)
    before = audit.snapshot(found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    if "code" in values and values["code"] != found.code:
        if document_revisions.ever_published(session, found.id):
            raise ValidationError(CODE_LOCKED)
        _ensure_code_free(session, values["code"], own_id=found.id)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    try:
        audit.record(session, actor, "update", found, before=before)
        session.flush()
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_CODE) from exc
    return found
