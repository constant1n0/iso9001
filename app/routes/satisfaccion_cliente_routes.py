# app/routes/satisfaccion_cliente_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash
from ..models import SatisfaccionCliente
from ..forms import SatisfaccionClienteForm
from ..extensions import db

bp = Blueprint('satisfaccion_cliente', __name__, url_prefix='/satisfaccion_cliente')

@bp.route('/', methods=['GET'])
def listar_encuestas():
    encuestas = SatisfaccionCliente.query.all()
    return render_template('satisfaccion_cliente/listar.html', encuestas=encuestas)

@bp.route('/nueva', methods=['GET', 'POST'])
def nueva_encuesta():
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

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
def exportar_pdf(id):
    """
    Genera un PDF para una encuesta de satisfacción específica usando su ID.
    """
    encuesta = SatisfaccionCliente.query.get_or_404(id)
    rendered_html = render_template('satisfaccion_cliente/pdf_template.html', encuesta=encuesta)
    pdf_file = HTML(string=rendered_html).write_pdf()
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=encuesta_{id}.pdf'
    return response