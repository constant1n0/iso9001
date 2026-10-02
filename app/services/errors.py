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

"""Domain errors carrying a safe, user-facing message (no internals)."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for errors that adapters translate into responses."""

    default_message = "Se ha producido un error."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFound(DomainError):
    """The requested record does not exist."""

    default_message = "Recurso no encontrado."


class Conflict(DomainError):
    """The operation conflicts with existing data (e.g. a duplicate)."""

    default_message = "La operación entra en conflicto con datos existentes."


class PermissionDenied(DomainError):
    """The actor is not allowed to perform the operation."""

    default_message = "No tienes permiso para realizar esta acción."


class ValidationError(DomainError):
    """The submitted data is not valid."""

    default_message = "Los datos proporcionados no son válidos."
