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

"""Web authorization adapter: the central policy as a route guard and a template global.

``require_permission`` replaces role-name decorators: routes state the action
and resource, and ``app.services.policy`` decides. A refusal raises
``PermissionDenied``, which the error handlers turn into the usual notice and
redirect to the dashboard. ``can`` is registered as a Jinja global so templates
hide controls with the same rule the services enforce.
"""

from functools import wraps

from flask import current_app
from flask_login import current_user

from ..services import policy
from ..services.policy import Action, Resource
from .web_actor import current_actor


def require_permission(action: Action | str, resource: Resource | str):
    """Decorator: run the view only when the current user may ``action`` the ``resource``.

    Apply it below ``@login_required``. Unknown names fail when the module is
    imported, not on the first request.
    """
    action, resource = Action(action), Resource(resource)

    def decorator(view):
        @wraps(view)
        def guarded(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            policy.require(current_actor(), action, resource)
            return view(*args, **kwargs)

        return guarded

    return decorator


def can(action: Action | str, resource: Resource | str) -> bool:
    """Whether the logged-in user may ``action`` the ``resource`` (False when anonymous)."""
    action, resource = Action(action), Resource(resource)
    if not current_user.is_authenticated:
        return False
    return policy.can(current_actor(), action, resource)
