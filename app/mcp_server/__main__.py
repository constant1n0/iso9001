"""``python -m app.mcp_server --transport stdio|http``: the MCP server's entry point.

Environment: ``MCP_HOST`` / ``MCP_PORT`` (default 127.0.0.1:8765),
``MCP_ALLOWED_HOSTS`` (comma-separated Host values the HTTP transport accepts),
``ISO9001_MCP_TOKEN`` (the API token stdio acts as), plus the application's
``SECRET_KEY`` and ``DATABASE_URI``.
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
    configured = [h.strip() for h in environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    args.allowed_hosts = configured or [
        f"{host}:{args.port}" for host in ("127.0.0.1", "localhost", "[::1]")
    ]
    return args


def authenticate_stdio(app: Flask, environ: Mapping[str, str]):
    """The actor for stdio, from ``ISO9001_MCP_TOKEN``, authenticated once."""
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
        try:
            actor = authenticate_stdio(app, environ)
        except StartupError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        context.set_actor(actor)  # inherited by the server's tasks and worker threads
        build_server(app).run("stdio")
    else:
        uvicorn.run(
            create_http_app(app, settings.allowed_hosts),
            host=settings.host, port=settings.port, server_header=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
