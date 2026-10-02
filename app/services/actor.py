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

"""The authenticated principal on whose behalf a service operation runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import RoleEnum

if TYPE_CHECKING:
    from ..models import User

CHANNELS = frozenset({"web", "mcp", "cli", "system"})


@dataclass(frozen=True)
class Actor:
    """Who is acting, with which role, through which channel.

    Attributes:
        user_id: Primary key of the acting user (None for system actors).
        label: Human-readable name kept for attribution.
        role: The user's role.
        channel: One of ``web``, ``mcp``, ``cli``, ``system``.
        scopes: Token scopes (``read``, ``write``); None means unrestricted,
            as for web sessions. Scopes only narrow the role, never widen it.
    """

    user_id: int | None
    label: str
    role: RoleEnum
    channel: str
    scopes: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f"Unknown channel: {self.channel!r}")

    @classmethod
    def from_user(
        cls,
        user: User,
        channel: str,
        scopes: Iterable[str] | None = None,
    ) -> Actor:
        """Build an actor from a ``User`` row; the channel is explicit."""
        return cls(
            user_id=user.id,
            label=user.username,
            role=user.role,
            channel=channel,
            scopes=None if scopes is None else frozenset(scopes),
        )
