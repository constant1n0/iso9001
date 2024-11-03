from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ..models import ProcesoOperacion
from ..schemas import ProcesoOperacionSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('proceso_operacion', __name__, url_prefix='/proceso_operacion')

proceso_operacion_schema = ProcesoOperacionSchema()
procesos_operacion_schema = ProcesoOperacionSchema(many=True)

# Crear un nuevo proceso de operación
@bp.route('/', methods=['POST'])
@jwt_required()
def add_proceso_operacion():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = proceso_operacion_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nuevo_proceso = ProcesoOperacion(**data)
    db.session.add(nuevo_proceso)
    db.session.commit()

    return proceso_operacion_schema.jsonify(nuevo_proceso), 201

# Obtener todos los procesos de operación con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_procesos_operacion():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    procesos_paginados = ProcesoOperacion.query.paginate(page, per_page, error_out=False)
    return procesos_operacion_schema.jsonify(procesos_paginados.items), 200

# Actualizar un proceso de operación
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_proceso_operacion(id):
    proceso = ProcesoOperacion.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = proceso_operacion_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(proceso, key, value)

    db.session.commit()
    return proceso_operacion_schema.jsonify(proceso), 200

# Eliminar un proceso de operación
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_proceso_operacion(id):
    proceso = ProcesoOperacion.query.get_or_404(id)
    db.session.delete(proceso)
    db.session.commit()
    return jsonify({'message': 'Proceso de operación eliminado correctamente'}), 200
