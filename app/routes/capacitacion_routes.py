# app/routes/capacitacion_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash
from ..models import Capacitacion
from ..forms import CapacitacionForm
from ..extensions import db

bp = Blueprint('capacitacion', __name__, url_prefix='/capacitaciones')

@bp.route('/', methods=['GET'])
def listar_capacitaciones():
    capacitaciones = Capacitacion.query.all()
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
