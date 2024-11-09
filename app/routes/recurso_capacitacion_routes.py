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

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ..models import RecursoCapacitacion
from ..schemas import RecursoCapacitacionSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('recurso_capacitacion', __name__, url_prefix='/recurso_capacitacion')

recurso_capacitacion_schema = RecursoCapacitacionSchema()
recursos_capacitacion_schema = RecursoCapacitacionSchema(many=True)

# Crear un nuevo recurso y capacitación
@bp.route('/', methods=['POST'])
@jwt_required()
def add_recurso_capacitacion():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = recurso_capacitacion_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nuevo_recurso = RecursoCapacitacion(**data)
    db.session.add(nuevo_recurso)
    db.session.commit()

    return recurso_capacitacion_schema.jsonify(nuevo_recurso), 201

# Obtener todos los recursos y capacitaciones con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_recursos_capacitacion():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    recursos_paginados = RecursoCapacitacion.query.paginate(page, per_page, error_out=False)
    return recursos_capacitacion_schema.jsonify(recursos_paginados.items), 200

# Actualizar un recurso y capacitación
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_recurso_capacitacion(id):
    recurso = RecursoCapacitacion.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = recurso_capacitacion_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(recurso, key, value)

    db.session.commit()
    return recurso_capacitacion_schema.jsonify(recurso), 200

# Eliminar un recurso y capacitación
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_recurso_capacitacion(id):
    recurso = RecursoCapacitacion.query.get_or_404(id)
    db.session.delete(recurso)
    db.session.commit()
    return jsonify({'message': 'Recurso y capacitación eliminado correctamente'}), 200
