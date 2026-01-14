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

from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response
from ..models import SatisfaccionCliente
from ..forms import SatisfaccionClienteForm
from ..extensions import db
from flask_login import login_required
from weasyprint import HTML

bp = Blueprint('satisfaccion_cliente', __name__, url_prefix='/satisfaccion_cliente')

@bp.route('/', methods=['GET'])
@login_required
def listar_encuestas():
    """
    Lista todas las encuestas de satisfacción con opciones de filtrado por cliente y puntuación mínima.
    """
    query = SatisfaccionCliente.query
    
    # Filtrado por cliente
    cliente = request.args.get('cliente')
    if cliente:
        query = query.filter(SatisfaccionCliente.cliente.ilike(f'%{cliente}%'))
    
    # Filtrado por puntuación mínima
    puntuacion = request.args.get('puntuacion')
    if puntuacion:
        try:
            puntuacion = int(puntuacion)
            query = query.filter(SatisfaccionCliente.puntuacion >= puntuacion)
        except ValueError:
            flash('La puntuación debe ser un número entero.', 'warning')
    
    encuestas = query.all()
    return render_template('satisfaccion_cliente/listar.html', encuestas=encuestas)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_encuesta():
    """
    Muestra el formulario para crear una nueva encuesta de satisfacción y guarda el registro en la base de datos.
    """
    form = SatisfaccionClienteForm()
    if form.validate_on_submit():
        nueva_encuesta = SatisfaccionCliente(
            cliente=form.cliente.data,
            fecha_encuesta=form.fecha_encuesta.data,
            puntuacion=form.puntuacion.data,
            comentarios=form.comentarios.data
        )
        db.session.add(nueva_encuesta)
        db.session.commit()
        flash('Encuesta de satisfacción registrada exitosamente', 'success')
        return redirect(url_for('satisfaccion_cliente.listar_encuestas'))
    return render_template('satisfaccion_cliente/nueva.html', form=form)

# Ruta para editar una encuesta de satisfacción
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_encuesta(id):
    """
    Carga el formulario de edición de una encuesta y guarda los cambios en la base de datos.
    """
    encuesta = SatisfaccionCliente.query.get_or_404(id)
    form = SatisfaccionClienteForm(obj=encuesta)
    if form.validate_on_submit():
        encuesta.cliente = form.cliente.data
        encuesta.fecha_encuesta = form.fecha_encuesta.data
        encuesta.puntuacion = form.puntuacion.data
        encuesta.comentarios = form.comentarios.data
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
    encuesta = SatisfaccionCliente.query.get_or_404(id)
    db.session.delete(encuesta)
    db.session.commit()
    flash('Encuesta eliminada exitosamente', 'success')
    return redirect(url_for('satisfaccion_cliente.listar_encuestas'))

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    """
    Genera un PDF para una encuesta de satisfacción específica usando su ID.
    """
    encuesta = SatisfaccionCliente.query.get_or_404(id)
    rendered_html = render_template('satisfaccion_cliente/pdf_template.html', encuesta=encuesta)
    pdf_file = HTML(string=rendered_html).write_pdf()

    # Preparar la respuesta en PDF
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=encuesta_{id}.pdf'

    return response
