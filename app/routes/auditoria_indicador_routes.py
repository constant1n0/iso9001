from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ..models import AuditoriaIndicador
from ..schemas import AuditoriaIndicadorSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('auditoria_indicador', __name__, url_prefix='/auditoria_indicador')

auditoria_indicador_schema = AuditoriaIndicadorSchema()
auditorias_indicadores_schema = AuditoriaIndicadorSchema(many=True)

# Crear una nueva auditoría e indicador
@bp.route('/', methods=['POST'])
@jwt_required()
def add_auditoria_indicador():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = auditoria_indicador_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nueva_auditoria = AuditoriaIndicador(**data)
    db.session.add(nueva_auditoria)
    db.session.commit()

    return auditoria_indicador_schema.jsonify(nueva_auditoria), 201

# Obtener todas las auditorías e indicadores con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_auditorias_indicadores():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    auditorias_paginadas = AuditoriaIndicador.query.paginate(page, per_page, error_out=False)
    return auditorias_indicadores_schema.jsonify(auditorias_paginadas.items), 200

# Actualizar una auditoría e indicador
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_auditoria_indicador(id):
    auditoria = AuditoriaIndicador.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = auditoria_indicador_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(auditoria, key, value)

    db.session.commit()
    return auditoria_indicador_schema.jsonify(auditoria), 200

# Eliminar una auditoría e indicador
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_auditoria_indicador(id):
    auditoria = AuditoriaIndicador.query.get_or_404(id)
    db.session.delete(auditoria)
    db.session.commit()
    return jsonify({'message': 'Auditoría e indicador eliminados correctamente'}), 200
