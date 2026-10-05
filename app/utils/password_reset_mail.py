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

"""Password-reset e-mails, for the self-service request and for administrators.

Both variants carry the same timed link (``User.get_reset_token``, one hour)
built from ``PASSWORD_RESET_BASE_URL``, never from request-controlled host
data. Failures raise a ``ResetEmailError`` subclass whose message is fixed and
safe to log; the caller decides what the user sees.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from flask import current_app
from flask_mail import Message

from ..extensions import mail

if TYPE_CHECKING:
    from ..models import User

HOST_LABEL_PATTERN = re.compile(
    r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
)
SUBJECT = 'Recuperación de Contraseña - ISO9001 QMS'
SELF_SERVICE_INTRO = 'Para restablecer tu contraseña, visita el siguiente enlace:'
SELF_SERVICE_CLOSING = 'Si no solicitaste este cambio, ignora este mensaje.'
ADMIN_INTRO = (
    'Un administrador de ISO9001 QMS ha solicitado que restablezcas tu '
    'contraseña. Para elegir una nueva, visita el siguiente enlace:'
)
ADMIN_CLOSING = (
    'Si no esperabas este mensaje, puedes ignorarlo: tu contraseña actual '
    'seguirá siendo válida.'
)


class ResetEmailError(RuntimeError):
    """Base exception for password-reset email failures."""


class ResetEmailConfigurationError(ResetEmailError):
    """Raised when the canonical reset origin is missing or unsafe."""


class ResetEmailDeliveryError(ResetEmailError):
    """Raised when the configured mail transport cannot send a reset email."""


def describe_failure(error: ResetEmailError) -> str:
    """Name the failure and its cause by exception type, for operators.

    Only class names are reported: a transport message can carry the server,
    credentials or the recipient address, and the link must never be logged.
    """
    cause = error.__cause__
    cause_name = type(cause).__name__ if cause is not None else 'none'
    return f'{type(error).__name__} (cause: {cause_name})'


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


def send_reset_email(user: User) -> None:
    """Send the reset link the user asked for on the login screen."""
    _send(user, SELF_SERVICE_INTRO, SELF_SERVICE_CLOSING)


def send_admin_reset_email(user: User) -> None:
    """Send the reset link an administrator requested for ``user``."""
    _send(user, ADMIN_INTRO, ADMIN_CLOSING)


def _send(user: User, intro: str, closing: str) -> None:
    """Mail ``user`` a fresh one-hour reset link between ``intro`` and ``closing``."""
    reset_url = build_password_reset_url(user.get_reset_token())
    msg = Message(SUBJECT, recipients=[user.email])
    msg.body = (
        f'{intro}\n\n{reset_url}\n\nEste enlace expirará en 1 hora.\n\n{closing}\n'
    )
    try:
        mail.send(msg)
    except Exception as error:
        raise ResetEmailDeliveryError(
            'Could not send password reset email'
        ) from error
