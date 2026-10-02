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

"""Audit recorder for domain writes.

Services call :func:`record` next to each create, update or delete; the
adapter owns the commit.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from .. import models
from ..extensions import db
from ..models import AuditLog
from .actor import Actor

ACTIONS = frozenset({"create", "update", "delete"})
SENSITIVE_MARKERS = ("password", "token", "secret")

# Every domain model is audited except the user table (credentials) and the
# audit log itself.
AUDITED_MODELS: tuple[type, ...] = tuple(
    sorted(
        (
            mapper.class_
            for mapper in db.Model.registry.mappers
            if mapper.class_ not in (models.User, AuditLog)
        ),
        key=lambda cls: cls.__tablename__,
    )
)



def is_sensitive(name: str) -> bool:
    """Whether a column or key name must never reach the audit log."""
    lowered = name.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def json_safe(value: Any) -> Any:
    """Convert a column value to a JSON-safe representation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return json_safe(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    return str(value)


def snapshot(instance: Any) -> dict[str, Any]:
    """Column values of ``instance`` as a JSON-safe dict, minus sensitive ones."""
    mapper = sa_inspect(instance).mapper
    return {
        attr.key: json_safe(getattr(instance, attr.key))
        for attr in mapper.column_attrs
        if not is_sensitive(attr.key)
    }


def _scrub(values: dict[str, Any] | None) -> dict[str, Any] | None:
    if values is None:
        return None
    return {k: json_safe(v) for k, v in values.items() if not is_sensitive(k)}


def _primary_key(instance: Any) -> int | None:
    return sa_inspect(instance).mapper.primary_key_from_instance(instance)[0]


def _diff(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = [
        key
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    ]
    return (
        {key: before.get(key) for key in changed if key in before},
        {key: after.get(key) for key in changed if key in after},
    )


def record(
    session: Session,
    actor: Actor,
    action: str,
    instance: Any,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """Add one audit row for ``instance``; the caller commits.

    create: ``after`` is the full snapshot. update: pass the ``before``
    snapshot taken ahead of the change; only differing keys are stored on
    both sides. delete: ``before`` is the full snapshot (decision D3).
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown audit action: {action!r}")

    if action == "create" and _primary_key(instance) is None:
        # The primary key is assigned by the database; flush this one row first.
        session.flush([instance])

    scrubbed_before, scrubbed_after = _scrub(before), _scrub(after)
    if action == "create":
        stored_before = None
        stored_after = snapshot(instance) if scrubbed_after is None else scrubbed_after
    elif action == "delete":
        stored_before = snapshot(instance) if scrubbed_before is None else scrubbed_before
        stored_after = None
    else:
        stored_before, stored_after = _diff(
            scrubbed_before or {},
            snapshot(instance) if scrubbed_after is None else scrubbed_after,
        )

    row = AuditLog(
        entity_type=sa_inspect(instance).mapper.local_table.name,
        entity_id=_primary_key(instance),
        action=action,
        actor_user_id=actor.user_id,
        actor_label=actor.label,
        channel=actor.channel,
        before=stored_before,
        after=stored_after,
        request_id=request_id,
    )
    session.add(row)
    return row

