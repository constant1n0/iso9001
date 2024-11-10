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

from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user
from ..models import RoleEnum

def role_required(required_role):
    """
    Decorador para restringir el acceso en función del rol del usuario.
    El rol 'ADMINISTRADOR' tiene acceso completo a todas las rutas protegidas.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Por favor, inicia sesión para acceder a esta página.", "warning")
                return redirect(url_for("auth.login"))
            # Permitir siempre el acceso si el rol es ADMINISTRADOR
            if current_user.role != required_role and current_user.role != RoleEnum.ADMINISTRADOR:
                flash("No tienes permiso para acceder a esta página.", "danger")
                return redirect(url_for("dashboard.dashboard"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
