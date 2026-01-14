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
from flask_login import login_required
from ..models import NoConformidad
from ..forms import NoConformidadForm
from ..extensions import db
from weasyprint import HTML

# Define el blueprint y la URL base
bp = Blueprint('no_conformidad', __name__, url_prefix='/no_conformidades')

# Ruta para listar todas las no conformidades
@bp.route('/', methods=['GET'])
@login_required
def listar_no_conformidades():
    query = NoConformidad.query
    
    # Filtros opcionales por descripción, estado y fecha
    descripcion = request.args.get('descripcion')
    if descripcion:
        query = query.filter(NoConformidad.descripcion.ilike(f'%{descripcion}%'))
    
    estado = request.args.get('estado')
    if estado:
        query = query.filter(NoConformidad.estado.ilike(f'%{estado}%'))
    
    fecha_detectada = request.args.get('fecha_detectada')
    if fecha_detectada:
        query = query.filter(db.func.date(NoConformidad.fecha_detectada) == fecha_detectada)
    
    no_conformidades = query.all()
    return render_template('no_conformidades/listar.html', no_conformidades=no_conformidades)

# Ruta para registrar una nueva no conformidad
@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_no_conformidad():
    form = NoConformidadForm()
    if form.validate_on_submit():
        nueva_no_conformidad = NoConformidad(
            descripcion=form.descripcion.data,
            fecha_detectada=form.fecha_detectada.data,
            responsable=form.responsable.data,
            estado=form.estado.data,
            accion_correctiva=form.accion_correctiva.data
        )
        db.session.add(nueva_no_conformidad)
        db.session.commit()
        flash('No conformidad registrada exitosamente', 'success')
        return redirect(url_for('no_conformidad.listar_no_conformidades'))
    return render_template('no_conformidades/nueva.html', form=form)

# Ruta para editar una no conformidad
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_no_conformidad(id):
    no_conformidad = NoConformidad.query.get_or_404(id)
    form = NoConformidadForm(obj=no_conformidad)
    if form.validate_on_submit():
        no_conformidad.descripcion = form.descripcion.data
        no_conformidad.fecha_detectada = form.fecha_detectada.data
        no_conformidad.responsable = form.responsable.data
        no_conformidad.estado = form.estado.data
        no_conformidad.accion_correctiva = form.accion_correctiva.data
        db.session.commit()
        flash('No conformidad actualizada exitosamente', 'success')
        return redirect(url_for('no_conformidad.listar_no_conformidades'))
    return render_template('no_conformidades/editar.html', form=form, no_conformidad=no_conformidad)

# Ruta para eliminar una no conformidad
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_no_conformidad(id):
    no_conformidad = NoConformidad.query.get_or_404(id)
    db.session.delete(no_conformidad)
    db.session.commit()
    flash('No conformidad eliminada exitosamente', 'success')
    return redirect(url_for('no_conformidad.listar_no_conformidades'))

# Ruta para exportar una no conformidad a PDF
@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    no_conformidad = NoConformidad.query.get_or_404(id)
    rendered_html = render_template('no_conformidades/pdf_template.html', no_conformidad=no_conformidad)
    pdf_file = HTML(string=rendered_html).write_pdf()
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=no_conformidad_{id}.pdf'
    return response
