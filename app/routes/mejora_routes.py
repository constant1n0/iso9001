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
from ..models import Mejora
from ..schemas import MejoraSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('mejora', __name__, url_prefix='/mejora')

mejora_schema = MejoraSchema()
mejoras_schema = MejoraSchema(many=True)

# Crear una nueva mejora
@bp.route('/', methods=['POST'])
@jwt_required()
def add_mejora():
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

# Obtener todas las mejoras con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_mejoras():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    mejoras_paginadas = Mejora.query.paginate(page, per_page, error_out=False)
    return mejoras_schema.jsonify(mejoras_paginadas.items), 200

# Actualizar una mejora
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_mejora(id):
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

# Eliminar una mejora
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    db.session.delete(mejora)
    db.session.commit()
    return jsonify({'message': 'Mejora eliminada correctamente'}), 200
