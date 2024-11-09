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
from ..models import Auditoria, RoleEnum
from ..forms import AuditoriaForm
from ..extensions import db
from flask_login import login_required
from ..utils.decorators import role_required
from weasyprint import HTML
from math import ceil

bp = Blueprint('auditoria', __name__, url_prefix='/auditorias')

@bp.route('/', methods=['GET'])
@login_required
@role_required(RoleEnum.AUDITOR)
def listar_auditorias():
    """
    Lista todas las auditorías registradas en el sistema, con funcionalidad de búsqueda y filtrado avanzado.
    Incluye paginación para manejar grandes volúmenes de datos.
    """
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Número de auditorías por página

    query = Auditoria.query

    # Filtrado por área auditada
    area = request.args.get('area')
    if area:
        query = query.filter(Auditoria.area_auditada.ilike(f'%{area}%'))

    # Filtrado por auditor
    auditor = request.args.get('auditor')
    if auditor:
        query = query.filter(Auditoria.auditor.ilike(f'%{auditor}%'))

    # Filtrado por estado
    estado = request.args.get('estado')
    if estado:
        query = query.filter(Auditoria.estado.ilike(f'%{estado}%'))

    # Filtrado por rango de fechas
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')
    if fecha_inicio and fecha_fin:
        query = query.filter(Auditoria.fecha.between(fecha_inicio, fecha_fin))
    elif fecha_inicio:
        query = query.filter(Auditoria.fecha >= fecha_inicio)
    elif fecha_fin:
        query = query.filter(Auditoria.fecha <= fecha_fin)

    # Paginación
    total_auditorias = query.count()
    auditorias = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template('auditorias/listar.html', auditorias=auditorias.items, 
                           page=page, total_pages=ceil(total_auditorias / per_page),
                           total_auditorias=total_auditorias)


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def nueva_auditoria():
    """
    Muestra el formulario para crear una nueva auditoría y guarda el registro
    en la base de datos al enviarlo.
    """
    form = AuditoriaForm()
    if form.validate_on_submit():
        nueva_auditoria = Auditoria(
            area_auditada=form.area_auditada.data,
            fecha=form.fecha.data,
            auditor=form.auditor.data,
            resultado=form.resultado.data,
            accion_correctiva=form.accion_correctiva.data
        )
        db.session.add(nueva_auditoria)
        db.session.commit()
        flash('Auditoría creada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/nueva.html', form=form)

@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def editar_auditoria(id):
    """
    Carga el formulario de edición de una auditoría existente y guarda los
    cambios realizados en la base de datos.
    """
    auditoria = Auditoria.query.get_or_404(id)
    form = AuditoriaForm(obj=auditoria)
    if form.validate_on_submit():
        auditoria.area_auditada = form.area_auditada.data
        auditoria.fecha = form.fecha.data
        auditoria.auditor = form.auditor.data
        auditoria.resultado = form.resultado.data
        auditoria.accion_correctiva = form.accion_correctiva.data
        db.session.commit()
        flash('Auditoría actualizada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/editar.html', form=form, auditoria=auditoria)

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def eliminar_auditoria(id):
    """
    Elimina una auditoría existente de la base de datos.
    """
    auditoria = Auditoria.query.get_or_404(id)
    db.session.delete(auditoria)
    db.session.commit()
    flash('Auditoría eliminada exitosamente', 'success')
    return redirect(url_for('auditoria.listar_auditorias'))

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
@role_required(RoleEnum.AUDITOR)
def exportar_pdf(id):
    """
    Genera un PDF para una auditoría específica usando su ID.
    """
    auditoria = Auditoria.query.get_or_404(id)

    try:
        # Renderiza la plantilla en HTML
        rendered_html = render_template('auditorias/pdf_template.html', auditoria=auditoria)
        
        # Convierte el HTML en PDF usando WeasyPrint
        pdf_file = HTML(string=rendered_html).write_pdf()
        
        # Prepara la respuesta en PDF
        response = make_response(pdf_file)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=auditoria_{id}.pdf'
        
        return response
    except Exception as e:
        flash('Error al generar el PDF de la auditoría.', 'danger')
        return redirect(url_for('auditoria.listar_auditorias'))
