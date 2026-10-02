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

from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..forms import CapacitacionForm
from ..extensions import db
from ..services import training
from flask_login import login_required
from ..utils.pdf import pdf_response, render_pdf
from ..utils.web_actor import current_actor
from ..utils.web_args import date_arg

bp = Blueprint('capacitacion', __name__, url_prefix='/capacitaciones')

FORM_FIELDS = ('tema', 'fecha', 'personal', 'duracion_horas', 'evaluacion_final')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


@bp.route('/', methods=['GET'])
@login_required
def listar_capacitaciones():
    capacitaciones = training.list_(
        db.session,
        current_actor(),
        tema=request.args.get('tema'),
        fecha=date_arg('fecha'),
        personal=request.args.get('personal'),
    )
    return render_template('capacitaciones/listar.html', capacitaciones=capacitaciones)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_capacitacion():
    form = CapacitacionForm()
    if form.validate_on_submit():
        training.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('Capacitación registrada exitosamente', 'success')
        return redirect(url_for('capacitacion.listar_capacitaciones'))
    return render_template('capacitaciones/nueva.html', form=form)

# Ruta para editar una capacitación
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_capacitacion(id):
    """
    Carga el formulario de edición de una capacitación y guarda los cambios en la base de datos.
    """
    actor = current_actor()
    capacitacion = training.get(db.session, actor, id)
    form = CapacitacionForm(obj=capacitacion)
    if form.validate_on_submit():
        training.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('Capacitación actualizada exitosamente', 'success')
        return redirect(url_for('capacitacion.listar_capacitaciones'))
    return render_template('capacitaciones/editar.html', form=form, capacitacion=capacitacion)

# Ruta para eliminar una capacitación
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_capacitacion(id):
    """
    Elimina una capacitación de la base de datos.
    """
    training.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Capacitación eliminada exitosamente', 'success')
    return redirect(url_for('capacitacion.listar_capacitaciones'))

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    """
    Genera un PDF para un registro de capacitación específico usando su ID.
    """
    capacitacion = training.get(db.session, current_actor(), id)
    return pdf_response(render_pdf('capacitaciones/pdf_template.html', capacitacion=capacitacion), f'capacitacion_{id}.pdf')
