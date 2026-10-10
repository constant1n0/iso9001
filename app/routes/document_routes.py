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

# routes/document_routes.py
# Document control screens (``document-control``): adapters over ``documents``
# and ``document_revisions``. The list shows each document's revision in force,
# owner, next review date (flagged once overdue) and status; its filters are
# applied here, over what the services let the user read. A new document is
# created with its draft revision 1 and lands on its page, which shows the
# revision in force to every role and, to administrators and auditors, the
# revision in preparation with the actions its state allows, plus the history.
#
# Workflow (DC1-DC6): administrators and auditors start, edit, submit, reject
# and publish revisions; only administrators approve and withdraw. The approver
# picker leaves the author out; the service has the last word on every rule.
# A withdrawn document is read-only: its forms send back to its page with the
# reason. A service ``ValidationError`` or ``Conflict`` is rolled back and
# flashed: a form shows again with what was typed, a one-button action returns
# to the document's page. Workflow dates are ``local_today``. Documents are
# withdrawn, never deleted, so there is no delete route.
#
# Attachments (DC7): a draft's file is uploaded, replaced, removed or discarded
# with the draft; the file is stored first and recorded after. A new file is
# deleted only when it is refused before the commit; a file no committed
# revision references any more is deleted only after a successful commit. A
# failed commit may still have succeeded, so it deletes nothing: whatever no
# revision references is left to ``flask cleanup-document-files``. Downloads
# follow the reading rules: the revision in force for every role, the others
# for administrators and auditors only; they answer conditional (ETag from the
# SHA-256) and range requests.
import logging
import os
from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (CHOOSE_PERSON, ApproveRevisionForm, DocumentForm, DraftRevisionForm,
                     NewDocumentForm, NewRevisionForm, RejectRevisionForm,
                     WithdrawDocumentForm, person_choices)
from ..models import Document, DocumentCategory, DocumentRevision
from ..services import document_files, document_revisions, documents, people
from ..services.actor import Actor
from ..services.errors import NotFound, PermissionDenied, ValidationError
from ..utils import security_logger
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .people_routes import form_data, saved

bp = Blueprint('document', __name__, url_prefix='/documents')
logger = logging.getLogger(__name__)

FORM_FIELDS = ('title', 'code', 'category', 'owner_id', 'next_review_date')
FIRST_REVISION_FIELDS = ('author_id', 'content')
NEW_REVISION_FIELDS = ('author_id', 'change_summary')
DRAFT_FIELDS = ('content', 'change_summary', 'author_id')
# The list's «Estado» filter and badges.
STATUSES = {'vigente': 'Vigente', 'sin_publicar': 'Sin publicar', 'de_baja': 'De baja'}


def may_approve() -> bool:
    """Whether the logged-in user may approve revisions (administrators, DC3)."""
    return (can('update', 'documents')
            and current_actor().role in document_revisions.APPROVE_ROLES)


def may_withdraw() -> bool:
    """Whether the logged-in user may withdraw documents (administrators, DC6)."""
    return (can('update', 'documents')
            and current_actor().role in document_revisions.WITHDRAW_ROLES)


def _back_to(document_id: int) -> ResponseReturnValue:
    return redirect(url_for('document.view_document', document_id=document_id))


def _person_choices(actor: Actor, include: int | None = None,
                    exclude: int | None = None) -> list:
    """A required person picker: active people plus ``include``, without ``exclude``."""
    offered = people.choices(db.session, actor, include=include)
    return person_choices((p for p in offered if p.id != exclude), empty=CHOOSE_PERSON)


def _own_person(actor: Actor) -> int | None:
    """The person linked to the logged-in user, the default author or approver."""
    return people.of_user(db.session, actor.user_id)


def _refused(document: Document,
             draft: DocumentRevision | None = None) -> ResponseReturnValue | None:
    """Back to the document's page with the reason when its forms cannot be used.

    A withdrawn document is read-only, and a ``draft`` given must still be one.
    The service checks again when the form is posted.
    """
    try:
        if draft is not None:
            document_revisions.require_draft(db.session, draft)  # withdrawal included
        elif document.withdrawn_at is not None:
            raise ValidationError(document_revisions.WITHDRAWN)
    except ValidationError as error:
        flash(error.message, 'danger')
        return _back_to(document.id)
    return None


