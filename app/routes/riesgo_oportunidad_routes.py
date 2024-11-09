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
from ..models import RiesgoOportunidad
from ..schemas import RiesgoOportunidadSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('riesgo_oportunidad', __name__, url_prefix='/riesgo_oportunidad')

riesgo_oportunidad_schema = RiesgoOportunidadSchema()
riesgos_oportunidades_schema = RiesgoOportunidadSchema(many=True)

# Crear un nuevo riesgo o oportunidad
@bp.route('/', methods=['POST'])
@jwt_required()
def add_riesgo_oportunidad():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = riesgo_oportunidad_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nuevo_riesgo = RiesgoOportunidad(**data)
    db.session.add(nuevo_riesgo)
    db.session.commit()

    return riesgo_oportunidad_schema.jsonify(nuevo_riesgo), 201

# Obtener todos los riesgos y oportunidades con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_riesgos_oportunidades():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    riesgos_paginados = RiesgoOportunidad.query.paginate(page, per_page, error_out=False)
    return riesgos_oportunidades_schema.jsonify(riesgos_paginados.items), 200

# Actualizar un riesgo o una oportunidad
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_riesgo_oportunidad(id):
    riesgo = RiesgoOportunidad.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = riesgo_oportunidad_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(riesgo, key, value)

    db.session.commit()
    return riesgo_oportunidad_schema.jsonify(riesgo), 200

# Eliminar un riesgo o una oportunidad
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_riesgo_oportunidad(id):
    riesgo = RiesgoOportunidad.query.get_or_404(id)
    db.session.delete(riesgo)
    db.session.commit()
    return jsonify({'message': 'Riesgo/Oportunidad eliminado correctamente'}), 200
