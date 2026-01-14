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

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from ..models import Mejora
from ..schemas import MejoraSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('mejora', __name__, url_prefix='/mejoras')

mejora_schema = MejoraSchema()
mejoras_schema = MejoraSchema(many=True)

# Listar todas las mejoras (vista HTML)
@bp.route('/', methods=['GET'])
@login_required
def listar_mejoras():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    mejoras_paginadas = Mejora.query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template('mejoras/listar.html', mejoras=mejoras_paginadas.items, pagination=mejoras_paginadas)

# Crear una nueva mejora (vista HTML)
@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_mejora():
    if request.method == 'POST':
        nueva_mejora = Mejora(
            no_conformidad=request.form.get('no_conformidad'),
            accion_correctiva=request.form.get('accion_correctiva'),
            accion_preventiva=request.form.get('accion_preventiva')
        )
        db.session.add(nueva_mejora)
        db.session.commit()
        flash('Mejora registrada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/nueva.html')

# Editar una mejora
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    if request.method == 'POST':
        mejora.no_conformidad = request.form.get('no_conformidad')
        mejora.accion_correctiva = request.form.get('accion_correctiva')
        mejora.accion_preventiva = request.form.get('accion_preventiva')
        db.session.commit()
        flash('Mejora actualizada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/editar.html', mejora=mejora)

# Eliminar una mejora
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    db.session.delete(mejora)
    db.session.commit()
    flash('Mejora eliminada correctamente', 'success')
    return redirect(url_for('mejora.listar_mejoras'))

# API: Obtener todas las mejoras con paginación (JSON)
@bp.route('/api/', methods=['GET'])
@login_required
@cache.cached(timeout=50, query_string=True)
def api_get_mejoras():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    mejoras_paginadas = Mejora.query.paginate(page=page, per_page=per_page, error_out=False)
    return mejoras_schema.jsonify(mejoras_paginadas.items), 200

# API: Crear una nueva mejora (JSON)
@bp.route('/api/', methods=['POST'])
@login_required
def api_add_mejora():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = mejora_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nueva_mejora = Mejora(**data)
    db.session.add(nueva_mejora)
    db.session.commit()

    return mejora_schema.jsonify(nueva_mejora), 201

# API: Actualizar una mejora (JSON)
@bp.route('/api/<int:id>', methods=['PUT'])
@login_required
def api_update_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = mejora_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(mejora, key, value)

    db.session.commit()
    return mejora_schema.jsonify(mejora), 200

# API: Eliminar una mejora (JSON)
@bp.route('/api/<int:id>', methods=['DELETE'])
@login_required
def api_delete_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    db.session.delete(mejora)
    db.session.commit()
    return jsonify({'message': 'Mejora eliminada correctamente'}), 200
