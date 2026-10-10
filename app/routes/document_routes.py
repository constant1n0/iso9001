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
# Minimal screens over documents and their revisions (``document-control``):
# the list and each document's page show the revision in force; creating a
# document writes its draft revision 1. The workflow screens come later (DC-3).
# Documents are withdrawn, never deleted, so there is no delete route.
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

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.exceptions import RequestedRangeNotSatisfiable
from ..forms import CHOOSE_PERSON, DocumentForm, NewDocumentForm, person_choices
from ..extensions import db
from flask_login import login_required
from ..services import document_files, document_revisions, documents, people
from ..services.errors import NotFound, ValidationError
from ..utils import security_logger
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('document', __name__, url_prefix='/documents')
logger = logging.getLogger(__name__)

FORM_FIELDS = ('title', 'code', 'category', 'owner_id', 'next_review_date')
FIRST_REVISION_FIELDS = ('author_id', 'content')


def _form_data(form, names=FORM_FIELDS):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in names}


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def list_documents():
    actor = current_actor()
    listed = documents.list_(db.session, actor)
    return render_template(
        'documents/list.html', documents=listed,
        vigentes=document_revisions.effective(db.session, actor, (d.id for d in listed)))


@bp.route('/<int:document_id>', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def view_document(document_id):
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    vigente = document_revisions.effective(db.session, actor, [document.id]).get(document.id)
    cited = (document.owner_id, document.withdrawn_by_id,
             vigente.author_id if vigente else None, vigente.approver_id if vigente else None)
    return render_template(
        'documents/view.html', document=document, vigente=vigente,
        pendiente=document_revisions.in_preparation(db.session, actor, document.id),
        personas=people.names(db.session, actor, cited), accept=document_files.ACCEPT,
        human_size=document_files.human_size,
        max_size=document_files.human_size(current_app.config['DOCUMENT_MAX_BYTES']))


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'documents')
def new_document():
    actor = current_actor()
    form = NewDocumentForm()
    form.owner_id.choices = form.author_id.choices = person_choices(
        people.choices(db.session, actor), empty=CHOOSE_PERSON)
    if form.validate_on_submit():
        documents.create(db.session, actor, _form_data(form, FORM_FIELDS + FIRST_REVISION_FIELDS))
        db.session.commit()
        flash('Documento creado exitosamente', 'success')
        return redirect(url_for('document.list_documents'))
    return render_template('documents/new.html', form=form)


@bp.route('/edit/<int:document_id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'documents')
def edit_document(document_id):
    actor = current_actor()
    document = documents.get(db.session, actor, document_id)
    form = DocumentForm(obj=document)
    form.owner_id.choices = person_choices(
        people.choices(db.session, actor, include=document.owner_id), empty=CHOOSE_PERSON)
    # The select uses enum names as values; preselect the stored category.
    if request.method == 'GET':
        form.category.data = document.category.name
    if form.validate_on_submit():
        documents.update(db.session, actor, document_id, _form_data(form))
        db.session.commit()
        flash('Documento actualizado exitosamente', 'success')
        return redirect(url_for('document.list_documents'))
    return render_template('documents/edit.html', form=form, document=document)


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


def _back_to(document_id):
    return redirect(url_for('document.view_document', document_id=document_id))


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
    size = os.fstat(handle.fileno()).st_size
    response = send_file(handle, mimetype=revision.attachment_mime, conditional=False,
                         etag=revision.attachment_sha256, max_age=None)
    response.content_length = size
    response.headers['Content-Disposition'] = document_files.content_disposition(
        revision.attachment_name)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    frozen = revision.estado in document_revisions.FROZEN_STATES
    response.headers['Cache-Control'] = 'private, no-cache' if frozen else 'no-store'
    try:  # send_file cannot serve ranges of an open file: it does not know its size
        return response.make_conditional(request, accept_ranges=True, complete_length=size)
    except RequestedRangeNotSatisfiable:
        handle.close()
        raise
