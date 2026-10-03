"""The MCP server: five generic tools over the service layer."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..services import policy
from ..services.policy import Action
from . import context
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
    return described


def build_server() -> MCPServer:
    mcp = MCPServer("iso9001-qms", instructions=INSTRUCTIONS)

    @mcp.tool(
        name="qms_modules",
        title="List QMS modules",
        description=(
            "List the QMS modules with their fields (type, required, allowed values), the "
            "filters qms_list supports and what the calling user may do in each."
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

    return mcp
