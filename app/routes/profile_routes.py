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

"""The "Mi perfil" page: every signed-in user manages their own account.

Open to every role (``login_required`` only, no role gate): the page shows the
user's data and their own API tokens, and its forms change the e-mail and the
password (the current password is required) and revoke one of the user's own
tokens. Thin adapters over ``services.users`` self-service and
``services.api_tokens.list_own``/``revoke_own``; issuing tokens stays CLI-only.

A service ``ValidationError`` or ``Conflict`` (a wrong current password, a
duplicate e-mail, a password equal to the current one, a token already
revoked) is rolled back and flashed; the forms come back on the re-rendered
page with what was typed, except passwords. Another user's token, like an
unknown one, is a 404. Each e-mail or password change writes one security-log
line: after the commit when it succeeds, with a reason code when it is refused.

Both credential forms check the current password, so they share one budget of
``CREDENTIAL_LIMIT`` per signed-in user and client address: a stolen session
cannot try more than that many current passwords an hour, and colleagues
behind the same office address do not spend each other's budget. The limiter
is innermost, so anonymous requests are sent to the login page uncounted.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from flask import Blueprint, flash, redirect, render_template, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..forms import EmailChangeForm, PasswordChangeForm
from ..services import api_tokens, users
from ..services.errors import Conflict, DomainError, ValidationError
from ..utils.security_logger import (
    log_api_token_revoked,
    log_email_change,
    log_email_change_failed,
    log_password_change,
)
from ..utils.web_actor import current_actor

bp = Blueprint('profile', __name__, url_prefix='/perfil')

T = TypeVar('T')

EMAIL_CHANGED = 'Correo electrónico actualizado exitosamente'
PASSWORD_CHANGED = 'Contraseña actualizada exitosamente'
TOKEN_REVOKED = 'Token revocado exitosamente'
CREDENTIAL_LIMIT = '10 per hour'
# Security-log reason codes for a refused credential change.
INVALID_FORM = 'invalid_form'
REFUSAL_REASONS = {
    users.WRONG_PASSWORD: 'wrong_current_password',
    users.SAME_PASSWORD: 'same_password',
    users.DUPLICATE_EMAIL: 'duplicate_email',
}
# Label and badge variant by ``api_tokens.status``.
TOKEN_STATES = {
    api_tokens.ACTIVE: ('Activo', 'ok'),
    api_tokens.EXPIRED: ('Caducado', 'warn'),
    api_tokens.REVOKED: ('Revocado', 'danger'),
}


def _credential_client() -> str:
    """Rate-limit key of the credential forms: the signed-in user.

    Keyed by account, not address, so switching addresses buys no extra
    guesses at the current password.
    """
    return f'user:{current_user.get_id()}'


credential_limit = limiter.shared_limit(
    CREDENTIAL_LIMIT, scope='profile-credentials', key_func=_credential_client
)


def _refusal_reason(error: DomainError) -> str:
    """The security-log reason code of a refused service write."""
    fallback = 'conflict' if isinstance(error, Conflict) else 'invalid'
    return REFUSAL_REASONS.get(error.message, fallback)


def _committed(
    write: Callable[[], T],
    on_refusal: Callable[[str], None] | None = None,
) -> T | None:
    """Run a service write and commit it; on a refusal roll back and flash why (None).

    ``on_refusal`` receives the refusal's reason code, after the rollback.
    """
    try:
        result = write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        if on_refusal is not None:
            on_refusal(_refusal_reason(error))
        flash(error.message, 'danger')
        return None
    return result


def _page(
    email_form: EmailChangeForm | None = None,
    password_form: PasswordChangeForm | None = None,
) -> str:
    """The profile page; a form that was not submitted is rendered empty."""
    now = datetime.now(timezone.utc)
    tokens = [
        (token, api_tokens.status(token, now))
        for token in api_tokens.list_own(db.session, current_actor())
    ]
    return render_template(
        'profile/show.html',
        email_form=email_form or EmailChangeForm(formdata=None),
        password_form=password_form or PasswordChangeForm(formdata=None),
        tokens=tokens,
        token_states=TOKEN_STATES,
        active=api_tokens.ACTIVE,
    )


@bp.route('/', methods=['GET'])
@login_required
def show() -> ResponseReturnValue:
    return _page()


@bp.route('/email', methods=['POST'])
@login_required
@credential_limit
def change_email() -> ResponseReturnValue:
    actor = current_actor()
    form = EmailChangeForm()

    def refused(reason: str) -> None:
        log_email_change_failed(actor.label, reason)

    if not form.validate_on_submit():
        refused(INVALID_FORM)
        return _page(email_form=form)
    user = _committed(lambda: users.change_own_email(
        db.session, actor,
        current_password=form.email_current_password.data,
        new_email=form.email.data,
    ), refused)
    if user is None:
        return _page(email_form=form)
    log_email_change(actor.label, user.email)
    flash(EMAIL_CHANGED, 'success')
    return redirect(url_for('profile.show'))


@bp.route('/contrasena', methods=['POST'])
@login_required
@credential_limit
def change_password() -> ResponseReturnValue:
    actor = current_actor()
    form = PasswordChangeForm()

    def refused(reason: str) -> None:
        log_password_change(actor.label, False, reason=reason)

    if not form.validate_on_submit():
        refused(INVALID_FORM)
        return _page(password_form=form)
    user = _committed(lambda: users.change_own_password(
        db.session, actor,
        current_password=form.current_password.data,
        new_password=form.new_password.data,
    ), refused)
    if user is None:
        return _page(password_form=form)
    log_password_change(actor.label, True)
    flash(PASSWORD_CHANGED, 'success')
    return redirect(url_for('profile.show'))


@bp.route('/tokens/<int:token_id>/revocar', methods=['POST'])
@login_required
def revoke_token(token_id: int) -> ResponseReturnValue:
    """Revoke one of the user's own tokens; another user's token is a 404."""
    actor = current_actor()
    token = _committed(lambda: api_tokens.revoke_own(db.session, actor, token_id))
    if token is not None:
        log_api_token_revoked(token.prefix, actor.label)
        flash(TOKEN_REVOKED, 'success')
    return redirect(url_for('profile.show'))
