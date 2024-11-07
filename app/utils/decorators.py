from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user
from ..models import RoleEnum

def role_required(required_role):
    """
    Decorador para restringir el acceso en función del rol del usuario.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Por favor, inicia sesión para acceder a esta página.", "warning")
                return redirect(url_for("auth.login"))
            if current_user.role != required_role:
                flash("No tienes permiso para acceder a esta página.", "danger")
                return redirect(url_for("dashboard.dashboard"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
