from flask import jsonify

def handle_exception(e):
    return jsonify({'message': str(e)}), 500
