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

"""Document service, following ``nonconformities``.

Authorization comes from ``policy`` (resource ``DOCUMENTS``); validation mirrors
``DocumentForm``. ``code`` is unique: a duplicate raises ``Conflict``. There is
no versioning or approval workflow yet (Wave 1); ``signature`` is not writable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Document, DocumentCategory
from . import audit, crud, fields, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .errors import Conflict, NotFound
from .policy import Action, Resource


def get(session: Session, actor: Actor, document_id: int) -> Document:
    """Return one document or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return _load(session, document_id)


def _load(session: Session, document_id: int) -> Document:
    found = session.get(Document, document_id)
    if found is None:
        raise NotFound("Documento no encontrado.")
    return found


# ``code`` is unique; the id only keeps the order total should that ever change.
_ORDER = (Document.code, Document.id)


def list_(session: Session, actor: Actor) -> list[Document]:
    """All documents ordered by code."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return list(session.scalars(select(Document).order_by(*_ORDER)))


def list_page(
    session: Session, actor: Actor, *, page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE
) -> tuple[list[Document], int]:
    """One page in the ``list_`` order plus the total number of documents."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    page, per_page = crud.page_bounds(page, per_page)
    total = session.scalar(select(func.count()).select_from(Document))
    query = select(Document).order_by(*_ORDER).limit(per_page).offset((page - 1) * per_page)
    return list(session.scalars(query)), total


# Length limits mirror the Document columns and the web form.
TITLE_MAX, CODE_MAX, VERSION_MAX, APPROVED_BY_MAX = 150, 50, 10, 100
WRITABLE_FIELDS = frozenset(
    {"title", "code", "category", "version", "issued_date", "approved_by", "content"}
)
REQUIRED_ON_CREATE = WRITABLE_FIELDS - {"approved_by"}
DUPLICATE_CODE = "Ya existe un documento con ese código."


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the keys present in ``data`` and return the normalized values."""
    fields.reject_unknown(data, WRITABLE_FIELDS)
    clean: dict[str, Any] = {}
    if "title" in data:
        clean["title"] = fields.text(data, "title", required=True, max_length=TITLE_MAX)
    if "code" in data:
        clean["code"] = fields.text(data, "code", required=True, max_length=CODE_MAX)
    if "category" in data:
        clean["category"] = fields.enum_member(data, "category", DocumentCategory)
    if "version" in data:
        clean["version"] = fields.text(data, "version", required=True, max_length=VERSION_MAX)
    if "issued_date" in data:
        clean["issued_date"] = fields.required_date(data, "issued_date")
    if "approved_by" in data:
        clean["approved_by"] = fields.text(data, "approved_by", max_length=APPROVED_BY_MAX)
    if "content" in data:
        clean["content"] = fields.text(data, "content", required=True)
    return clean


def _ensure_code_free(session: Session, code: str, own_id: int | None = None) -> None:
    query = select(Document.id).where(Document.code == code)
    if own_id is not None:
        query = query.where(Document.id != own_id)
    if session.scalar(query) is not None:
        raise Conflict(DUPLICATE_CODE)


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_CODE) from exc


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> Document:
    """Create a document; every form field except ``approved_by`` is required."""
    policy.require(actor, Action.CREATE, Resource.DOCUMENTS)
    fields.require_keys(data, REQUIRED_ON_CREATE)
    values = _clean(data)
    _ensure_code_free(session, values["code"])
    created = Document(**values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_CODE) from exc
    return created


def update(
    session: Session, actor: Actor, document_id: int, data: Mapping[str, Any]
) -> Document:
    """Apply the given fields; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    found = _load(session, document_id)
    values = _clean(data)
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


def delete(session: Session, actor: Actor, document_id: int) -> None:
    """Hard-delete a document, keeping its full snapshot in the audit log."""
    policy.require(actor, Action.DELETE, Resource.DOCUMENTS)
    found = _load(session, document_id)
    audit.record(session, actor, "delete", found)
    session.delete(found)
    _flush(session)
