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

"""Field validators shared by the services that copy the nonconformity pattern.

Each helper reads one key that the caller has already checked is present and
raises ``ValidationError`` with a Spanish message suitable for flashing.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any, TypeVar

from .errors import ValidationError

E = TypeVar("E", bound=enum.Enum)


def text(data: Mapping[str, Any], key: str, *, required: bool = False,
         max_length: int | None = None, strip: bool = True) -> Any:
    """Text value: required ones must be non-blank; optional blank ones become ``None``.

    Forms post an empty string for a blank optional field while stored rows hold
    NULL, so normalising here keeps an unchanged edit a no-op.
    """
    value = data[key]
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"El campo «{key}» debe ser texto.")
    blank = not value.strip()
    if blank and not required:
        return None
    value = value.strip() if strip else value
    if blank:
        raise ValidationError(f"El campo «{key}» es obligatorio.")
    if max_length is not None and len(value) > max_length:
        raise ValidationError(
            f"El campo «{key}» admite como máximo {max_length} caracteres."
        )
    return value


def required_date(data: Mapping[str, Any], key: str) -> date:
    """A real ``date`` (not a ``datetime`` or text)."""
    value = data[key]
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValidationError(f"El campo «{key}» es obligatorio y debe ser una fecha.")
    return value


def enum_member(data: Mapping[str, Any], key: str, enum_cls: type[E]) -> E:
    """An enum member, given either as the member or as its name."""
    value = data[key]
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and value in enum_cls.__members__:
        return enum_cls[value]
    raise ValidationError(f"El valor del campo «{key}» no es válido.")


def require_keys(data: Mapping[str, Any], keys: frozenset[str]) -> None:
    """Raise unless every key in ``keys`` is present in ``data``."""
    missing = keys - set(data)
    if missing:
        raise ValidationError(f"Faltan campos obligatorios: {', '.join(sorted(missing))}.")


def reject_unknown(data: Mapping[str, Any], allowed: frozenset[str]) -> None:
    """Raise when ``data`` holds a key outside the whitelist."""
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationError(f"Campos no permitidos: {', '.join(unknown)}.")


def integer(data: Mapping[str, Any], key: str, *, required: bool = False,
            minimum: int | None = None, maximum: int | None = None) -> int | None:
    """Whole number within optional bounds; optional ones may be ``None``."""
    value = data[key]
    if value is None and not required:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"El campo «{key}» debe ser un número entero.")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValidationError(f"El campo «{key}» está fuera del rango permitido.")
    return value


def boolean(data: Mapping[str, Any], key: str) -> bool | None:
    """A real boolean (not 0/1 or text); the nullable column also accepts ``None``."""
    value = data[key]
    if value is None or isinstance(value, bool):
        return value
    raise ValidationError(f"El campo «{key}» debe ser verdadero o falso.")


def optional_datetime(data: Mapping[str, Any], key: str) -> datetime | None:
    """A ``datetime`` or an ISO 8601 string, stored naive in UTC; ``None`` is allowed."""
    value = data[key]
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime):
        raise ValidationError(f"El campo «{key}» debe ser una fecha y hora válida.")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
