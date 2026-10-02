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

"""Central permission policy.

Encodes the effective access matrix as of QF-2 (route decorators plus the
audit form check), so routes and future adapters can share one source:

- Nonconformities: every role, except delete (administrators only).
- Audits and documents: administrators (audits also auditors) for everything.
- Every other resource: every role for every action.

Seams: the ``mcp`` channel never deletes, and token scopes intersect the role.
"""

from __future__ import annotations

from enum import StrEnum

from ..models import RoleEnum
from .actor import Actor
from .errors import PermissionDenied


class Action(StrEnum):
    """Operations an actor may attempt on a resource."""

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class Resource(StrEnum):
    """Protected resources."""

    NONCONFORMITIES = "nonconformities"
    IMPROVEMENTS = "improvements"
    CUSTOMER_SATISFACTION = "customer_satisfaction"
    TRAINING = "training"
    AUDITS = "audits"
    AUDIT_INDICATORS = "audit_indicators"
    DOCUMENTS = "documents"
    INTERESTED_PARTIES = "interested_parties"
    ROLES_RESPONSIBILITIES = "roles_responsibilities"
    RISKS_OPPORTUNITIES = "risks_opportunities"
    TRAINING_RESOURCES = "training_resources"
    PROCESS_OPERATIONS = "process_operations"


_ALL = frozenset(RoleEnum)
_ADMIN = frozenset({RoleEnum.ADMINISTRADOR})
_ADMIN_AUDITOR = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})

# resource -> (roles for read/create/update, roles for delete)
_MATRIX: dict[Resource, tuple[frozenset[RoleEnum], frozenset[RoleEnum]]] = {
    Resource.NONCONFORMITIES: (_ALL, _ADMIN),
    Resource.AUDITS: (_ADMIN_AUDITOR, _ADMIN_AUDITOR),
    Resource.DOCUMENTS: (_ADMIN, _ADMIN),
}
_DEFAULT = (_ALL, _ALL)


def can(actor: Actor, action: Action, resource: Resource) -> bool:
    """Return whether ``actor`` may perform ``action`` on ``resource``."""
    action = Action(action)
    resource = Resource(resource)
    if actor.channel == "mcp" and action is Action.DELETE:
        return False
    if actor.scopes is not None:
        needed = "read" if action is Action.READ else "write"
        if needed not in actor.scopes:
            return False
    write_roles, delete_roles = _MATRIX.get(resource, _DEFAULT)
    roles = delete_roles if action is Action.DELETE else write_roles
    return actor.role in roles


def require(actor: Actor, action: Action, resource: Resource) -> None:
    """Raise ``PermissionDenied`` unless ``can(actor, action, resource)``."""
    if not can(actor, action, resource):
        raise PermissionDenied()
