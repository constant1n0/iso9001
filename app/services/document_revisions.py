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

"""Document revisions and their review workflow (ISO 9001 clause 7.5, ``document-control``).

A document's text lives in numbered revisions (decision DC1). Revision 1 is
created with the document (``documents.create`` calls ``clean_first`` and
``add_first``); ``start_draft`` creates the next one from the effective text.
At most one revision is in preparation (``borrador``, ``en_revision`` or
``aprobado``) and at most one is in force (``vigente``); two partial unique
indexes back both rules.

Transitions (DC2): ``submit`` (borrador to en_revision, needs a change
summary), ``approve`` (en_revision to aprobado; administrators only, and the
approver is never the author: neither the named approver nor the acting
user's own person, DC3), ``reject`` (en_revision back to borrador, with a
comment) and ``publish`` (aprobado to vigente from the adapter's date; the
previous effective revision becomes ``obsoleto`` that day). ``withdraw``
(administrators only, DC6) obsoletes the effective revision and marks the
document withdrawn, which makes it read-only; a revision still in preparation
stays frozen as it was. Only a ``borrador`` is edited (``edit_draft``);
effective and obsolete revisions never change again (DC3).

Attachments (DC7): ``attach`` records a file kept by ``document_files.store``
on a ``borrador`` and ``detach`` clears it; both return the stored name of the
file they replace, which the adapter deletes only after committing.
``require_draft`` lets the adapter refuse an upload before storing it; the
write re-checks under the lock. A new
draft starts without the effective revision's file, so no two revisions share
one (``attachment_path`` is unique). ``discard_draft`` deletes a ``borrador``
when its document has a revision in force to fall back on: revision 1 of a
document never put in force is its only text and stays (withdraw the document
instead). The discarded number is free again; the deletion's audit row keeps
the revision's full snapshot, attachment name, size and SHA-256 included.

Reading (DC4): administrators and auditors see every revision; other roles
only the effective one. Writing follows the policy (``DOCUMENTS``:
administrators and auditors), narrowed to administrators for approving and
withdrawing.

Every write locks the document row first (``lock_document``), so concurrent
transitions of one document serialize on PostgreSQL. Each changed row gets its
own audit row; dates come from the adapter, never from the server clock.
Workflow dates are ``date`` values: a ``datetime`` is refused (``ValueError``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ATTACHMENT_COLUMNS, Document, DocumentRevision, EstadoRevision, RoleEnum
from . import audit, fields, people, policy
from .actor import Actor
from .attribution import stamp_created, stamp_updated
from .document_files import MIME_BY_EXTENSION, STORED_NAME, StoredFile
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

R = EstadoRevision
PENDING_STATES = (R.borrador, R.en_revision, R.aprobado)
FROZEN_STATES = (R.vigente, R.obsoleto)
DRAFT_READERS = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})  # DC4
# Beyond writing documents (the policy matrix), only these roles approve or withdraw.
APPROVE_ROLES = frozenset({RoleEnum.ADMINISTRADOR})
WITHDRAW_ROLES = APPROVE_ROLES
DRAFT_FIELDS = frozenset({"content", "change_summary", "author_id"})
NEW_DRAFT_FIELDS = frozenset({"change_summary", "author_id"})  # the text is copied

DOCUMENT_NOT_FOUND = "Documento no encontrado."
NOT_FOUND = "Revisión no encontrada."
NOT_A_MAPPING = "Los datos deben ser un objeto con campos."
WITHDRAWN = "El documento está dado de baja y no se puede modificar."
IMMUTABLE = "Una revisión vigente u obsoleta no se puede modificar."
ONE_DRAFT = "El documento ya tiene una revisión en preparación."
NO_EFFECTIVE = "El documento no tiene una revisión vigente de la que partir."
NOT_A_DRAFT = "Solo se puede modificar una revisión en borrador."
AUTHOR_REQUIRED = "El autor de la revisión es obligatorio."
SUMMARY_REQUIRED = "El resumen de cambios es obligatorio para enviar la revisión a revisión."
APPROVER_REQUIRED = "Indica la persona que aprueba la revisión."
AUTHOR_APPROVES = "El autor de una revisión no puede aprobarla."
COMMENT_REQUIRED = "El motivo del rechazo es obligatorio."
REASON_REQUIRED = "El motivo de la baja es obligatorio."
CONFLICT = "Otra revisión del documento ha cambiado a la vez; vuelve a intentarlo."
FIRST_DRAFT = ("La primera revisión de un documento que nunca ha estado vigente no se puede "
               "descartar; da de baja el documento si ya no se necesita.")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def may_see_drafts(actor: Actor) -> bool:
    """Whether ``actor`` reads revisions other than the effective one (DC4)."""
    return actor.role in DRAFT_READERS


def lock_document(session: Session, document_id: int) -> Document:
    """Load a document afresh with its row locked (``FOR UPDATE``), or raise ``NotFound``.

    The lock lasts until the adapter ends the transaction; SQLite ignores it.
    """
    found = None
    if people.is_db_id(document_id):
        found = session.scalars(
            select(Document).where(Document.id == document_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
    if found is None:
        raise NotFound(DOCUMENT_NOT_FOUND)
    return found


def _visible(actor: Actor) -> list[Any]:
    return [] if may_see_drafts(actor) else [DocumentRevision.estado == R.vigente]


def get(session: Session, actor: Actor, revision_id: int) -> DocumentRevision:
    """One revision the actor may read, or ``NotFound``."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    found = _load(session, revision_id)
    if not may_see_drafts(actor) and found.estado is not R.vigente:
        raise NotFound(NOT_FOUND)
    return found


