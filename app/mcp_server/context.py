"""Who is calling: the actor of the current tool call.

The HTTP transport sets the ``ContextVar`` per request (bearer middleware).
The stdio transport has no per-request credential: ``stdio_identity`` makes
every ``current_actor()`` call authenticate the configured token again, so a
revoked or expired token, or a changed role, takes effect on the next call. A
tool never reads credentials; it only asks for the actor.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
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

logger = logging.getLogger(__name__)

_current: ContextVar[Actor | None] = ContextVar("mcp_actor", default=None)
_stdio_actor: Callable[[], Actor] | None = None  # re-authenticates the stdio token per call


def set_actor(actor: Actor) -> Token:
    return _current.set(actor)


def reset_actor(token: Token) -> None:
    _current.reset(token)


@contextmanager
def stdio_identity(app: Flask, raw: str) -> Iterator[None]:
    """Serve stdio as the owner of ``raw``, checking the token on every call."""
    global _stdio_actor
    _stdio_actor = lambda: authenticate(app, raw)  # noqa: E731
    try:
        yield
    finally:
        _stdio_actor = None


def current_actor() -> Actor:
    """The authenticated actor, or a clean tool error when there is none."""
    actor = _current.get()
    if actor is not None:
        return actor
    if _stdio_actor is None:
        raise ToolError("Sesión no autenticada.")
    try:
        return _stdio_actor()
    except AuthenticationFailed as failure:
        raise ToolError(failure.message) from None
    except SQLAlchemyError as error:
        logger.error("Token authentication failed: %s", type(error).__name__)
        raise ToolError("Servicio no disponible.") from None


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
