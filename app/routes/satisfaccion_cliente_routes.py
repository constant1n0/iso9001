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
from ..forms import SatisfaccionClienteForm
from ..extensions import db
from ..services import satisfaction
from flask_login import login_required
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('satisfaccion_cliente', __name__, url_prefix='/satisfaccion_cliente')

FORM_FIELDS = ('cliente', 'fecha_encuesta', 'puntuacion', 'comentarios')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


@bp.route('/', methods=['GET'])
@login_required
def listar_encuestas():
    """
    Lista todas las encuestas de satisfacción con opciones de filtrado por cliente y puntuación mínima.
    """
    # Filtrado por puntuación mínima
    puntuacion = request.args.get('puntuacion')
    if puntuacion:
        try:
            puntuacion = int(puntuacion)
        except ValueError:
            puntuacion = None
            flash('La puntuación debe ser un número entero.', 'warning')
    else:
        puntuacion = None

    encuestas = satisfaction.list_(
        db.session,
        current_actor(),
        cliente=request.args.get('cliente'),
        puntuacion_minima=puntuacion,
    )
    return render_template('satisfaccion_cliente/listar.html', encuestas=encuestas)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'customer_satisfaction')
def nueva_encuesta():
    """
    Muestra el formulario para crear una nueva encuesta de satisfacción y guarda el registro en la base de datos.
    """
    form = SatisfaccionClienteForm()
    if form.validate_on_submit():
        satisfaction.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('Encuesta de satisfacción registrada exitosamente', 'success')
        return redirect(url_for('satisfaccion_cliente.listar_encuestas'))
    return render_template('satisfaccion_cliente/nueva.html', form=form)

# Ruta para editar una encuesta de satisfacción
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'customer_satisfaction')
def editar_encuesta(id):
    """
    Carga el formulario de edición de una encuesta y guarda los cambios en la base de datos.
    """
    actor = current_actor()
    encuesta = satisfaction.get(db.session, actor, id)
    form = SatisfaccionClienteForm(obj=encuesta)
    if form.validate_on_submit():
        satisfaction.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('Encuesta actualizada exitosamente', 'success')
        return redirect(url_for('satisfaccion_cliente.listar_encuestas'))
    return render_template('satisfaccion_cliente/editar.html', form=form, encuesta=encuesta)

# Ruta para eliminar una encuesta de satisfacción
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_encuesta(id):
    """
    Elimina una encuesta de satisfacción de la base de datos.
    """
    satisfaction.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Encuesta eliminada exitosamente', 'success')
    return redirect(url_for('satisfaccion_cliente.listar_encuestas'))

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    """
    Genera un PDF para una encuesta de satisfacción específica usando su ID.
    """
    encuesta = satisfaction.get(db.session, current_actor(), id)
    return pdf_response(render_pdf('satisfaccion_cliente/pdf_template.html', encuesta=encuesta), f'encuesta_{id}.pdf')