def list_(session: Session, actor: Actor, document_id: int) -> list[DocumentRevision]:
    """The revisions of a document that the actor may read, newest first."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return list(session.scalars(
        select(DocumentRevision)
        .where(DocumentRevision.document_id == document_id, *_visible(actor))
        .order_by(DocumentRevision.numero.desc())
    ))


def effective(
    session: Session, actor: Actor, document_ids: Iterable[int]
) -> dict[int, DocumentRevision]:
    """The effective revision of each document in ``document_ids`` that has one."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    wanted = {value for value in document_ids if people.is_db_id(value)}
    if not wanted:
        return {}
    rows = session.scalars(select(DocumentRevision).where(
        DocumentRevision.document_id.in_(wanted), DocumentRevision.estado == R.vigente))
    return {row.document_id: row for row in rows}


def in_preparation(
    session: Session, actor: Actor, document_id: int
) -> DocumentRevision | None:
    """The document's revision in preparation, for actors who read drafts (else ``None``)."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return _pending(session, document_id) if may_see_drafts(actor) else None


def require_draft(session: Session, revision: DocumentRevision) -> None:
    """Refuse a ``revision`` that is not a ``borrador`` of an active document.

    A cheap check without locks, for the adapter to run before costly work
    (storing an upload); the write itself checks again under the lock.
    """
    _check_state(session.get(Document, revision.document_id), revision, R.borrador,
                 NOT_A_DRAFT)


def in_force(session: Session, document_id: int) -> DocumentRevision | None:
    """The effective revision of a document whose reading was already authorized."""
    return session.scalar(select(DocumentRevision).where(
        DocumentRevision.document_id == document_id, DocumentRevision.estado == R.vigente))


def clean_first(session: Session, data: Mapping[str, Any]) -> dict[str, Any]:
    """Validated values of revision 1: ``content`` and ``author_id`` are required."""
    fields.require_keys(data, frozenset({"content", "author_id"}))
    return _clean(session, data, DRAFT_FIELDS)


def add_first(
    session: Session, actor: Actor, document: Document, values: Mapping[str, Any]
) -> DocumentRevision:
    """Add draft revision 1 of a new ``document`` from ``clean_first`` values."""
    return _add(session, actor, document.id, 1, values)


def start_draft(
    session: Session, actor: Actor, document_id: int, data: Mapping[str, Any]
) -> DocumentRevision:
    """Start the next revision, a ``borrador`` holding the effective text.

    ``data`` names the ``author_id`` (required) and may give a ``change_summary``.
    """
    policy.require(actor, Action.CREATE, Resource.DOCUMENTS)
    document = lock_document(session, document_id)
    _ensure_active(document)
    fields.require_keys(_mapping(data), frozenset({"author_id"}))
    values = _clean(session, data, NEW_DRAFT_FIELDS)
    if _pending(session, document.id) is not None:
        raise ValidationError(ONE_DRAFT)
    current = in_force(session, document.id)
    if current is None:
        raise ValidationError(NO_EFFECTIVE)
    last = session.scalar(select(func.max(DocumentRevision.numero))
                          .where(DocumentRevision.document_id == document.id))
    return _add(session, actor, document.id, last + 1, values | {"content": current.content})


def edit_draft(
    session: Session, actor: Actor, revision_id: int, data: Mapping[str, Any]
) -> DocumentRevision:
    """Change a draft's text, change summary or author; a no-op writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.borrador, NOT_A_DRAFT)
    values = _clean(session, data, DRAFT_FIELDS, current=revision)
    if all(getattr(revision, key) == value for key, value in values.items()):
        return revision
    _change(session, actor, revision, **values)
    return revision


