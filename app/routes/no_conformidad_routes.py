# app/routes/no_conformidad_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash
from ..models import NoConformidad
from ..forms import NoConformidadForm
from ..extensions import db

bp = Blueprint('no_conformidad', __name__, url_prefix='/no_conformidades')

@bp.route('/', methods=['GET'])
def listar_no_conformidades():
    no_conformidades = NoConformidad.query.all()
    return render_template('no_conformidades/listar.html', no_conformidades=no_conformidades)

@bp.route('/nueva', methods=['GET', 'POST'])
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
