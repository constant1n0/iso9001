from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response
from ..models import Auditoria, RoleEnum
from ..forms import AuditoriaForm
from ..extensions import db
from flask_login import login_required
from ..utils.decorators import role_required
from weasyprint import HTML

bp = Blueprint('auditoria', __name__, url_prefix='/auditorias')

@bp.route('/', methods=['GET'])
@login_required
@role_required(RoleEnum.AUDITOR)
def listar_auditorias():
    """
    Lista todas las auditorías registradas en el sistema, con funcionalidad de búsqueda y filtrado.
    Solo accesible para Auditores y Administradores.
    """
    query = Auditoria.query
    
    # Filtrado por área auditada
    area = request.args.get('area')
    if area:
        query = query.filter(Auditoria.area_auditada.ilike(f'%{area}%'))
    
    # Filtrado por auditor
    auditor = request.args.get('auditor')
    if auditor:
        query = query.filter(Auditoria.auditor.ilike(f'%{auditor}%'))
    
    # Filtrado por fecha
    fecha = request.args.get('fecha')
    if fecha:
        query = query.filter(db.func.date(Auditoria.fecha) == fecha)
    
    auditorias = query.all()
    return render_template('auditorias/listar.html', auditorias=auditorias)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def nueva_auditoria():
    """
    Muestra el formulario para crear una nueva auditoría y guarda el registro
    en la base de datos al enviarlo.
    Solo accesible para Auditores y Administradores.
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
    Solo accesible para Auditores y Administradores.
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
    Solo accesible para Auditores y Administradores.
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
    Solo accesible para Auditores y Administradores.
    """
    auditoria = Auditoria.query.get_or_404(id)
    
    # Renderiza la plantilla en HTML
    rendered_html = render_template('auditorias/pdf_template.html', auditoria=auditoria)
    
    # Convierte el HTML en PDF usando WeasyPrint
    pdf_file = HTML(string=rendered_html).write_pdf()
    
    # Prepara la respuesta en PDF
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=auditoria_{id}.pdf'
    
    return response
