# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

import logging
import os
from datetime import datetime
from flask import request

# Crear directorio de logs si no existe
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Configurar logger de seguridad
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.INFO)

# Handler para archivo de seguridad
security_handler = logging.FileHandler(os.path.join(LOG_DIR, 'security.log'))
security_handler.setLevel(logging.INFO)

# Formato del log
formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
security_handler.setFormatter(formatter)
security_logger.addHandler(security_handler)


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


def log_registration(username, email, success, reason=None):
    """Registra intentos de registro."""
    ip = get_client_ip()

    if success:
        security_logger.info(
            f"REGISTRATION_SUCCESS | user={username} | email={email} | ip={ip}"
        )
    else:
        security_logger.warning(
            f"REGISTRATION_FAILED | user={username} | email={email} | ip={ip} | reason={reason}"
        )


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