def submit(session: Session, actor: Actor, revision_id: int) -> DocumentRevision:
    """Send a draft for review; it needs a change summary."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.borrador,
                       "Solo se puede enviar a revisión una revisión en borrador.")
    if not (revision.change_summary or "").strip():
        raise ValidationError(SUMMARY_REQUIRED)
    _change(session, actor, revision, estado=R.en_revision)
    return revision


def approve(
    session: Session, actor: Actor, revision_id: int, *, approver_id: int | None, today: date
) -> DocumentRevision:
    """Approve a revision in review; administrators only, never its author (DC3).

    Args:
        approver_id: The approving person (an active ``personas`` id).
        today: The approval date to record (the adapter's local date).
    """
    _require_role(actor, APPROVE_ROLES)
    revision = _locked(session, revision_id, R.en_revision,
                       "Solo se puede aprobar una revisión enviada a revisión.")
    approver = people.reference(session, {"approver_id": approver_id}, "approver_id")
    if approver is None:
        raise ValidationError(APPROVER_REQUIRED)
    author = revision.author_id
    if author is not None and author in (approver, people.of_user(session, actor.user_id)):
        raise ValidationError(AUTHOR_APPROVES)
    _change(session, actor, revision, estado=R.aprobado, approver_id=approver,
            approved_at=_date(today))
    return revision


def reject(
    session: Session, actor: Actor, revision_id: int, comment: str | None
) -> DocumentRevision:
    """Return a revision in review to ``borrador``, recording why (``review_comment``)."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.en_revision,
                       "Solo se puede rechazar una revisión enviada a revisión.")
    if not isinstance(comment, str) or not comment.strip():
        raise ValidationError(COMMENT_REQUIRED)
    _change(session, actor, revision, estado=R.borrador, review_comment=comment.strip())
    return revision


def publish(session: Session, actor: Actor, revision_id: int, *, today: date) -> DocumentRevision:
    """Put an approved revision in force from ``today``; the previous one becomes obsolete."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.aprobado,
                       "Solo se puede publicar una revisión aprobada.")
    today = _date(today)
    previous = in_force(session, revision.document_id)
    if previous is not None:  # first, so the two never hold "vigente" at once
        _change(session, actor, previous, estado=R.obsoleto, obsolete_from=today)
    _change(session, actor, revision, estado=R.vigente, effective_from=today)
    return revision


def withdraw(
    session: Session, actor: Actor, document_id: int, reason: str | None, *, today: date
) -> Document:
    """Withdraw a document (DC6); administrators only.

    The effective revision becomes obsolete; the document records the date,
    the reason and the person linked to the acting user, and is read-only
    from then on.
    """
    _require_role(actor, WITHDRAW_ROLES)
    document = lock_document(session, document_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError(REASON_REQUIRED)
    _ensure_active(document)
    today = _date(today)
    current = in_force(session, document.id)
    if current is not None:
        _change(session, actor, current, estado=R.obsoleto, obsolete_from=today)
    _change(session, actor, document, withdrawn_at=today, withdrawn_reason=reason.strip(),
            withdrawn_by_id=people.of_user(session, actor.user_id))
    return document


def attach(
    session: Session, actor: Actor, revision_id: int, stored: StoredFile
) -> str | None:
    """Record ``stored`` as the attachment of a ``borrador``, replacing any previous one.

    Returns the stored name of the replaced file (or ``None``) for the adapter
    to delete once the change is committed, never before.
    """
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    values = _attachment_values(stored)
    revision = _locked(session, revision_id, R.borrador, NOT_A_DRAFT)
    replaced = revision.attachment_path
    _change(session, actor, revision, **values)
    return replaced


def detach(session: Session, actor: Actor, revision_id: int) -> str | None:
    """Remove a ``borrador``'s attachment; returns its stored name, ``None`` if it had none."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.borrador, NOT_A_DRAFT)
    removed = revision.attachment_path
    if removed is not None:
        _change(session, actor, revision, **dict.fromkeys(ATTACHMENT_COLUMNS))
    return removed


def discard_draft(session: Session, actor: Actor, revision_id: int) -> str | None:
    """Delete a ``borrador`` whose document has a revision in force (``FIRST_DRAFT``).

    Returns the stored name of its attachment (or ``None``) for the adapter to
    delete once the deletion is committed.
    """
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    revision = _locked(session, revision_id, R.borrador, NOT_A_DRAFT)
    if in_force(session, revision.document_id) is None:
        raise ValidationError(FIRST_DRAFT)
    orphaned = revision.attachment_path
    audit.record(session, actor, "delete", revision)
    session.delete(revision)
    try:
        session.flush()
    except IntegrityError as exc:  # nothing should reference a draft; stay typed if it does
        raise Conflict(CONFLICT) from exc
    return orphaned


