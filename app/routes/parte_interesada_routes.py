# app/routes/parte_interesada_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from ..models import ParteInteresada
from ..forms import ParteInteresadaForm  # Importar el formulario
from ..extensions import db

bp = Blueprint('parte_interesada', __name__, url_prefix='/partes_interesadas')

@bp.route('/', methods=['GET'])
@login_required
def listar_partes_interesadas():
    partes = ParteInteresada.query.all()
    return render_template('partes_interesadas/listar.html', partes=partes)

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def crear_parte_interesada():
    form = ParteInteresadaForm()
    if form.validate_on_submit():
        nueva_parte = ParteInteresada(
            nombre=form.nombre.data,
            necesidades_expectativas=form.necesidades_expectativas.data,
            requisitos_identificados=form.requisitos_identificados.data,
            objetivo_estrategico=form.objetivo_estrategico.data
        )
        db.session.add(nueva_parte)
        db.session.commit()
        flash('Parte interesada creada exitosamente', 'success')
        return redirect(url_for('parte_interesada.listar_partes_interesadas'))
    return render_template('partes_interesadas/nueva.html', form=form)

@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_parte_interesada(id):
    parte = ParteInteresada.query.get_or_404(id)
    form = ParteInteresadaForm(obj=parte)
    if form.validate_on_submit():
        parte.nombre = form.nombre.data
        parte.necesidades_expectativas = form.necesidades_expectativas.data
        parte.requisitos_identificados = form.requisitos_identificados.data
        parte.objetivo_estrategico = form.objetivo_estrategico.data
        db.session.commit()
        flash('Parte interesada actualizada exitosamente', 'success')
        return redirect(url_for('parte_interesada.listar_partes_interesadas'))
    return render_template('partes_interesadas/editar.html', form=form, parte=parte)

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_parte_interesada(id):
    parte = ParteInteresada.query.get_or_404(id)
    db.session.delete(parte)
    db.session.commit()
    flash('Parte interesada eliminada correctamente', 'success')
    return redirect(url_for('parte_interesada.listar_partes_interesadas'))
