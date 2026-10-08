"""What each tool does with a module: validate input, call the service, serialise."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from ..services import audit, crud
from ..services.actor import Actor
from ..services.errors import ValidationError
from .registry import MODULES, FieldDef, Module


def get_module(slug: str) -> Module:
    try:
        return MODULES[slug]
    except KeyError:
        raise ValidationError(
            f"Módulo desconocido: «{slug}». Módulos disponibles: {', '.join(MODULES)}."
        ) from None


def serialise(row: Any, module: Module | None = None) -> dict[str, Any]:
    """JSON-safe columns (credentials excluded) plus ``id``, whatever the key column is.

    A module's ``extras`` (values that are not columns, such as ``rol_ids``)
    are added after the columns.
    """
    record = audit.snapshot(row)
    record["id"] = sa_inspect(row).mapper.primary_key_from_instance(row)[0]
    if module is not None and module.extras is not None:
        record.update(audit.json_safe(module.extras(row)))
    return record


def _filter_value(field: FieldDef, value: Any) -> Any:
    name = field.name
    if field.type == "date":
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValidationError(f"El filtro «{name}» debe ser una fecha AAAA-MM-DD.") from None
    if field.type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ValidationError(f"El filtro «{name}» debe ser un número entero.")
    if field.type == "boolean":
        if isinstance(value, bool):
            return value
        raise ValidationError(f"El filtro «{name}» debe ser verdadero o falso.")
    if not isinstance(value, str):
        raise ValidationError(f"El filtro «{name}» debe ser texto.")
    if field.type != "enum":
        return value
    if value not in field.allowed:
        raise ValidationError(f"El filtro «{name}» admite: {', '.join(field.allowed)}.")
    return field.members[value] if field.members is not None else value


def _clean_filters(module: Module, filters: dict[str, Any]) -> dict[str, Any]:
    known = {f.name: f for f in module.filters}
    unknown = sorted(set(filters) - set(known))
    if unknown:
        offered = ", ".join(known) or "ninguno"
        raise ValidationError(f"Filtros no admitidos: {', '.join(unknown)}. Admitidos: {offered}.")
    return {name: _filter_value(known[name], value) for name, value in filters.items()}


def list_records(session: Session, actor: Actor, module: Module,
                 filters: dict[str, Any] | None, page: int, per_page: int) -> dict[str, Any]:
    """One bounded page (``crud.page_bounds``) of a module, filtered, paged in the database."""
    criteria = _clean_filters(module, filters or {})
    page, per_page = crud.page_bounds(page, per_page)
    rows, total = module.service.list_page(
        session, actor, **criteria, page=page, per_page=per_page
    )
    return {
        "module": module.slug, "items": [serialise(row, module) for row in rows], "total": total,
        "page": page, "per_page": per_page, "has_more": page * per_page < total,
    }


def get_record(session: Session, actor: Actor, module: Module, record_id: int) -> dict[str, Any]:
    return serialise(module.service.get(session, actor, record_id), module)


def _clean_data(module: Module, data: dict[str, Any]) -> dict[str, Any]:
    """ISO text becomes ``date`` for date fields; the service validates everything else."""
    dates = {f.name for f in module.fields if f.type == "date"}
    clean = dict(data)
    for name in dates & clean.keys():
        if isinstance(clean[name], str):
            try:
                clean[name] = date.fromisoformat(clean[name])
            except ValueError:
                raise ValidationError(
                    f"El campo «{name}» debe ser una fecha AAAA-MM-DD."
                ) from None
    return clean


def create_record(session: Session, actor: Actor, module: Module, data: dict[str, Any]) -> dict[str, Any]:
    return serialise(module.service.create(session, actor, _clean_data(module, data)), module)


def update_record(session: Session, actor: Actor, module: Module, record_id: int,
                  data: dict[str, Any]) -> dict[str, Any]:
    record = module.service.update(session, actor, record_id, _clean_data(module, data))
    return serialise(record, module)
