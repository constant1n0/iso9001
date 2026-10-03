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

import logging
from pathlib import Path

from flask import Flask, has_request_context, request


security_logger = logging.getLogger("security")
security_logger.setLevel(logging.INFO)
security_logger.propagate = False


def init_security_logging(app: Flask) -> None:
    """Configure the security file handler for one application instance."""
    for handler in list(security_logger.handlers):
        if getattr(handler, "_iso9001_security_handler", False):
            security_logger.removeHandler(handler)
            handler.close()

    if not app.config.get("SECURITY_LOG_ENABLED", True):
        return

    log_path = Path(app.config["SECURITY_LOG_FILE"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._iso9001_security_handler = True
    security_logger.addHandler(handler)


def get_client_ip():
    """Obtiene la IP del cliente considerando proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def log_login_attempt(username, success, reason=None):
    """Registra intentos de login."""
    ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')

    if success:
        security_logger.info(
            f"LOGIN_SUCCESS | user={username} | ip={ip} | user_agent={user_agent}"
        )
    else:
        security_logger.warning(
            f"LOGIN_FAILED | user={username} | ip={ip} | reason={reason or 'invalid_credentials'} | user_agent={user_agent}"
        )


def log_logout(username):
    """Registra cierre de sesión."""
    ip = get_client_ip()
    security_logger.info(f"LOGOUT | user={username} | ip={ip}")


def log_rate_limit_exceeded(endpoint):
    """Registra cuando se excede el límite de peticiones."""
    ip = get_client_ip()
    security_logger.warning(
        f"RATE_LIMIT_EXCEEDED | endpoint={endpoint} | ip={ip}"
    )


def log_password_reset_request(email, success):
    """Registra solicitudes de recuperación de contraseña."""
    ip = get_client_ip()

    if success:
        security_logger.info(
            f"PASSWORD_RESET_REQUEST | email={email} | ip={ip}"
        )
    else:
        security_logger.warning(
            f"PASSWORD_RESET_REQUEST_FAILED | email={email} | ip={ip}"
        )


def log_password_change(username, success):
    """Registra cambios de contraseña."""
    ip = get_client_ip()

    if success:
        security_logger.info(f"PASSWORD_CHANGE_SUCCESS | user={username} | ip={ip}")
    else:
        security_logger.warning(f"PASSWORD_CHANGE_FAILED | user={username} | ip={ip}")


def log_suspicious_activity(activity_type, details):
    """Registra actividad sospechosa."""
    ip = get_client_ip()
    security_logger.warning(
        f"SUSPICIOUS_ACTIVITY | type={activity_type} | ip={ip} | details={details}"
    )


def _field(value):
    """One log field: no separators or control characters, so values cannot forge lines."""
    text = "-" if value is None else str(value)
    return "".join(c if c.isprintable() and c != "|" else "_" for c in text)[:150] or "-"


def _client_ip_or_dash():
    """Client IP inside a request, ``-`` for CLI and other non-request callers."""
    return _field(get_client_ip()) if has_request_context() else "-"


def log_api_token_issued(prefix, username, actor_label):
    """Registra la emisión de un token de API (nunca el token ni su hash)."""
    security_logger.info(
        f"API_TOKEN_ISSUED | prefix={_field(prefix)} | user={_field(username)} "
        f"| by={_field(actor_label)} | ip={_client_ip_or_dash()}"
    )


def log_api_token_revoked(prefix, actor_label):
    """Registra la revocación de un token de API."""
    security_logger.info(
        f"API_TOKEN_REVOKED | prefix={_field(prefix)} | by={_field(actor_label)} "
        f"| ip={_client_ip_or_dash()}"
    )


def log_api_token_auth_failed(reason, prefix=None):
    """Registra un token rechazado; ``reason`` y ``prefix`` vienen de AuthenticationFailed."""
    security_logger.warning(
        f"API_TOKEN_AUTH_FAILED | reason={_field(reason)} | prefix={_field(prefix)} "
        f"| ip={_client_ip_or_dash()}"
    )
