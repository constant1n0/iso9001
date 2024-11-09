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
from ..models import RolResponsabilidad
from ..schemas import RolResponsabilidadSchema
from ..extensions import db, cache
from marshmallow import ValidationError

bp = Blueprint('rol_responsabilidad', __name__, url_prefix='/rol_responsabilidad')

rol_responsabilidad_schema = RolResponsabilidadSchema()
roles_responsabilidades_schema = RolResponsabilidadSchema(many=True)

# Crear un nuevo rol y responsabilidad
@bp.route('/', methods=['POST'])
@jwt_required()
def add_rol_responsabilidad():
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = rol_responsabilidad_schema.load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 422

    nuevo_rol = RolResponsabilidad(**data)
    db.session.add(nuevo_rol)
    db.session.commit()

    return rol_responsabilidad_schema.jsonify(nuevo_rol), 201

# Obtener todos los roles y responsabilidades con paginación
@bp.route('/', methods=['GET'])
@jwt_required()
@cache.cached(timeout=50, query_string=True)
def get_roles_responsabilidades():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    roles_paginados = RolResponsabilidad.query.paginate(page, per_page, error_out=False)
    return roles_responsabilidades_schema.jsonify(roles_paginados.items), 200

# Actualizar un rol y responsabilidad
@bp.route('/<int:id>', methods=['PUT'])
@jwt_required()
def update_rol_responsabilidad(id):
    rol = RolResponsabilidad.query.get_or_404(id)
    json_data = request.get_json()
    if not json_data:
        return jsonify({'message': 'No se proporcionaron datos'}), 400
    try:
        data = rol_responsabilidad_schema.load(json_data, partial=True)
    except ValidationError as err:
        return jsonify(err.messages), 422

    for key, value in data.items():
        setattr(rol, key, value)

    db.session.commit()
    return rol_responsabilidad_schema.jsonify(rol), 200

# Eliminar un rol y responsabilidad
@bp.route('/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_rol_responsabilidad(id):
    rol = RolResponsabilidad.query.get_or_404(id)
    db.session.delete(rol)
    db.session.commit()
    return jsonify({'message': 'Rol y responsabilidad eliminado correctamente'}), 200
