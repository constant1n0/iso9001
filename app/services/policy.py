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

The approved role-by-action matrix (decision D1). Roles may read, write
(create/update) or delete each resource:

- Nonconformities, improvements, surveys, training, stakeholders: every role
  reads and writes; only administrators delete.
- Audits: administrators and auditors read and write; administrators delete.
- Documents: every role reads, administrators and auditors write, nobody
  deletes (decisions DC4 and DC6 of ``document-control``). The document
  services narrow it further: other roles read only revisions in force, and
  only administrators approve or withdraw.
- JSON registers (roles, risks and opportunities, training resources, process
  operations, audit indicators): every role reads; administrators and auditors
  write; administrators delete.
- Users and the audit log: administrators and auditors read; administrators
  write; nobody deletes (policy entries only, no routes exist yet).

- API tokens: administrators list (read), issue (create) and revoke (update);
  nobody deletes, and the ``mcp`` channel can never touch them.
- People and competence (required per role, demonstrated per person): every
  role reads; administrators and auditors write; administrators delete
  (decision Q1 of ``qms-people``).
- Corrective actions follow nonconformities; verifying one is further limited
  to administrators and auditors by its service (decision N4 of ``nc-capa-loop``).

Seams: the ``mcp`` channel never deletes and never writes documents (they are
written in the web only), and token scopes intersect the role.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    USERS = "users"
    AUDIT_LOG = "audit_log"
    API_TOKENS = "api_tokens"
    PEOPLE = "people"
    COMPETENCE = "competence"
    CORRECTIVE_ACTIONS = "corrective_actions"


_ALL = frozenset(RoleEnum)
_ADMIN = frozenset({RoleEnum.ADMINISTRADOR})
_ADMIN_AUDITOR = frozenset({RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR})
_NOBODY: frozenset[RoleEnum] = frozenset()


@dataclass(frozen=True)
class Grant:
    """Roles allowed to read, to write (create/update) and to delete."""

    read: frozenset[RoleEnum]
    write: frozenset[RoleEnum]
    delete: frozenset[RoleEnum]

    def roles_for(self, action: Action) -> frozenset[RoleEnum]:
        if action is Action.READ:
            return self.read
        return self.delete if action is Action.DELETE else self.write


_OPEN_REGISTER = Grant(_ALL, _ALL, _ADMIN)  # everyone works, administrators delete
_JSON_REGISTER = Grant(_ALL, _ADMIN_AUDITOR, _ADMIN)
_MATRIX: dict[Resource, Grant] = {
    Resource.NONCONFORMITIES: _OPEN_REGISTER,
    Resource.IMPROVEMENTS: _OPEN_REGISTER,
    Resource.CUSTOMER_SATISFACTION: _OPEN_REGISTER,
    Resource.TRAINING: _OPEN_REGISTER,
    Resource.INTERESTED_PARTIES: _OPEN_REGISTER,
    Resource.AUDITS: Grant(_ADMIN_AUDITOR, _ADMIN_AUDITOR, _ADMIN),
    Resource.DOCUMENTS: Grant(_ALL, _ADMIN_AUDITOR, _NOBODY),
    Resource.AUDIT_INDICATORS: _JSON_REGISTER,
    Resource.ROLES_RESPONSIBILITIES: _JSON_REGISTER,
    Resource.RISKS_OPPORTUNITIES: _JSON_REGISTER,
    Resource.TRAINING_RESOURCES: _JSON_REGISTER,
    Resource.PROCESS_OPERATIONS: _JSON_REGISTER,
    Resource.USERS: Grant(_ADMIN_AUDITOR, _ADMIN, _NOBODY),
    Resource.AUDIT_LOG: Grant(_ADMIN_AUDITOR, _ADMIN, _NOBODY),
    # Read lists, create issues, update revokes; tokens are never hard-deleted.
    Resource.API_TOKENS: Grant(_ADMIN, _ADMIN, _NOBODY),
    Resource.PEOPLE: Grant(_ALL, _ADMIN_AUDITOR, _ADMIN),
    Resource.COMPETENCE: Grant(_ALL, _ADMIN_AUDITOR, _ADMIN),
    Resource.CORRECTIVE_ACTIONS: _OPEN_REGISTER,
}


def can(actor: Actor, action: Action, resource: Resource) -> bool:
    """Return whether ``actor`` may perform ``action`` on ``resource``."""
    action = Action(action)
    resource = Resource(resource)
    if actor.channel == "mcp" and (
        action is Action.DELETE or resource is Resource.API_TOKENS
    ):
        return False  # an agent never deletes and never manages its own credentials
    if actor.channel == "mcp" and resource is Resource.DOCUMENTS and action is not Action.READ:
        return False  # documents are written in the web only
    if actor.scopes is not None:
        needed = "read" if action is Action.READ else "write"
        if needed not in actor.scopes:
            return False
    return actor.role in _MATRIX[resource].roles_for(action)


def require(actor: Actor, action: Action, resource: Resource) -> None:
    """Raise ``PermissionDenied`` unless ``can(actor, action, resource)``."""
    if not can(actor, action, resource):
        raise PermissionDenied()
