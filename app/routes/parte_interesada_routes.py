from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ..models import ParteInteresada
from ..schemas import ParteInteresadaSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('parte_interesada', __name__, url_prefix='/parte_interesada')

parte_interesada_schema = ParteInteresadaSchema()
partes_interesadas_schema = ParteInteresadaSchema(many=True)

# Crear una nueva parte interesada
@bp.route('/', methods=['POST'])
@jwt_required()
def add_parte_interesada():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = parte_interesada_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nueva_parte = ParteInteresada(**data)
    db.session.add(nueva_parte)
    db.session.commit()

    return parte_interesada_schema.jsonify(nueva_parte), 201

# Obtener todas las partes interesadas con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_partes_interesadas():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    partes_paginadas = ParteInteresada.query.paginate(page, per_page, error_out=False)
    return partes_interesadas_schema.jsonify(partes_paginadas.items), 200

# Actualizar una parte interesada
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_parte_interesada(id):
    parte = ParteInteresada.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = parte_interesada_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(parte, key, value)

    db.session.commit()
    return parte_interesada_schema.jsonify(parte), 200

# Eliminar una parte interesada
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_parte_interesada(id):
    parte = ParteInteresada.query.get_or_404(id)
    db.session.delete(parte)
    db.session.commit()
    return jsonify({'message': 'Parte interesada eliminada correctamente'}), 200
