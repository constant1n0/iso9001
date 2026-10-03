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

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from ..forms import MejoraForm
from ..schemas import MejoraSchema
from ..extensions import db
from ..services import crud, improvements
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .json_register import register_json_api

bp = Blueprint('mejora', __name__, url_prefix='/mejoras')


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
@require_permission('create', 'improvements')
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
@require_permission('update', 'improvements')
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

# API JSON: listar, crear, actualizar y eliminar (sin caché; ver json_register)
register_json_api(bp, improvements, MejoraSchema, 'Mejora eliminada correctamente', rule='/api/', prefix='api_')
