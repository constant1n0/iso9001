"""Who is calling: the actor of the current tool call.

The HTTP transport sets the ``ContextVar`` per request (bearer middleware);
the stdio transport authenticates once at start and installs a process-wide
actor. A tool never reads credentials; it only asks for the actor.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from mcp.server.mcpserver.exceptions import ToolError

from ..services.actor import Actor

_current: ContextVar[Actor | None] = ContextVar("mcp_actor", default=None)


def set_actor(actor: Actor) -> Token:
    return _current.set(actor)


def reset_actor(token: Token) -> None:
    _current.reset(token)


def current_actor() -> Actor:
    """The authenticated actor, or a clean tool error when there is none."""
    actor = _current.get()
    if actor is None:
        raise ToolError("Sesión no autenticada.")
    return actor
