"""Who is calling: the actor of the current tool call.

The HTTP transport sets the ``ContextVar`` per request (bearer middleware);
the stdio transport authenticates once at start and installs a process-wide
actor. A tool never reads credentials; it only asks for the actor.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from flask import Flask
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session

from ..extensions import db
from ..services.actor import Actor
from ..services.errors import DomainError

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


@contextmanager
def unit_of_work(app: Flask, *, write: bool = False) -> Iterator[tuple[Session, Actor]]:
    """One tool call: app context, its own session, commit only after a write.

    Any failure rolls the session back. A domain error becomes a ``ToolError``
    carrying its safe message; anything else propagates so the SDK answers with
    a generic message and logs the traceback.
    """
    actor = current_actor()
    with app.app_context():
        try:
            yield db.session, actor
            if write:
                db.session.commit()
        except DomainError as error:
            db.session.rollback()
            raise ToolError(error.message) from error
        except BaseException:
            db.session.rollback()
            raise
