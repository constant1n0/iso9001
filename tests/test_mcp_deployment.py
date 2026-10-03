"""Deployment artefacts: the systemd unit and the client guide."""

from __future__ import annotations

import configparser
import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "mcp.md"
EXEC_START = (
    "/home/dcm/work/iso9001/venv/bin/python -m app.mcp_server "
    "--transport http --host 127.0.0.1 --port 8765"
)


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / name, encoding="utf-8")
    return parser


def fenced(language: str) -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)```", text, flags=re.DOTALL)


class SystemdUnitTestCase(unittest.TestCase):
    def test_unit_runs_the_http_server_on_localhost_like_the_web_unit(self) -> None:
        mcp, web = unit("iso9001-mcp.service")["Service"], unit("iso9001.service")["Service"]
        self.assertEqual(EXEC_START, mcp["ExecStart"])
        self.assertEqual("simple", mcp["Type"])
        for key in ("User", "Group", "WorkingDirectory", "EnvironmentFile", "PrivateTmp",
                    "NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths"):
            self.assertEqual(web[key], mcp[key], key)
        self.assertEqual("on-failure", mcp["Restart"])
        self.assertIn("network.target", unit("iso9001-mcp.service")["Unit"]["After"])
        self.assertEqual("multi-user.target", unit("iso9001-mcp.service")["Install"]["WantedBy"])


class GuideTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.text = GUIDE.read_text(encoding="utf-8")

    def test_guide_covers_setup_and_every_client(self) -> None:
        for expected in (
            "flask --app run.py create-api-token", "MCP_ALLOWED_HOSTS", "MCP_HOST", "MCP_PORT",
            "ISO9001_MCP_TOKEN", "Traefik", "127.0.0.1:8765", "iso9001-mcp.service",
            "claude mcp add --transport http", "Authorization: Bearer ${ISO9001_TOKEN}",
            "~/.codex/config.toml", "bearer_token_env_var", "~/.pi/agent/mcp.json",
            "opencode.json", '"type": "remote"', "OpenClaw", '"transport": "streamable-http"',
            "claude_desktop_config.json", "mcp-remote", "--header-file",
            "Smoke test", "Unverified",
        ):
            self.assertIn(expected, self.text)

    def test_every_client_has_a_smoke_test_item(self) -> None:
        checklist = self.text.split("Smoke test", 1)[1]
        for client in ("Claude Code", "Codex", "Pi", "OpenCode", "OpenClaw", "Claude Desktop"):
            self.assertIn(client, checklist)

    def test_configuration_snippets_parse(self) -> None:
        json_blocks, toml_blocks = fenced("json"), fenced("toml")
        self.assertGreaterEqual(len(json_blocks), 4)
        self.assertGreaterEqual(len(toml_blocks), 1)
        for block in json_blocks:
            json.loads(block)
        for block in toml_blocks:
            tomllib.loads(block)

    def test_guide_never_contains_a_real_token(self) -> None:
        self.assertIsNone(re.search(r"iso_[0-9a-f]{8}_[A-Za-z0-9_-]{43}", self.text))

    def test_readme_points_to_the_guide(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/mcp.md", readme)


if __name__ == "__main__":
    unittest.main()
