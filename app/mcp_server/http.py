"""Streamable HTTP transport: our bearer authentication in front of the SDK app.

The SDK's own ``AuthSettings`` is not used: it publishes OAuth discovery
metadata that static-token clients would follow. A failed credential is a
plain 401 with ``WWW-Authenticate: Bearer`` and nothing else. Going over a rate
limit (see ``rate_limit``) is a 429 with ``Retry-After``.
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

from ..services.actor import Actor
from ..services.errors import AuthenticationFailed
from ..utils import security_logger
from . import context
from .rate_limit import McpRateLimiter
from .server import build_server

logger = logging.getLogger(__name__)

RATE_LIMITED = "Demasiadas solicitudes. Inténtelo de nuevo más tarde."


class _RateLimited(Exception):
    """Flow control inside the middleware: refuse with 429 after ``retry_after`` seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, credentials = value.decode("latin-1").partition(" ")
            if scheme.lower() != "bearer":
                return None
            return credentials.strip() or None
    return None


class BearerAuthMiddleware:
    """Pure-ASGI middleware: rate-limit, authenticate the bearer token, expose its actor."""

    def __init__(self, app: ASGIApp, flask_app: Flask, limiter: McpRateLimiter) -> None:
        self.app = app
        self.flask_app = flask_app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan; the SDK app serves no websockets
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        try:
            actor = await anyio.to_thread.run_sync(
                self._admit, _bearer_token(scope), client[0] if client else None
            )
        except _RateLimited as limited:
            response = JSONResponse(
                {"error": RATE_LIMITED}, status_code=429,
                headers={"Retry-After": str(limited.retry_after)},
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

    def _admit(self, token: str | None, client_ip: str | None) -> Actor:
        """Authenticate, then count against one limit; blocking, so it runs in a worker thread.

        A failed authentication counts against its address (one atomic hit) and
        is refused with 429 instead of 401 once the address is over the limit,
        so a valid token from that address is never refused by it. A valid token
        counts against its own limit. Every refusal is logged: each failed
        attempt already writes an ``API_TOKEN_AUTH_FAILED`` line, so throttling
        this one would not bound the log, only add shared state.
        """
        try:
            actor = context.authenticate(self.flask_app, token, client_ip)
        except AuthenticationFailed:
            wait = self.limiter.hit_auth_failure(client_ip)
            if wait is None:
                raise
            with context.client_request(self.flask_app, client_ip):
                security_logger.log_mcp_auth_rate_limited()
            raise _RateLimited(wait) from None
        if actor.token_prefix is not None:
            wait = self.limiter.hit_token(actor.token_prefix)
            if wait is not None:
                with context.client_request(self.flask_app, client_ip):
                    security_logger.log_api_token_rate_limited(actor.token_prefix)
                raise _RateLimited(wait)
        return actor


def create_http_app(flask_app: Flask, allowed_hosts: list[str]) -> Starlette:
    """The ASGI app: stateless JSON responses at ``/mcp``, Host-checked, bearer-authenticated.

    Raises ``RateLimitConfigError`` for an invalid limit or storage, before serving.
    """
    limiter = McpRateLimiter.from_app(flask_app)
    app = build_server(flask_app).streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )
    app.add_middleware(BearerAuthMiddleware, flask_app=flask_app, limiter=limiter)
    return app
