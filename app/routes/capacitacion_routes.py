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
from ..models import Capacitacion
from ..forms import CapacitacionForm
from ..extensions import db
from flask_login import login_required
from weasyprint import HTML

bp = Blueprint('capacitacion', __name__, url_prefix='/capacitaciones')

@bp.route('/', methods=['GET'])
@login_required
def listar_capacitaciones():
    query = Capacitacion.query

    # Filtrado por tema
    tema = request.args.get('tema')
    if tema:
        query = query.filter(Capacitacion.tema.ilike(f'%{tema}%'))

    # Filtrado por fecha
    fecha = request.args.get('fecha')
    if fecha:
        query = query.filter(db.func.date(Capacitacion.fecha) == fecha)

    # Filtrado por personal
    personal = request.args.get('personal')
    if personal:
        query = query.filter(Capacitacion.personal.ilike(f'%{personal}%'))

    capacitaciones = query.all()
    return render_template('capacitaciones/listar.html', capacitaciones=capacitaciones)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_capacitacion():
    form = CapacitacionForm()
    if form.validate_on_submit():
        nueva_capacitacion = Capacitacion(
            tema=form.tema.data,
            fecha=form.fecha.data,
            personal=form.personal.data,
            duracion_horas=form.duracion_horas.data,
            evaluacion_final=form.evaluacion_final.data
        )
        db.session.add(nueva_capacitacion)
        db.session.commit()
        flash('Capacitación registrada exitosamente', 'success')
        return redirect(url_for('capacitacion.listar_capacitaciones'))
    return render_template('capacitaciones/nueva.html', form=form)

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    """
    Genera un PDF para un registro de capacitación específico usando su ID.
    """
    capacitacion = Capacitacion.query.get_or_404(id)
    rendered_html = render_template('capacitaciones/pdf_template.html', capacitacion=capacitacion)

    # Convertir el HTML en PDF usando WeasyPrint
    pdf_file = HTML(string=rendered_html).write_pdf()
    
    # Crear respuesta de PDF
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=capacitacion_{id}.pdf'
    return response
