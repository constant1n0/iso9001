"""Shared fixtures for the MCP adapter tests: an in-memory app and an in-memory SDK client."""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap
from mcp import Client

from app.extensions import db
from app.mcp_server import context
from app.mcp_server.server import build_server
from app.models import RoleEnum
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO


def mcp_actor(role=ADMIN, scopes=("read", "write"), user_id=7) -> Actor:
    return Actor(user_id=user_id, label=f"user{user_id}", role=role, channel="mcp",
                 scopes=None if scopes is None else frozenset(scopes))


class McpDbCase(unittest.IsolatedAsyncioTestCase):
    """In-memory app plus ``call``, which talks to a fresh in-memory MCP server."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    async def call(self, actor: Actor | None, name: str, arguments: dict | None = None):
        # The actor is set before the client starts so the server tasks inherit it.
        token = context.set_actor(actor) if actor is not None else None
        try:
            async with Client(build_server()) as client:
                return await client.call_tool(name, arguments or {})
        finally:
            if token is not None:
                context.reset_actor(token)

    async def list_tools(self):
        async with Client(build_server()) as client:
            return (await client.list_tools()).tools
