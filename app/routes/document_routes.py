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
from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..forms import CHOOSE_PERSON, DocumentForm, NewDocumentForm, person_choices
from ..extensions import db
from flask_login import login_required
from ..services import document_revisions, documents, people
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('document', __name__, url_prefix='/documents')

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
    return render_template('documents/view.html', document=document, vigente=vigente,
                           personas=people.names(db.session, actor, cited))


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
