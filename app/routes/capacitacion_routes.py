# app/routes/capacitacion_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash
from ..models import Capacitacion
from ..forms import CapacitacionForm
from ..extensions import db

bp = Blueprint('capacitacion', __name__, url_prefix='/capacitaciones')

@bp.route('/', methods=['GET'])
def listar_capacitaciones():
    query = Capacitacion.query
    
    tema = request.args.get('tema')
    if tema:
        query = query.filter(Capacitacion.tema.ilike(f'%{tema}%'))
    
    fecha = request.args.get('fecha')
    if fecha:
        query = query.filter(db.func.date(Capacitacion.fecha) == fecha)
    
    personal = request.args.get('personal')
    if personal:
        query = query.filter(Capacitacion.personal.ilike(f'%{personal}%'))
    
    capacitaciones = query.all()
    return render_template('capacitaciones/listar.html', capacitaciones=capacitaciones)

@bp.route('/nueva', methods=['GET', 'POST'])
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
def exportar_pdf(id):
    """
    Genera un PDF para un registro de capacitación específico usando su ID.
    """
    capacitacion = Capacitacion.query.get_or_404(id)
    rendered_html = render_template('capacitaciones/pdf_template.html', capacitacion=capacitacion)
    pdf_file = HTML(string=rendered_html).write_pdf()
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=capacitacion_{id}.pdf'
    return response