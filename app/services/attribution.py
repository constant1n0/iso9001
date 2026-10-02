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

"""Explicit record attribution: who created or changed a record, and when.

Stamping is never implicit (no column defaults, no flush hooks) so that a
missing stamp stays visible. Services call these helpers next to each write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .actor import Actor


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stamp_created(instance: Any, actor: Actor, at: datetime | None = None) -> None:
    """Set ``created_*`` and ``updated_*`` to the same instant and actor."""
    moment = at if at is not None else _now()
    instance.created_at = moment
    instance.created_by_id = actor.user_id
    stamp_updated(instance, actor, at=moment)


def stamp_updated(instance: Any, actor: Actor, at: datetime | None = None) -> None:
    """Set ``updated_*``; a system actor without a user id stores None."""
    instance.updated_at = at if at is not None else _now()
    instance.updated_by_id = actor.user_id
