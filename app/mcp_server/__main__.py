"""``python -m app.mcp_server --transport stdio|http``: the MCP server's entry point.

Environment: ``MCP_HOST`` / ``MCP_PORT`` (default 127.0.0.1:8765),
``MCP_ALLOWED_HOSTS`` (comma-separated Host values the HTTP transport accepts),
``MCP_TRUSTED_PROXIES`` (comma-separated addresses whose ``X-Forwarded-For`` the
HTTP transport believes; default ``127.0.0.1``, never ``*``),
``ISO9001_MCP_TOKEN`` (the API token stdio acts as, re-checked on every call),
``MCP_TOKEN_RATE_LIMIT`` / ``MCP_AUTH_FAILURE_RATE_LIMIT`` (HTTP only; default
``120/minute`` per token and ``20/minute`` failed tokens per address), plus the
application's ``SECRET_KEY``, ``DATABASE_URI`` and ``RATELIMIT_STORAGE_URI``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence

import uvicorn
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from .. import create_app
from ..services.errors import AuthenticationFailed
from . import context
from .http import create_http_app
from .rate_limit import RateLimitConfigError
from .server import build_server


class StartupError(Exception):
    """The server cannot start; the message is safe to print."""


def _port(value: str) -> int:
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError(f"invalid port: {value!r}")
    return int(value)


def parse_args(argv: Sequence[str] | None, environ: Mapping[str, str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.mcp_server", description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default=environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=_port, default=environ.get("MCP_PORT", "8765"))
    args = parser.parse_args(argv)
    args.trusted_proxies = [
        p.strip() for p in environ.get("MCP_TRUSTED_PROXIES", "127.0.0.1").split(",") if p.strip()
    ]
    if "*" in args.trusted_proxies:
        parser.error("MCP_TRUSTED_PROXIES must list addresses, not '*'.")
    configured = [h.strip() for h in environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    args.allowed_hosts = configured or [
        f"{host}:{args.port}" for host in ("127.0.0.1", "localhost", "[::1]")
    ]
    return args


def authenticate_stdio(app: Flask, environ: Mapping[str, str]):
    """Start-up check of ``ISO9001_MCP_TOKEN``; later calls re-authenticate it themselves."""
    token = environ.get("ISO9001_MCP_TOKEN", "").strip()
    if not token:
        raise StartupError("ISO9001_MCP_TOKEN is not set: stdio needs an API token.")
    try:
        return context.authenticate(app, token)
    except AuthenticationFailed:
        raise StartupError("ISO9001_MCP_TOKEN was not accepted.") from None
    except SQLAlchemyError:
        raise StartupError("The database is not reachable.") from None


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    app_factory: Callable[[], Flask] = create_app,
) -> int:
    environ = os.environ if environ is None else environ
    settings = parse_args(argv, environ)
    app = app_factory()
    if settings.transport == "stdio":
        # No rate limits: stdio is local, so whoever starts it already has server access.
        try:
            actor = authenticate_stdio(app, environ)
        except StartupError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        del actor  # only proves the token is valid now; every tool call authenticates again
        with context.stdio_identity(app, environ["ISO9001_MCP_TOKEN"].strip()):
            build_server(app).run("stdio")
    else:
        try:
            asgi = create_http_app(app, settings.allowed_hosts)
        except RateLimitConfigError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        uvicorn.run(
            asgi,
            host=settings.host, port=settings.port, server_header=False,
            # Honour X-Forwarded-For only from the local proxy (Traefik), so the
            # security log records the real caller and nobody else can forge it.
            proxy_headers=True, forwarded_allow_ips=",".join(settings.trusted_proxies),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
