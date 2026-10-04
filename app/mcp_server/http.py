"""Streamable HTTP transport: our bearer authentication in front of the SDK app.

The SDK's own ``AuthSettings`` is not used: it publishes OAuth discovery
metadata that static-token clients would follow. A failed credential is a
plain 401 with ``WWW-Authenticate: Bearer`` and nothing else.
"""

from __future__ import annotations

import logging

import anyio
from flask import Flask
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..services.errors import AuthenticationFailed
from . import context
from .server import build_server

logger = logging.getLogger(__name__)


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, credentials = value.decode("latin-1").partition(" ")
            return credentials.strip() or None if scheme.lower() == "bearer" else None
    return None


class BearerAuthMiddleware:
    """Pure-ASGI middleware: authenticate the bearer token, expose its actor to the tools."""

    def __init__(self, app: ASGIApp, flask_app: Flask) -> None:
        self.app = app
        self.flask_app = flask_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan; the SDK app serves no websockets
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        try:
            actor = await anyio.to_thread.run_sync(
                context.authenticate, self.flask_app, _bearer_token(scope), client[0] if client else None
            )
        except AuthenticationFailed as failure:
            response = JSONResponse(
                {"error": failure.message}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        except SQLAlchemyError as error:
            logger.error("Token authentication failed: %s", type(error).__name__)
            response = JSONResponse({"error": "Servicio no disponible."}, status_code=503)
        else:
            reset = context.set_actor(actor)
            try:
                await self.app(scope, receive, send)
            finally:
                context.reset_actor(reset)
            return
        await response(scope, receive, send)


def create_http_app(flask_app: Flask, allowed_hosts: list[str]) -> Starlette:
    """The ASGI app: stateless JSON responses at ``/mcp``, Host-checked, bearer-authenticated."""
    app = build_server(flask_app).streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )
    app.add_middleware(BearerAuthMiddleware, flask_app=flask_app)
    return app
