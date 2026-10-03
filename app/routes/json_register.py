# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""JSON API shared by the registers: thin handlers over a crud-based service.

``register_json_api`` adds list/create/update/delete handlers to a blueprint.
The service raises domain errors that the registered error handlers turn into
404/409/422/403 JSON bodies (rolling the session back for 409/422); this
module only parses the body, commits and serializes with the marshmallow
schema, so the response keys match the GET listing.
"""

from flask import jsonify, request
from flask_login import login_required

from ..extensions import db
from ..utils.web_actor import current_actor


def register_json_api(bp, service, schema_cls, deleted_message, *, rule="/", prefix=""):
    """Add the four JSON endpoints for ``service`` under ``rule`` of ``bp``.

    ``prefix`` namespaces the endpoint names when the blueprint also serves HTML.
    """
    one, many = schema_cls(), schema_cls(many=True)
    item_rule = f"{rule}<int:id>"

    def listing():
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        items, _total = service.list_page(db.session, current_actor(), page=page, per_page=per_page)
        return jsonify(many.dump(items)), 200

    def create():
        json_data = request.get_json()
        if not json_data:
            return jsonify({'message': 'No se proporcionaron datos'}), 400
        created = service.create(db.session, current_actor(), json_data)
        db.session.commit()
        return jsonify(one.dump(created)), 201

    def update(id):
        json_data = request.get_json()
        if not json_data:
            return jsonify({'message': 'No se proporcionaron datos'}), 400
        updated = service.update(db.session, current_actor(), id, json_data)
        db.session.commit()
        return jsonify(one.dump(updated)), 200

    def delete(id):
        service.delete(db.session, current_actor(), id)
        db.session.commit()
        return jsonify({'message': deleted_message}), 200

    for view, path, method in (
        (listing, rule, 'GET'), (create, rule, 'POST'),
        (update, item_rule, 'PUT'), (delete, item_rule, 'DELETE'),
    ):
        bp.add_url_rule(path, f"{prefix}{view.__name__}", login_required(view), methods=[method])