def _status(document: Document, vigente: DocumentRevision | None) -> str:
    """The list's status: withdrawn, in force or never published."""
    if document.withdrawn_at is not None:
        return 'de_baja'
    return 'vigente' if vigente is not None else 'sin_publicar'


def _overdue(document: Document, today: date) -> bool:
    """Whether a document still in use is past its next review date (DC5)."""
    return (document.withdrawn_at is None and document.next_review_date is not None
            and document.next_review_date < today)


def _list_filters(owners: dict[int, str]) -> dict:
    """The list filters; a value outside its options is ignored and does not count."""
    categoria = request.args.get('categoria', '')
    estado = request.args.get('estado', '')
    propietario = request.args.get('propietario', type=int)
    return {
        'categoria': categoria if categoria in DocumentCategory.__members__ else '',
        'propietario': propietario if propietario in owners else None,
        'estado': estado if estado in STATUSES else '',
        'vencida': request.args.get('vencida') == '1',
    }


def _matches(filters: dict, document: Document, status: str, overdue: bool) -> bool:
    """Whether a listed document passes every filter that applies."""
    return ((not filters['categoria'] or document.category.name == filters['categoria'])
            and filters['propietario'] in (None, document.owner_id)
            and filters['estado'] in ('', status)
            and (overdue or not filters['vencida']))


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def list_documents() -> ResponseReturnValue:
    """The documents the user may read, narrowed by category, owner, status and overdue review."""
    actor = current_actor()
    listed = documents.list_(db.session, actor)
    vigentes = document_revisions.effective(db.session, actor, (d.id for d in listed))
    owners = people.names(db.session, actor, (d.owner_id for d in listed))
    today = local_today()
    filters = _list_filters(owners)
    rows = []
    for document in listed:
        vigente = vigentes.get(document.id)
        status, overdue = _status(document, vigente), _overdue(document, today)
        if _matches(filters, document, status, overdue):
            rows.append((document, vigente, status, overdue))
    return render_template(
        'documents/list.html', rows=rows, owners=owners, filters=filters,
        applied=sum(1 for value in filters.values() if value), statuses=STATUSES,
        categories=DocumentCategory, may_see_drafts=document_revisions.may_see_drafts(actor))


@bp.route('/<int:document_id>', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def view_document(document_id: int) -> ResponseReturnValue:
    """The document with its revision in force, its history and, for drafts readers, the draft."""
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    history = document_revisions.list_(db.session, actor, document.id)
    cited = (document.owner_id, document.withdrawn_by_id,
             *(r.author_id for r in history), *(r.approver_id for r in history))
    return render_template(
        'documents/view.html', document=document, history=history,
        vigente=document_revisions.effective(db.session, actor, [document.id]).get(document.id),
        pendiente=document_revisions.in_preparation(db.session, actor, document.id),
        personas=people.names(db.session, actor, cited),
        overdue=_overdue(document, local_today()), may_approve=may_approve(),
        may_withdraw=may_withdraw(), accept=document_files.ACCEPT,
        human_size=document_files.human_size,
        max_size=document_files.human_size(current_app.config['DOCUMENT_MAX_BYTES']))


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'documents')
def new_document() -> ResponseReturnValue:
    """Create a document and its draft revision 1, then show its page."""
    actor = current_actor()
    form = NewDocumentForm()
    form.owner_id.choices = form.author_id.choices = _person_choices(actor)
    if request.method == 'GET':
        form.author_id.data = _own_person(actor)
    created = form.validate_on_submit() and saved(lambda: documents.create(
        db.session, actor, form_data(form, FORM_FIELDS + FIRST_REVISION_FIELDS)))
    if created:
        flash('Documento creado exitosamente', 'success')
        return _back_to(created.id)
    return render_template('documents/new.html', form=form)


