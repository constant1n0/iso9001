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

import ipaddress
import re
from urllib.parse import quote, urlsplit

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash
from ..models import User
from ..forms import LoginForm, PasswordResetRequestForm, PasswordResetForm
from ..extensions import limiter, mail
from ..utils.security_logger import (
    log_login_attempt, log_logout,
    log_password_reset_request, log_password_change
)

bp = Blueprint('auth', __name__)

HOST_LABEL_PATTERN = re.compile(
    r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
)
GENERIC_RESET_MESSAGE = (
    'Se ha enviado un correo con instrucciones para restablecer tu contraseña.'
)
INVALID_RESET_MESSAGE = 'El enlace de recuperación es inválido o ha expirado.'


class ResetEmailError(RuntimeError):
    """Base exception for password-reset email failures."""


class ResetEmailConfigurationError(ResetEmailError):
    """Raised when the canonical reset origin is missing or unsafe."""


class ResetEmailDeliveryError(ResetEmailError):
    """Raised when the configured mail transport cannot send a reset email."""


def _is_valid_hostname(hostname: str) -> bool:
    """Return whether a hostname is an ASCII DNS name or IP address."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass

    if not hostname.isascii() or len(hostname) > 253:
        return False
    return all(
        HOST_LABEL_PATTERN.fullmatch(label) is not None
        for label in hostname.split('.')
    )


def _validated_password_reset_origin() -> str:
    """Return the configured root HTTPS origin or fail closed."""
    value = current_app.config.get('PASSWORD_RESET_BASE_URL')
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or '\\' in value
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        )
    ):
        raise ResetEmailConfigurationError('Invalid password reset base URL')

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ResetEmailConfigurationError(
            'Invalid password reset base URL'
        ) from error

    if (
        parsed.scheme != 'https'
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ('', '/')
        or parsed.hostname is None
        or not _is_valid_hostname(parsed.hostname)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ResetEmailConfigurationError('Invalid password reset base URL')

    return f'https://{parsed.netloc}'


def build_password_reset_url(token: str) -> str:
    """Build a reset URL without consulting request-controlled host data."""
    origin = _validated_password_reset_origin()
    return f"{origin}/reset_password/{quote(token, safe='')}"


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            log_login_attempt(username, success=True)
            flash('Inicio de sesión exitoso', 'success')
            return redirect(url_for('dashboard.dashboard'))
        else:
            log_login_attempt(username, success=False, reason='invalid_credentials')
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


def send_reset_email(user: User) -> None:
    """Send a password-reset email through the configured canonical origin."""
    token = user.get_reset_token()
    reset_url = build_password_reset_url(token)
    msg = Message(
        'Recuperación de Contraseña - ISO9001 QMS',
        recipients=[user.email]
    )
    msg.body = f'''Para restablecer tu contraseña, visita el siguiente enlace:

{reset_url}

Este enlace expirará en 1 hora.

Si no solicitaste este cambio, ignora este mensaje.
'''
    try:
        mail.send(msg)
    except Exception as error:
        raise ResetEmailDeliveryError(
            'Could not send password reset email'
        ) from error


@bp.route('/reset_password_request', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            try:
                send_reset_email(user)
            except ResetEmailError:
                log_password_reset_request(form.email.data, success=False)
            else:
                log_password_reset_request(form.email.data, success=True)
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
