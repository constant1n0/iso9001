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

The account actions (deactivate, reactivate, send a reset link) are POST-only
and need the update grant; they flash the outcome or the refusal on the list
and write one security-log line when something happened. The reset link is
rate limited per client, counting only requests that pass the grant check, and
never shows why mail failed: the exception types go to the application log.
"""

import logging
from collections.abc import Callable, Iterable
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from flask_wtf import FlaskForm

from ..extensions import db, limiter
from ..forms import UserCreateForm, UserEditForm
from ..services import users
from ..services.errors import Conflict, ValidationError
from ..utils.password_reset_mail import (
    ResetEmailConfigurationError,
    ResetEmailError,
    describe_failure,
    send_admin_reset_email,
)
from ..utils.permissions import require_permission
from ..utils.security_logger import log_admin_reset_link, log_user_status_change
from ..utils.web_actor import current_actor

bp = Blueprint('users', __name__, url_prefix='/usuarios')
logger = logging.getLogger(__name__)

CREATE_FIELDS = ('username', 'email', 'role', 'password')
EDIT_FIELDS = ('email', 'role')
# Messages by target state: ``True`` is reactivation, ``False`` deactivation.
STATE_CHANGED = {
    True: 'Usuario reactivado exitosamente',
    False: 'Usuario desactivado exitosamente',
}
STATE_UNCHANGED = {
    True: 'El usuario ya estaba activo.',
    False: 'El usuario ya estaba desactivado.',
}
LINK_SENT = 'Se ha enviado un enlace para restablecer la contraseña.'
MAIL_FAILED = 'No se ha podido enviar el correo. Inténtalo de nuevo más tarde.'
# Refusal messages by security-log reason; mail problems share one message, so
# configuration details stay in the log and never reach the screen.
LINK_REFUSED = {
    'no_email': 'El usuario no tiene correo electrónico: añade uno antes de enviar '
                'el enlace.',
    'inactive': 'No se puede enviar un enlace a una cuenta desactivada.',
    'configuration': MAIL_FAILED,
    'delivery': MAIL_FAILED,
}


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


@bp.route('/<int:user_id>/desactivar', methods=['POST'])
@login_required
@require_permission('update', 'users')
def deactivate_user(user_id: int) -> ResponseReturnValue:
    return _set_active(user_id, False)


@bp.route('/<int:user_id>/reactivar', methods=['POST'])
@login_required
@require_permission('update', 'users')
def reactivate_user(user_id: int) -> ResponseReturnValue:
    return _set_active(user_id, True)


def _set_active(user_id: int, active: bool) -> ResponseReturnValue:
    """Deactivate or reactivate; deactivation also revokes the user's API tokens."""
    actor = current_actor()
    user = users.get(db.session, actor, user_id)
    username = user.username
    if user.active == active:
        flash(STATE_UNCHANGED[active], 'info')
    elif _saved(lambda: users.set_active(db.session, actor, user_id, active)):
        log_user_status_change(username, active, actor.label)
        flash(STATE_CHANGED[active], 'success')
    return redirect(url_for('users.list_users'))


# The limiter is innermost so that refused requests (anonymous, or without the
# grant) never spend the administrators' budget.
@bp.route('/<int:user_id>/enviar-enlace', methods=['POST'])
@login_required
@require_permission('update', 'users')
@limiter.limit('10 per hour')
def send_reset_link(user_id: int) -> ResponseReturnValue:
    """Mail the user a password-reset link with the administrator wording."""
    actor = current_actor()
    user = users.get(db.session, actor, user_id)
    reason: str | None = None
    if not user.email:
        reason = 'no_email'
    elif not user.is_active:
        reason = 'inactive'
    else:
        try:
            send_admin_reset_email(user)
        except ResetEmailError as error:
            reason = (
                'configuration'
                if isinstance(error, ResetEmailConfigurationError)
                else 'delivery'
            )
            logger.warning(
                'Administrator reset e-mail not sent: %s', describe_failure(error)
            )
    log_admin_reset_link(user.username, actor.label, reason=reason)
    if reason is None:
        flash(LINK_SENT, 'success')
    else:
        flash(LINK_REFUSED[reason], 'danger')
    return redirect(url_for('users.list_users'))