@bp.route('/edit/<int:document_id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def edit_document(document_id: int) -> ResponseReturnValue:
    """Change a document's own data; a withdrawn document sends back to its page."""
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    if (refused := _refused(document)) is not None:
        return refused
    form = DocumentForm(obj=document)
    form.owner_id.choices = _person_choices(actor, include=document.owner_id)
    # The select uses enum names as values; preselect the stored category.
    if request.method == 'GET':
        form.category.data = document.category.name
    if form.validate_on_submit() and saved(lambda: documents.update(
            db.session, actor, document_id, form_data(form, FORM_FIELDS))):
        flash('Documento actualizado exitosamente', 'success')
        return _back_to(document_id)
    return render_template('documents/edit.html', form=form, document=document)


def _revision_page(form, document: Document, title: str, submit_label: str,
                   revision: DocumentRevision | None = None,
                   note: str | None = None) -> str:
    """A workflow form under a summary of the document and, if given, the revision."""
    cited = (revision.author_id, revision.approver_id) if revision is not None else ()
    return render_template(
        'documents/revision_form.html', form=form, document=document, revision=revision,
        title=title, submit_label=submit_label, note=note,
        personas=people.names(db.session, current_actor(), cited))


@bp.route('/<int:document_id>/revisions/new', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'documents')
def new_revision(document_id: int) -> ResponseReturnValue:
    """Start the next revision, a draft holding the text in force (``start_draft``)."""
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    if (refused := _refused(document)) is not None:
        return refused
    form = NewRevisionForm()
    form.author_id.choices = _person_choices(actor)
    if request.method == 'GET':
        form.author_id.data = _own_person(actor)
    created = form.validate_on_submit() and saved(lambda: document_revisions.start_draft(
        db.session, actor, document_id, form_data(form, NEW_REVISION_FIELDS)))
    if created:
        flash(f'Revisión {created.numero} creada en borrador.', 'success')
        return _back_to(document_id)
    return _revision_page(form, document, 'Nueva revisión', 'Crear borrador',
                          note='El borrador parte del texto de la revisión vigente.')


@bp.route('/<int:document_id>/revisions/<int:revision_id>/edit', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def edit_draft(document_id: int, revision_id: int) -> ResponseReturnValue:
    """Change a draft's text, change summary or author."""
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    revision = _revision_of(actor, document_id, revision_id)
    if (refused := _refused(document, draft=revision)) is not None:
        return refused
    form = DraftRevisionForm(obj=revision)
    form.author_id.choices = _person_choices(actor, include=revision.author_id)
    if form.validate_on_submit() and saved(lambda: document_revisions.edit_draft(
            db.session, actor, revision_id, form_data(form, DRAFT_FIELDS))):
        flash('Borrador guardado.', 'success')
        return _back_to(document_id)
    return _revision_page(form, document, f'Editar la revisión {revision.numero}',
                          'Guardar borrador', revision)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/submit', methods=['POST'])
@login_required
@require_permission('update', 'documents')
def submit_revision(document_id: int, revision_id: int) -> ResponseReturnValue:
    """Send a draft for review; it needs a change summary."""
    actor = current_actor()
    revision = _revision_of(actor, document_id, revision_id)
    if saved(lambda: document_revisions.submit(db.session, actor, revision_id)):
        flash(f'Revisión {revision.numero} enviada a revisión.', 'success')
    return _back_to(document_id)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/approve', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def approve_revision(document_id: int, revision_id: int) -> ResponseReturnValue:
    """Approve a revision in review; administrators only, never its author (DC3)."""
    if not may_approve():
        raise PermissionDenied()
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    revision = _revision_of(actor, document_id, revision_id)
    if (refused := _refused(document)) is not None:
        return refused
    form = ApproveRevisionForm()
    form.approver_id.choices = _person_choices(actor, exclude=revision.author_id)
    if request.method == 'GET':
        own = _own_person(actor)
        form.approver_id.data = None if own == revision.author_id else own
    if form.validate_on_submit() and saved(lambda: document_revisions.approve(
            db.session, actor, revision_id, approver_id=form.approver_id.data,
            today=local_today())):
        flash(f'Revisión {revision.numero} aprobada.', 'success')
        return _back_to(document_id)
    author = people.names(db.session, actor, [revision.author_id]).get(revision.author_id)
    note = (f'El autor de la revisión ({author}) no puede aprobarla, ni tampoco el usuario '
            'vinculado a esa persona.' if author else None)
    return _revision_page(form, document, f'Aprobar la revisión {revision.numero}', 'Aprobar',
                          revision, note)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/reject', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def reject_revision(document_id: int, revision_id: int) -> ResponseReturnValue:
    """Return a revision in review to draft, recording why."""
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    revision = _revision_of(actor, document_id, revision_id)
    if (refused := _refused(document)) is not None:
        return refused
    form = RejectRevisionForm()
    if form.validate_on_submit() and saved(lambda: document_revisions.reject(
            db.session, actor, revision_id, form.review_comment.data)):
        flash(f'Revisión {revision.numero} devuelta a borrador.', 'success')
        return _back_to(document_id)
    return _revision_page(form, document, f'Rechazar la revisión {revision.numero}',
                          'Rechazar', revision)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/publish', methods=['POST'])
@login_required
@require_permission('update', 'documents')
def publish_revision(document_id: int, revision_id: int) -> ResponseReturnValue:
    """Put an approved revision in force today; the previous one becomes obsolete."""
    actor = current_actor()
    revision = _revision_of(actor, document_id, revision_id)
    if saved(lambda: document_revisions.publish(db.session, actor, revision_id,
                                                today=local_today())):
        flash(f'Revisión {revision.numero} publicada: ya está vigente.', 'success')
    return _back_to(document_id)


@bp.route('/<int:document_id>/withdraw', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def withdraw_document(document_id: int) -> ResponseReturnValue:
    """Withdraw a document (DC6); administrators only."""
    if not may_withdraw():
        raise PermissionDenied()
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    if (refused := _refused(document)) is not None:
        return refused
    form = WithdrawDocumentForm()
    if form.validate_on_submit() and saved(lambda: document_revisions.withdraw(
            db.session, actor, document_id, form.withdrawn_reason.data, today=local_today())):
        flash('Documento dado de baja.', 'success')
        return _back_to(document_id)
    return _revision_page(form, document, 'Dar de baja el documento', 'Dar de baja',
                          note='La revisión vigente pasará a obsoleta y el documento quedará '
                               'en solo lectura.')


def _revision_of(actor, document_id, revision_id):
    """Revision ``revision_id`` of document ``document_id`` if the actor reads it, else 404."""
    revision = document_revisions.get(db.session, actor, revision_id)
    if revision.document_id != document_id:
        raise NotFound(document_revisions.NOT_FOUND)
    return revision


def _storage():
    return current_app.config['DOCUMENT_STORAGE_DIR']


def _delete_file(stored_name):
    """Delete a stored file nothing references; a failure leaves it for the cleanup command."""
    if stored_name is None:
        return
    try:
        document_files.remove(_storage(), stored_name)
    except OSError:
        logger.warning('Stored document file %s could not be deleted; '
                       'flask cleanup-document-files will remove it.', stored_name)


def _commit(revision_id, kept):
    """Commit; on failure roll back, log the file left for the cleanup and re-raise.

    The commit may have succeeded with its acknowledgement lost, so a failure
    deletes no file: ``kept`` (a stored name or ``None``) stays on disk.
    """
    try:
        db.session.commit()
    except BaseException:
        db.session.rollback()
        if kept is None:
            logger.error('The commit of document revision %s failed.', revision_id)
        else:
            logger.error('The commit of document revision %s failed; stored file %s is kept '
                         'for flask cleanup-document-files.', revision_id, kept)
        raise


def _attachment(revision):
    return revision.attachment_name, revision.attachment_size, revision.attachment_sha256


@bp.route('/<int:document_id>/revisions/<int:revision_id>/attachment', methods=['POST'])
@login_required
@require_permission('update', 'documents')
def upload_attachment(document_id, revision_id):
    """Attach the uploaded ``file`` to a draft revision, replacing its previous file."""
    actor = current_actor()
    revision = _revision_of(actor, document_id, revision_id)
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        flash('Selecciona un fichero.', 'danger')
        return _back_to(document_id)
    try:
        document_revisions.require_draft(db.session, revision)  # before any disk I/O
        stored = document_files.store(_storage(), upload.stream, upload.filename,
                                      max_bytes=current_app.config['DOCUMENT_MAX_BYTES'])
        try:
            previous = document_revisions.attach(db.session, actor, revision_id, stored)
        except BaseException:  # refused before the commit: nothing records the new file
            db.session.rollback()
            _delete_file(stored.stored_name)
            raise
    except ValidationError as error:
        security_logger.log_document_file_rejected(
            actor.label, document_id, revision_id, document_files.display_name(upload.filename),
            error.message)
        raise
    _commit(revision_id, kept=stored.stored_name)
    _delete_file(previous)
    security_logger.log_document_file('DOCUMENT_ATTACHMENT_UPLOADED', actor.label, document_id,
                                      revision_id, stored.display_name, stored.size,
                                      stored.sha256)
    flash('Fichero adjuntado.', 'success')
    return _back_to(document_id)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/attachment/delete',
          methods=['POST'])
@login_required
@require_permission('update', 'documents')
def detach_attachment(document_id, revision_id):
    """Remove a draft revision's file."""
    actor = current_actor()
    described = _attachment(_revision_of(actor, document_id, revision_id))
    removed = document_revisions.detach(db.session, actor, revision_id)
    _commit(revision_id, kept=removed)
    if removed is None:
        flash('La revisión no tiene ningún fichero adjunto.', 'info')
        return _back_to(document_id)
    _delete_file(removed)
    security_logger.log_document_file('DOCUMENT_ATTACHMENT_DETACHED', actor.label, document_id,
                                      revision_id, *described)
    flash('Fichero quitado.', 'success')
    return _back_to(document_id)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/discard', methods=['POST'])
@login_required
@require_permission('update', 'documents')
def discard_draft(document_id, revision_id):
    """Delete a draft revision and its file (``document_revisions.discard_draft``)."""
    actor = current_actor()
    described = _attachment(_revision_of(actor, document_id, revision_id))
    orphaned = document_revisions.discard_draft(db.session, actor, revision_id)
    _commit(revision_id, kept=orphaned)
    _delete_file(orphaned)
    security_logger.log_document_file('DOCUMENT_DRAFT_DISCARDED', actor.label, document_id,
                                      revision_id, *described)
    flash('Borrador descartado.', 'success')
    return _back_to(document_id)


@bp.route('/<int:document_id>/revisions/<int:revision_id>/attachment', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def download_attachment(document_id, revision_id):
    """Send a revision's file as a download, never rendered by the browser.

    The ETag is the file's SHA-256, so an unchanged file answers 304; ranges
    are served from the file's size on disk. Drafts are never stored by caches.
    """
    revision = _revision_of(current_actor(), document_id, revision_id)
    if revision.attachment_path is None:
        abort(404)
    try:
        handle = document_files.open_stored(_storage(), revision.attachment_path)
    except ValueError:
        logger.error('Document revision %s records an invalid stored file name.', revision.id)
        abort(404)
    except FileNotFoundError:
        logger.error('The stored file of document revision %s is missing.', revision.id)
        abort(404)
    try:
        size = os.fstat(handle.fileno()).st_size
        response = send_file(handle, mimetype=revision.attachment_mime, conditional=False,
                             etag=revision.attachment_sha256, max_age=None)
        response.content_length = size
        response.headers['Content-Disposition'] = document_files.content_disposition(
            revision.attachment_name)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        frozen = revision.estado in document_revisions.FROZEN_STATES
        response.headers['Cache-Control'] = 'private, no-cache' if frozen else 'no-store'
        # send_file cannot serve ranges of an open file: it does not know its size
        return response.make_conditional(request, accept_ranges=True, complete_length=size)
    except BaseException:  # the response never took the handle over: close it here
        handle.close()
        raise
