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
from ..services import clientes, satisfaction
from ..models import CanalContacto, TipoEncuesta
from .people_routes import saved
from flask_login import login_required
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('satisfaccion_cliente', __name__, url_prefix='/satisfaccion_cliente')

FORM_FIELDS = ('cliente_id', 'tipo_encuesta', 'canal', 'fecha_encuesta', 'puntuacion', 'comentarios')


def _form(actor, encuesta=None):
    form = SatisfaccionClienteForm(obj=encuesta)
    form.cliente_id.choices = [('', '— Elige un cliente —'),
                               *((c.id, c.nombre) for c in clientes.list_(db.session, actor))]
    return form


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

    actor = current_actor()
    customers = clientes.list_(db.session, actor)
    chosen = {}
    customer_id = request.args.get('cliente_id', type=int)
    if customer_id in {c.id for c in customers}:
        chosen['cliente_id'] = customer_id
    for key, enum_cls in (('tipo_encuesta', TipoEncuesta), ('canal', CanalContacto)):
        value = request.args.get(key, '')
        if value in enum_cls.__members__:
            chosen[key] = value
    encuestas = satisfaction.list_(db.session, actor, puntuacion_minima=puntuacion, **chosen)
    return render_template('satisfaccion_cliente/listar.html', encuestas=encuestas,
                           clientes=customers, tipos=TipoEncuesta, canales=CanalContacto,
                           elegidos=chosen)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'customer_satisfaction')
def nueva_encuesta():
    """
    Muestra el formulario para crear una nueva encuesta de satisfacción y guarda el registro en la base de datos.
    """
    form = _form(current_actor())
    if form.validate_on_submit() and saved(lambda: satisfaction.create(
            db.session, current_actor(), _form_data(form))):
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
    form = _form(actor, encuesta)
    if form.validate_on_submit() and saved(lambda: satisfaction.update(
            db.session, actor, id, _form_data(form))):
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
