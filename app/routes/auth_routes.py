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

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash
from ..models import User
from ..forms import LoginForm, PasswordResetRequestForm, PasswordResetForm
# ``mail`` stays importable here: tests patch ``auth_routes.mail.send``.
from ..extensions import limiter, mail  # noqa: F401
from ..utils.password_reset_mail import ResetEmailError, send_reset_email
from ..utils.security_logger import (
    log_login_attempt, log_logout,
    log_password_reset_request, log_password_change
)

bp = Blueprint('auth', __name__)

GENERIC_RESET_MESSAGE = (
    'Se ha enviado un correo con instrucciones para restablecer tu contraseña.'
)
INVALID_RESET_MESSAGE = 'El enlace de recuperación es inválido o ha expirado.'


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            # login_user() refuses an inactive user by returning False, so the
            # result decides; the response matches wrong credentials exactly.
            if login_user(user):
                log_login_attempt(username, success=True)
                flash('Inicio de sesión exitoso', 'success')
                return redirect(url_for('dashboard.dashboard'))
            reason = 'inactive'
        else:
            reason = 'invalid_credentials'
        log_login_attempt(username, success=False, reason=reason)
        flash('Credenciales inválidas', 'danger')
        return redirect(url_for('auth.login'))
    return render_template('login.html', form=form)

@bp.route('/logout')
@login_required
def logout():
    username = current_user.username
    logout_user()
    log_logout(username)
    flash('Has cerrado sesión', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/reset_password_request', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        # Addresses are unique regardless of case (legacy rows may be mixed case).
        email = form.email.data.strip().lower()
        user = (
            User.query.filter(func.lower(User.email) == email)
            .order_by(User.id)
            .first()
        )
        if user and not user.is_active:
            # Same response as an unknown address; only the log tells them apart.
            log_password_reset_request(email, success=False, reason='inactive')
        elif user:
            try:
                send_reset_email(user)
            except ResetEmailError:
                log_password_reset_request(email, success=False)
            else:
                log_password_reset_request(email, success=True)
        flash(GENERIC_RESET_MESSAGE, 'info')
        return redirect(url_for('auth.login'))

    return render_template('reset_password_request.html', form=form)


@bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    verification = User.verify_reset_token(token)
    if verification is None:
        flash(INVALID_RESET_MESSAGE, 'warning')
        return redirect(url_for('auth.reset_password_request'))

    form = PasswordResetForm()
    if form.validate_on_submit():
        username = verification.user.username
        new_password_hash = generate_password_hash(
            form.password.data,
            method='pbkdf2:sha256',
        )
        updated = User.update_password_from_reset(
            verification.user.id,
            verification.expected_password_hash,
            new_password_hash,
        )
        if not updated:
            log_password_change(username, success=False)
            flash(INVALID_RESET_MESSAGE, 'warning')
            return redirect(url_for('auth.reset_password_request'))

        log_password_change(username, success=True)
        flash('Tu contraseña ha sido actualizada exitosamente.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', form=form)
