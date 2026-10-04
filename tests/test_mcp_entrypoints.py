"""Command line: settings, stdio token bootstrap and transport selection."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from mcp.server import MCPServer

from mcp_support import ADMIN, OPERATIVO, McpDbCase

from app.mcp_server import __main__ as cli
from app.mcp_server import context

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
            seen.append(context._current.get())

        err = io.StringIO()
        with (patch.object(MCPServer, "run", autospec=True, side_effect=fake_run),
              redirect_stderr(err)):
            code = cli.main(argv, env, app_factory=lambda: self.app)
        return code, err.getvalue(), calls, seen

    def test_stdio_authenticates_the_token_once_and_serves_as_its_owner(self) -> None:
        token, row, user = self.issue_token(OPERATIVO, ("read",))
        code, err, calls, seen = self.run_main(["--transport", "stdio"], {"ISO9001_MCP_TOKEN": token})
        self.assertEqual((0, [("stdio", {})]), (code, calls))
        actor = seen[0]
        self.assertEqual(("mcp", user.id, OPERATIVO, frozenset({"read"})),
                         (actor.channel, actor.user_id, actor.role, actor.scopes))
        self.assertNotIn(token, err)

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


if __name__ == "__main__":
    unittest.main()
