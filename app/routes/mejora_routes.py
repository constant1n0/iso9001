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

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from ..forms import MejoraForm
from ..models import Mejora
from ..schemas import MejoraSchema
from ..extensions import db, cache
from ..services import crud, improvements
from ..utils.web_actor import current_actor
from marshmallow import ValidationError

bp = Blueprint('mejora', __name__, url_prefix='/mejoras')

mejora_schema = MejoraSchema()
mejoras_schema = MejoraSchema(many=True)


class _Pagination:
    """The few fields ``mejoras/listar.html`` reads from a pagination object."""

    def __init__(self, page, per_page, total):
        self.page = page
        self.pages = -(-total // per_page)  # ceiling division; 0 when empty
        self.has_prev = page > 1
        self.prev_num = page - 1 if self.has_prev else None
        self.has_next = page < self.pages
        self.next_num = page + 1 if self.has_next else None


FORM_FIELDS = ('no_conformidad', 'accion_correctiva', 'accion_preventiva')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


# Listar todas las mejoras (vista HTML)
@bp.route('/', methods=['GET'])
@login_required
def listar_mejoras():
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = request.args.get('per_page', 10, type=int)
    mejoras, total = improvements.list_page(db.session, current_actor(), page=page, per_page=per_page)
    pagination = _Pagination(page, per_page if per_page >= 1 else crud.DEFAULT_PER_PAGE, total)
    return render_template('mejoras/listar.html', mejoras=mejoras, pagination=pagination)

# Crear una nueva mejora (vista HTML)
@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_mejora():
    form = MejoraForm()
    if form.validate_on_submit():
        improvements.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('Mejora registrada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/nueva.html', form=form)

# Editar una mejora
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_mejora(id):
    actor = current_actor()
    mejora = improvements.get(db.session, actor, id)
    form = MejoraForm(obj=mejora)
    if form.validate_on_submit():
        improvements.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('Mejora actualizada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/editar.html', form=form, mejora=mejora)

# Eliminar una mejora
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_mejora(id):
    improvements.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Mejora eliminada correctamente', 'success')
    return redirect(url_for('mejora.listar_mejoras'))

# API: Obtener todas las mejoras con paginación (JSON)
@bp.route('/api/', methods=['GET'])
@login_required
@cache.cached(timeout=50, query_string=True)
def api_get_mejoras():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    mejoras_paginadas = Mejora.query.paginate(page=page, per_page=per_page, error_out=False)
    return mejoras_schema.jsonify(mejoras_paginadas.items), 200

# API: Crear una nueva mejora (JSON)
@bp.route('/api/', methods=['POST'])
@login_required
def api_add_mejora():
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

# API: Actualizar una mejora (JSON)
@bp.route('/api/<int:id>', methods=['PUT'])
@login_required
def api_update_mejora(id):
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

# API: Eliminar una mejora (JSON)
@bp.route('/api/<int:id>', methods=['DELETE'])
@login_required
def api_delete_mejora(id):
    mejora = Mejora.query.get_or_404(id)
    db.session.delete(mejora)
    db.session.commit()
    return jsonify({'message': 'Mejora eliminada correctamente'}), 200
