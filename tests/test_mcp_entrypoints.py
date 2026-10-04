"""Command line: settings, stdio token bootstrap and transport selection."""

from __future__ import annotations

import io
import unittest
from contextlib import asynccontextmanager, redirect_stderr
from datetime import datetime, timedelta
from unittest.mock import patch

import anyio
import uvicorn
from mcp import ClientSession
from mcp.server import MCPServer
from mcp.server.mcpserver import server as sdk_server

from mcp_support import ADMIN, OPERATIVO, McpDbCase

from app.mcp_server import __main__ as cli
from app.mcp_server import context
from app.mcp_server.server import build_server
from app.models import RoleEnum

BAD_TOKEN = f"iso_{'0' * 8}_{'A' * 43}"


class SettingsTestCase(unittest.TestCase):
    def test_defaults_bind_to_localhost_and_allow_only_local_hosts(self) -> None:
        settings = cli.parse_args([], {})
        self.assertEqual(("stdio", "127.0.0.1", 8765), (settings.transport, settings.host, settings.port))
        self.assertEqual(["127.0.0.1:8765", "localhost:8765", "[::1]:8765"], settings.allowed_hosts)

    def test_environment_and_flags_override_the_defaults(self) -> None:
        env = {"MCP_HOST": "0.0.0.0", "MCP_PORT": "9000",
               "MCP_ALLOWED_HOSTS": " qms.example.com , qms.example.com:443 ,"}
        settings = cli.parse_args(["--transport", "http"], env)
        self.assertEqual(("http", "0.0.0.0", 9000), (settings.transport, settings.host, settings.port))
        self.assertEqual(["qms.example.com", "qms.example.com:443"], settings.allowed_hosts)
        flagged = cli.parse_args(["--transport", "http", "--host", "127.0.0.1", "--port", "8799"], env)
        self.assertEqual(("127.0.0.1", 8799), (flagged.host, flagged.port))

    def test_a_bad_port_is_refused(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.parse_args([], {"MCP_PORT": "http"})


class MainTestCase(McpDbCase):
    def run_main(self, argv, env):
        """Run ``main`` against the test app; returns (exit code, stderr text, run calls)."""
        calls, seen = [], []

        def fake_run(server, transport, **kwargs):
            calls.append((transport, kwargs))
            seen.append(context.current_actor())

        err = io.StringIO()
        with (patch.object(MCPServer, "run", autospec=True, side_effect=fake_run),
              redirect_stderr(err)):
            code = cli.main(argv, env, app_factory=lambda: self.app)
        return code, err.getvalue(), calls, seen

    def test_stdio_serves_as_the_token_owner_and_clears_it_afterwards(self) -> None:
        token, row, user = self.issue_token(OPERATIVO, ("read",))
        code, err, calls, seen = self.run_main(["--transport", "stdio"], {"ISO9001_MCP_TOKEN": token})
        self.assertEqual((0, [("stdio", {})]), (code, calls))
        actor = seen[0]  # what a tool call resolves to while the server runs
        self.assertEqual(("mcp", user.id, OPERATIVO, frozenset({"read"})),
                         (actor.channel, actor.user_id, actor.role, actor.scopes))
        self.assertNotIn(token, err)
        with self.assertRaises(Exception):  # the identity does not outlive the run
            context.current_actor()

    def test_stdio_refuses_to_start_without_a_valid_token(self) -> None:
        good, _, _ = self.issue_token(ADMIN)
        revoked, row, _ = self.issue_token(ADMIN)
        from app.extensions import db
        from app.services import api_tokens

        api_tokens.revoke(db.session, self.cli(), row.id)
        db.session.commit()
        for label, env in {"unset": {}, "blank": {"ISO9001_MCP_TOKEN": " "},
                           "unknown": {"ISO9001_MCP_TOKEN": BAD_TOKEN},
                           "revoked": {"ISO9001_MCP_TOKEN": revoked}}.items():
            with self.subTest(case=label):
                code, err, calls, _ = self.run_main(["--transport", "stdio"], env)
                self.assertEqual((2, []), (code, calls))
                self.assertIn("ISO9001_MCP_TOKEN", err)
                for secret in (good, revoked, BAD_TOKEN):
                    self.assertNotIn(secret, err)

    def test_http_serves_without_a_process_wide_token(self) -> None:
        with patch.object(cli.uvicorn, "run") as run:
            code = cli.main(["--transport", "http", "--host", "127.0.0.1", "--port", "8799"],
                            {"MCP_ALLOWED_HOSTS": "qms.example.com"}, app_factory=lambda: self.app)
        self.assertEqual(0, code)
        (args, kwargs), = [run.call_args]
        self.assertEqual(("127.0.0.1", 8799), (kwargs["host"], kwargs["port"]))
        self.assertIsNone(context._current.get())

    def test_http_trusts_proxy_headers_only_from_the_configured_proxies(self) -> None:
        for env, expected in ({}, "127.0.0.1"), ({"MCP_TRUSTED_PROXIES": "10.0.0.5, 10.0.0.6"}, "10.0.0.5,10.0.0.6"):
            with self.subTest(env=env), patch.object(cli.uvicorn, "run") as run:
                cli.main(["--transport", "http"], env, app_factory=lambda: self.app)
            kwargs = run.call_args.kwargs
            self.assertTrue(kwargs["proxy_headers"])
            self.assertEqual(expected, kwargs["forwarded_allow_ips"])

    def test_a_wildcard_proxy_list_is_refused(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.parse_args(["--transport", "http"], {"MCP_TRUSTED_PROXIES": "*"})


class ProxyTrustTestCase(unittest.IsolatedAsyncioTestCase):
    """The options we hand to uvicorn decide whose ``X-Forwarded-For`` is believed."""

    async def client_seen(self, peer: str, trusted: str) -> str:
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["client"][0])

        config = uvicorn.Config(inner, proxy_headers=True, forwarded_allow_ips=trusted)
        config.load()
        scope = {"type": "http", "client": (peer, 5000), "headers": [(b"x-forwarded-for", b"203.0.113.9")]}
        await config.loaded_app(scope, None, None)
        return seen[0]

    async def test_a_trusted_proxy_reveals_the_real_caller(self) -> None:
        self.assertEqual("203.0.113.9", await self.client_seen("127.0.0.1", "127.0.0.1"))

    async def test_an_untrusted_peer_cannot_forge_its_address(self) -> None:
        self.assertEqual("198.51.100.7", await self.client_seen("198.51.100.7", "127.0.0.1"))


class StdioEndToEndTestCase(McpDbCase):
    """The real stdio wiring (``run_stdio_async``) over in-memory pipes, no process-wide actor."""

    @asynccontextmanager
    async def stdio_session(self, token: str):
        to_server, server_in = anyio.create_memory_object_stream(32)
        server_out, from_server = anyio.create_memory_object_stream(32)

        @asynccontextmanager
        async def fake_stdio():  # same shape as ``mcp.server.stdio.stdio_server``
            yield server_in, server_out

        server = build_server(self.app)
        with patch.object(sdk_server, "stdio_server", fake_stdio), context.stdio_identity(self.app, token):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run_stdio_async)
                async with ClientSession(from_server, to_server) as session:
                    await session.initialize()
                    yield session
                tasks.cancel_scope.cancel()

    async def whoami(self, session) -> tuple[str, str]:
        result = await session.call_tool("qms_modules", {})
        text = result.content[0].text
        return ("error" if result.is_error else "ok"), text

    async def role_of(self, session) -> str:
        result = await session.call_tool("qms_modules", {})
        self.assertFalse(result.is_error, result.content)
        return result.structured_content["role"]

    async def test_a_tool_call_sees_the_token_owner(self) -> None:
        token, _, user = self.issue_token(OPERATIVO, ("read",))
        async with self.stdio_session(token) as session:
            result = await session.call_tool("qms_modules", {})
        self.assertEqual(("Operativo", ["read"]),
                         (result.structured_content["role"], result.structured_content["scopes"]))

    async def test_revoking_the_token_stops_the_running_server(self) -> None:
        from app.extensions import db
        from app.services import api_tokens

        token, row, _ = self.issue_token(ADMIN)
        async with self.stdio_session(token) as session:
            self.assertEqual("Administrador", await self.role_of(session))
            api_tokens.revoke(db.session, self.cli(), row.id)
            db.session.commit()
            state, text = await self.whoami(session)
        self.assertEqual("error", state)
        self.assertIn("Credenciales no válidas.", text)
        self.assertNotIn(token, text)

    async def test_an_expired_token_stops_the_running_server(self) -> None:
        from app.extensions import db

        token, row, _ = self.issue_token(ADMIN)
        async with self.stdio_session(token) as session:
            self.assertEqual("Administrador", await self.role_of(session))
            row.expires_at = datetime.utcnow() - timedelta(days=1)
            db.session.commit()
            state, text = await self.whoami(session)
        self.assertEqual("error", state)
        self.assertIn("Credenciales no válidas.", text)

    async def test_a_role_change_applies_to_the_next_call(self) -> None:
        from app.extensions import db

        token, _, user = self.issue_token(ADMIN)
        async with self.stdio_session(token) as session:
            self.assertEqual("Administrador", await self.role_of(session))
            user.role = RoleEnum.AUDITOR
            db.session.commit()
            self.assertEqual("Auditor", await self.role_of(session))

    async def test_a_database_failure_is_a_clean_tool_error(self) -> None:
        from sqlalchemy.exc import OperationalError

        token, _, _ = self.issue_token(ADMIN)
        async with self.stdio_session(token) as session:
            with patch.object(context.api_tokens, "authenticate",
                              side_effect=OperationalError("select", {}, Exception("db down"))):
                state, text = await self.whoami(session)
        self.assertEqual("error", state)
        self.assertIn("Servicio no disponible.", text)
        self.assertNotIn("db down", text)


if __name__ == "__main__":
    unittest.main()
