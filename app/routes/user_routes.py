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

"""Administrator screens for user accounts: thin adapters over ``services.users``.

The list follows the ``USERS`` read grant (administrators and auditors); the
create and edit forms follow the write grant (administrators). Users are never
deleted, so there is no delete route.

A service ``ValidationError`` or ``Conflict`` (a duplicate, your own role, the
last administrator, a value the service rejects) is rolled back and flashed on
the re-rendered form, which keeps what was typed; password fields are never
filled back in. Other domain errors reach the global handlers (an unknown id is
a 404, a policy refusal redirects to the dashboard).
"""

from collections.abc import Callable, Iterable
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from flask_wtf import FlaskForm

from ..extensions import db
from ..forms import UserCreateForm, UserEditForm
from ..services import users
from ..services.errors import Conflict, ValidationError
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('users', __name__, url_prefix='/usuarios')

CREATE_FIELDS = ('username', 'email', 'role', 'password')
EDIT_FIELDS = ('email', 'role')


def _form_data(form: FlaskForm, names: Iterable[str]) -> dict[str, Any]:
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in names}


def _saved(write: Callable[[], object]) -> bool:
    """Run a service write and commit it; on a refusal roll back and flash why."""
    try:
        write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        flash(error.message, 'danger')
        return False
    return True


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'users')
def list_users() -> ResponseReturnValue:
    return render_template('users/list.html',
                           users=users.list_(db.session, current_actor()))


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'users')
def new_user() -> ResponseReturnValue:
    actor = current_actor()
    form = UserCreateForm()
    if form.validate_on_submit() and _saved(
        lambda: users.create(db.session, actor, _form_data(form, CREATE_FIELDS))
    ):
        flash('Usuario creado exitosamente', 'success')
        return redirect(url_for('users.list_users'))
    return render_template('users/new.html', form=form)


@bp.route('/<int:user_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'users')
def edit_user(user_id: int) -> ResponseReturnValue:
    actor = current_actor()
    user = users.get(db.session, actor, user_id)
    form = UserEditForm(obj=user)
    # The select uses enum names as values; preselect the stored role.
    if request.method == 'GET':
        form.role.data = user.role.name
    if form.validate_on_submit() and _saved(
        lambda: users.update(db.session, actor, user_id, _form_data(form, EDIT_FIELDS))
    ):
        flash('Usuario actualizado exitosamente', 'success')
        return redirect(url_for('users.list_users'))
    return render_template('users/edit.html', form=form, user=user)
