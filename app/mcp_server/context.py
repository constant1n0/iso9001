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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..extensions import db
from ..services import api_tokens
from ..services.actor import Actor
from ..services.errors import AuthenticationFailed, DomainError
from ..utils import security_logger

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


def authenticate(app: Flask, raw: str | None, client_ip: str | None = None) -> Actor:
    """Turn a bearer token into its actor, committing the throttled ``last_used_at``.

    Raises ``AuthenticationFailed`` (after writing the security log, never the
    token) or ``SQLAlchemyError`` when the database fails. Blocking: run it in
    a worker thread from async code.
    """
    with app.app_context():
        try:
            actor = api_tokens.authenticate(db.session, raw, secret_key=app.config["SECRET_KEY"])
            db.session.commit()
            return actor
        except AuthenticationFailed as failure:
            db.session.rollback()
            environ = {"REMOTE_ADDR": client_ip} if client_ip else {}
            with app.test_request_context(environ_base=environ):
                security_logger.log_api_token_auth_failed(failure.reason, failure.token_prefix)
            raise
        except SQLAlchemyError:
            db.session.rollback()
            raise
