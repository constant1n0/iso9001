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
from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..forms import DocumentForm
from ..extensions import db
from flask_login import login_required
from ..services import documents
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('document', __name__, url_prefix='/documents')

FORM_FIELDS = ('title', 'code', 'category', 'version', 'issued_date', 'approved_by', 'content')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'documents')
def list_documents():
    return render_template('documents/list.html',
                           documents=documents.list_(db.session, current_actor()))

@bp.route('/new', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'documents')
def new_document():
    form = DocumentForm()
    if form.validate_on_submit():
        documents.create(db.session, current_actor(), _form_data(form))
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
    # The select uses enum names as values; preselect the stored category.
    if request.method == 'GET':
        form.category.data = document.category.name
    if form.validate_on_submit():
        documents.update(db.session, actor, document_id, _form_data(form))
        db.session.commit()
        flash('Documento actualizado exitosamente', 'success')
        return redirect(url_for('document.list_documents'))
    return render_template('documents/edit.html', form=form, document=document)

@bp.route('/delete/<int:document_id>', methods=['POST'])
@login_required
@require_permission('delete', 'documents')
def delete_document(document_id):
    documents.delete(db.session, current_actor(), document_id)
    db.session.commit()
    flash('Documento eliminado exitosamente', 'success')
    return redirect(url_for('document.list_documents'))
