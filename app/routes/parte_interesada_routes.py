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

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from ..forms import ParteInteresadaForm  # Importar el formulario
from ..extensions import db
from ..services import stakeholders
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('parte_interesada', __name__, url_prefix='/partes_interesadas')

FORM_FIELDS = ('nombre', 'necesidades_expectativas', 'requisitos_identificados', 'objetivo_estrategico')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


@bp.route('/', methods=['GET'])
@login_required
def listar_partes_interesadas():
    partes = stakeholders.list_(db.session, current_actor())
    return render_template('partes_interesadas/listar.html', partes=partes)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'interested_parties')
def crear_parte_interesada():
    form = ParteInteresadaForm()
    if form.validate_on_submit():
        stakeholders.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('Parte interesada creada exitosamente', 'success')
        return redirect(url_for('parte_interesada.listar_partes_interesadas'))
    return render_template('partes_interesadas/nueva.html', form=form)

@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'interested_parties')
def editar_parte_interesada(id):
    actor = current_actor()
    parte = stakeholders.get(db.session, actor, id)
    form = ParteInteresadaForm(obj=parte)
    if form.validate_on_submit():
        stakeholders.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('Parte interesada actualizada exitosamente', 'success')
        return redirect(url_for('parte_interesada.listar_partes_interesadas'))
    return render_template('partes_interesadas/editar.html', form=form, parte=parte)

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_parte_interesada(id):
    stakeholders.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Parte interesada eliminada correctamente', 'success')
    return redirect(url_for('parte_interesada.listar_partes_interesadas'))
