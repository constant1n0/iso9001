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

``code`` is unique: a duplicate raises ``Conflict``. ``owner_id`` cites an
active person (``people.reference``); it is required on create and cannot be
cleared, though documents that predate it keep none until edited.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Document, DocumentCategory, DocumentRevision, EstadoRevision
from . import audit, crud, document_revisions, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound, ValidationError
from .policy import Action, Resource


def _visible(actor: Actor) -> list[Any]:
    """Conditions limiting documents to those ``actor`` may read (DC4)."""
    if document_revisions.may_see_drafts(actor):
        return []
    return [exists().where(DocumentRevision.document_id == Document.id,
                           DocumentRevision.estado == EstadoRevision.vigente)]


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


def list_(session: Session, actor: Actor) -> list[Document]:
    """The documents the actor may read, ordered by code."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return list(session.scalars(select(Document).where(*_visible(actor)).order_by(*_ORDER)))


def list_page(
    session: Session, actor: Actor, *, page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE
) -> tuple[list[Document], int]:
    """One page in the ``list_`` order plus the total the actor may read."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    page, per_page = crud.page_bounds(page, per_page)
    where = _visible(actor)
    total = session.scalar(select(func.count()).select_from(Document).where(*where))
    query = (select(Document).where(*where).order_by(*_ORDER)
             .limit(per_page).offset((page - 1) * per_page))
    return list(session.scalars(query)), total


# Length limits mirror the Document columns and the web form.
TITLE_MAX, CODE_MAX = 150, 50
WRITABLE_FIELDS = frozenset({"title", "code", "category", "owner_id", "next_review_date"})
REQUIRED_ON_CREATE = frozenset({"title", "code", "category", "owner_id"})
DUPLICATE_CODE = "Ya existe un documento con ese código."
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

    A withdrawn document is read-only (``ValidationError``).
    """
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    found = document_revisions.lock_document(session, document_id)
    if found.withdrawn_at is not None:
        raise ValidationError(document_revisions.WITHDRAWN)
    values = _clean(session, data, current=found)
    before = audit.snapshot(found)
    if all(getattr(found, key) == value for key, value in values.items()):
        return found
    if "code" in values:
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
