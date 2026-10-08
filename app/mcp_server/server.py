"""The MCP server: five generic tools over the service layer."""

from __future__ import annotations

from typing import Any

from flask import Flask
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..services import policy
from ..services.policy import Action
from . import context, operations
from .registry import MODULES, FieldDef

INSTRUCTIONS = (
    "Quality management system (ISO 9001). Call qms_modules first: it lists the modules, "
    "their fields, allowed values, filters and what you may do. Messages are in Spanish."
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def _describe(field: FieldDef) -> dict[str, Any]:
    described: dict[str, Any] = {"name": field.name, "type": field.type, "required": field.required}
    if field.allowed:
        described["allowed"] = list(field.allowed)
    if field.note:
        described["note"] = field.note
    return described


def build_server(app: Flask) -> MCPServer:
    mcp = MCPServer("iso9001-qms", instructions=INSTRUCTIONS)

    @mcp.tool(
        name="qms_modules",
        title="List QMS modules",
        description=(
            "List the QMS modules with their fields (type, required, allowed values and an "
            "optional note), the filters qms_list supports and what the calling user may do "
            "in each."
        ),
        annotations=READ_ONLY,
    )
    def qms_modules() -> dict[str, Any]:
        actor = context.current_actor()
        return {
            "role": actor.role.value,
            "scopes": sorted(actor.scopes) if actor.scopes is not None else None,
            "modules": [
                {
                    "slug": module.slug,
                    "label": module.label,
                    "fields": [_describe(f) for f in module.fields],
                    "filters": [_describe(f) for f in module.filters],
                    "permissions": {
                        action.value: policy.can(actor, action, module.resource)
                        for action in (Action.READ, Action.CREATE, Action.UPDATE)
                    },
                }
                for module in MODULES.values()
            ],
        }

    @mcp.tool(
        name="qms_list",
        title="List records of a module",
        description=(
            "List records of one module, newest or ordered as the module defines, one page at a "
            "time (per_page is capped by the server). `filters` accepts only the filters that "
            "qms_modules lists for the module. Each record carries `id`."
        ),
        annotations=READ_ONLY,
    )
    def qms_list(module: str, filters: dict[str, Any] | None = None,
                 page: int = 1, per_page: int = 20) -> dict[str, Any]:
        with context.unit_of_work(app) as (session, actor):
            return operations.list_records(
                session, actor, operations.get_module(module), filters, page, per_page
            )

    @mcp.tool(
        name="qms_get",
        title="Get one record",
        description="Return one record of a module by its `id`.",
        annotations=READ_ONLY,
    )
    def qms_get(module: str, id: int) -> dict[str, Any]:  # noqa: A002 - the tool's public name
        with context.unit_of_work(app) as (session, actor):
            return operations.get_record(session, actor, operations.get_module(module), id)

    @mcp.tool(
        name="qms_create",
        title="Create a record",
        description=(
            "Create a record in a module. `data` holds the fields qms_modules lists (required "
            "ones must be present; dates as YYYY-MM-DD). Returns the stored record."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def qms_create(module: str, data: dict[str, Any]) -> dict[str, Any]:
        with context.unit_of_work(app, write=True) as (session, actor):
            return operations.create_record(session, actor, operations.get_module(module), data)

    @mcp.tool(
        name="qms_update",
        title="Update a record",
        description=(
            "Overwrite the given fields of a record (other fields are kept). Returns the stored "
            "record. There is no delete tool."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def qms_update(module: str, id: int, data: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        with context.unit_of_work(app, write=True) as (session, actor):
            return operations.update_record(
                session, actor, operations.get_module(module), id, data
            )

    return mcp
