# app/routes/auditoria_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..models import Auditoria
from ..forms import AuditoriaForm
from ..extensions import db

bp = Blueprint('auditoria', __name__, url_prefix='/auditorias')

@bp.route('/', methods=['GET'])
def listar_auditorias():
    """
    Lista todas las auditorías registradas en el sistema.
    """
    auditorias = Auditoria.query.all()
    return render_template('auditorias/listar.html', auditorias=auditorias)

@bp.route('/nueva', methods=['GET', 'POST'])
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
def eliminar_auditoria(id):
    """
    Elimina una auditoría existente de la base de datos.
    """
    auditoria = Auditoria.query.get_or_404(id)
    db.session.delete(auditoria)
    db