def _attachment_values(stored: Any) -> dict[str, Any]:
    """The columns recording ``stored``; anything but a well-formed record is a bug."""
    valid = (
        isinstance(stored, StoredFile)
        and isinstance(stored.stored_name, str) and STORED_NAME.fullmatch(stored.stored_name)
        and isinstance(stored.display_name, str) and 0 < len(stored.display_name) <= 255
        and type(stored.size) is int and stored.size > 0
        and isinstance(stored.sha256, str) and _SHA256.fullmatch(stored.sha256)
        and stored.mime in MIME_BY_EXTENSION.values()
    )
    if not valid:
        raise ValueError("An attachment must be a StoredFile returned by document_files.store.")
    return dict(zip(ATTACHMENT_COLUMNS, (stored.display_name, stored.stored_name, stored.size,
                                         stored.sha256, stored.mime), strict=True))


def _load(session: Session, revision_id: int) -> DocumentRevision:
    found = session.get(DocumentRevision, revision_id) if people.is_db_id(revision_id) else None
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _locked(
    session: Session, revision_id: int, state: EstadoRevision, wrong_state: str
) -> DocumentRevision:
    """The revision, re-read once its document's row is locked, checked to be in ``state``.

    Refuses a withdrawn document, an effective or obsolete revision
    (``IMMUTABLE``) and any other state than ``state`` (``wrong_state``).
    """
    document = lock_document(session, _load(session, revision_id).document_id)
    revision = session.scalars(
        select(DocumentRevision).where(DocumentRevision.id == revision_id)
        .execution_options(populate_existing=True)
    ).one()
    _check_state(document, revision, state, wrong_state)
    return revision


def _check_state(
    document: Document, revision: DocumentRevision, state: EstadoRevision, wrong_state: str
) -> None:
    """Refuse a withdrawn ``document``, a frozen ``revision`` or one not in ``state``."""
    _ensure_active(document)
    if revision.estado in FROZEN_STATES:
        raise ValidationError(IMMUTABLE)
    if revision.estado is not state:
        raise ValidationError(wrong_state)


def _ensure_active(document: Document) -> None:
    if document.withdrawn_at is not None:
        raise ValidationError(WITHDRAWN)


def _pending(session: Session, document_id: int) -> DocumentRevision | None:
    return session.scalar(select(DocumentRevision).where(
        DocumentRevision.document_id == document_id,
        DocumentRevision.estado.in_(PENDING_STATES)))


def _mapping(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValidationError(NOT_A_MAPPING)
    return data


def _clean(
    session: Session, data: Any, allowed: frozenset[str],
    current: DocumentRevision | None = None,
) -> dict[str, Any]:
    """Validate the keys present in ``data`` (within ``allowed``) and normalize them."""
    fields.reject_unknown(_mapping(data), allowed)
    clean: dict[str, Any] = {}
    if "content" in data:
        clean["content"] = fields.text(data, "content", required=True)
    if "change_summary" in data:
        clean["change_summary"] = fields.text(data, "change_summary")
    if "author_id" in data:  # last: it queries the database
        if data["author_id"] is None:
            raise ValidationError(AUTHOR_REQUIRED)
        clean["author_id"] = people.reference(
            session, data, "author_id", current.author_id if current is not None else None)
    return clean


def _add(
    session: Session, actor: Actor, document_id: int, numero: int, values: Mapping[str, Any]
) -> DocumentRevision:
    created = DocumentRevision(document_id=document_id, numero=numero, estado=R.borrador,
                               **values)
    stamp_created(created, actor)
    session.add(created)
    try:
        audit.record(session, actor, "create", created)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(CONFLICT) from exc
    return created


def _change(session: Session, actor: Actor, row: Any, **values: Any) -> None:
    """Set ``values`` on a revision or document, then stamp, audit and flush it."""
    before = audit.snapshot(row)
    for key, value in values.items():
        setattr(row, key, value)
    stamp_updated(row, actor)
    audit.record(session, actor, "update", row, before=before)
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict(CONFLICT) from exc


def _require_role(actor: Actor, roles: frozenset[RoleEnum]) -> None:
    """The policy check for writing documents, narrowed to ``roles``."""
    policy.require(actor, Action.UPDATE, Resource.DOCUMENTS)
    if actor.role not in roles:
        raise PermissionDenied()


def _date(value: Any) -> date:
    """The adapter's date (never a ``datetime``); there is no server-clock fallback."""
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("A workflow date must come from the adapter.")
    return value
