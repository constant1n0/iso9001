# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..models import Document
from ..forms import DocumentForm
from ..extensions import db
from flask_login import login_required

bp = Blueprint('document', __name__, url_prefix='/documents')

@bp.route('/', methods=['GET'])
@login_required
def list_documents():
    documents = Document.query.all()
    return render_template('documents/list.html', documents=documents)

@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_document():
    form = DocumentForm()
    if form.validate_on_submit():
        document = Document(
            title=form.title.data,
            code=form.code.data,
            category=form.category.data,
            version=form.version.data,
            issued_date=form.issued_date.data,
            approved_by=form.approved_by.data,
            content=form.content.data
        )
        db.session.add(document)
        db.session.commit()
        flash("Documento registrado exitosamente", "success")
        return redirect(url_for('document.list_documents'))
    return render_template('documents/new.html', form=form)

@bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_document(id):
    document = Document.query.get_or_404(id)
    form = DocumentForm(obj=document)
    if form.validate_on_submit():
        document.title = form.title.data
        document.code = form.code.data
        document.category = form.category.data
        document.version = form.version.data
        document.issued_date = form.issued_date.data
        document.approved_by = form.approved_by.data
        document.content = form.content.data
        db.session.commit()
        flash("Documento actualizado exitosamente", "success")
        return redirect(url_for('document.list_documents'))
    return render_template('documents/edit.html', form=form, document=document)
