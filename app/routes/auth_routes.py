from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from datetime import timedelta

bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['POST'])
def login():
    username = request.json.get('username')
    password = request.json.get('password')
    # Validación simplificada de usuario y contraseña
    if username == 'admin' and password == 'password':
        access_token = create_access_token(identity={'username': username}, expires_delta=timedelta(hours=1))
        return jsonify(access_token=access_token), 200
    else:
        return jsonify({'message': 'Credenciales inválidas'}), 401
