"""Streamable HTTP: bearer middleware, host checks and protocol handshakes, over ASGI."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import anyio
import httpx2
from sqlalchemy.exc import OperationalError

from mcp_support import ADMIN, OPERATIVO, SEEDS, McpDbCase

from app.extensions import db
from app.mcp_server import http
from app.models import ApiToken, AuditLog
from app.services import api_tokens

HOST = "127.0.0.1:8000"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CLIENT_INFO = {"name": "test", "version": "1"}
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
}


def call_body(name: str, arguments: dict | None = None, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


class HttpCase(McpDbCase):
    @asynccontextmanager
    async def serve(self):
        asgi = http.create_http_app(self.app, allowed_hosts=[HOST])
        async with asgi.router.lifespan_context(asgi):
            transport = httpx2.ASGITransport(app=asgi)
            async with httpx2.AsyncClient(transport=transport, base_url=f"http://{HOST}") as client:
                yield client

    async def post(self, client, body, token=None, **headers):
        sent = ACCEPT | ({"Authorization": f"Bearer {token}"} if token else {}) | headers
        return await client.post("/mcp", content=json.dumps(body), headers=sent)


class AuthenticationTestCase(HttpCase):
    async def test_missing_or_bad_credentials_get_a_plain_401(self) -> None:
        good, row, _ = self.issue_token()
        revoked, revoked_row, _ = self.issue_token()
        api_tokens.revoke(db.session, self.cli(), revoked_row.id)
        db.session.commit()
        unknown = f"iso_{'0' * 8}_{'A' * 43}"
        cases = {
            "no header": {}, "basic scheme": {"Authorization": "Basic abc"},
            "empty bearer": {"Authorization": "Bearer "}, "garbage": {"Authorization": "Bearer nope"},
            "unknown prefix": {"Authorization": f"Bearer {unknown}"},
            "tampered secret": {"Authorization": f"Bearer {good[:-1]}{'A' if good[-1] != 'A' else 'B'}"},
            "revoked": {"Authorization": f"Bearer {revoked}"},
        }
        async with self.serve() as client:
            for label, headers in cases.items():
                with self.subTest(case=label), self.assertLogs("security", level="WARNING"):
                    response = await client.post("/mcp", content=json.dumps(call_body("qms_modules")),
                                                 headers=ACCEPT | headers)
                    self.assertEqual(401, response.status_code)
                    self.assertEqual("Bearer", response.headers["www-authenticate"])
                    self.assertNotIn("resource_metadata", response.headers["www-authenticate"])
                    self.assertNotIn(good, response.text)

    async def test_failures_are_logged_without_the_token(self) -> None:
        good, row, _ = self.issue_token()
        tampered = f"{good[:-1]}{'A' if good[-1] != 'A' else 'B'}"
        async with self.serve() as client:
            with self.assertLogs("security", level="WARNING") as logged:
                await self.post(client, call_body("qms_modules"), token=tampered)
        output = "\n".join(logged.output)
        self.assertIn("API_TOKEN_AUTH_FAILED | reason=bad_secret", output)
        self.assertIn(f"prefix={row.prefix}", output)
        self.assertIn("ip=127.0.0.1", output)
        self.assertNotIn(tampered, output)
        self.assertNotIn(tampered.split("_", 2)[2], output)

    async def test_a_database_failure_is_a_503_without_details(self) -> None:
        good, _, _ = self.issue_token()
        failure = OperationalError("SELECT secret FROM x", {}, Exception("password=hunter2"))
        async with self.serve() as client:
            with (patch.object(api_tokens, "authenticate", side_effect=failure),
                  self.assertLogs("app.mcp_server.http", level="ERROR")):
                response = await self.post(client, call_body("qms_modules"), token=good)
        self.assertEqual(503, response.status_code)
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("SELECT", response.text)

    async def test_other_paths_need_a_token_too(self) -> None:
        async with self.serve() as client:
            self.assertEqual(401, (await client.get("/anything")).status_code)

    async def test_a_valid_request_persists_last_used_at(self) -> None:
        good, row, _ = self.issue_token()
        async with self.serve() as client:
            await self.post(client, call_body("qms_modules"), token=good)
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(ApiToken, row.id).last_used_at)


class ActorTestCase(HttpCase):
    async def test_the_token_owner_is_the_actor_of_every_tool_call(self) -> None:
        admin, _, admin_user = self.issue_token(ADMIN, ("read", "write"))
        reader, _, _ = self.issue_token(OPERATIVO, ("read",))
        async with self.serve() as client:
            who = {}
            for label, token in (("admin", admin), ("reader", reader), ("admin2", admin)):
                response = await self.post(client, call_body("qms_modules"), token=token)
                who[label] = response.json()["result"]["structuredContent"]
            self.assertEqual(("Administrador", ["read", "write"]), (who["admin"]["role"], who["admin"]["scopes"]))
            self.assertEqual(("Operativo", ["read"]), (who["reader"]["role"], who["reader"]["scopes"]))
            self.assertEqual("Administrador", who["admin2"]["role"])
            created = await self.post(client, call_body("qms_create", {
                "module": "no_conformidades", "data": SEEDS["no_conformidades"]}), token=admin)
            self.assertFalse(created.json()["result"]["isError"])
            denied = await self.post(client, call_body("qms_create", {
                "module": "no_conformidades", "data": SEEDS["no_conformidades"]}), token=reader)
            self.assertTrue(denied.json()["result"]["isError"])
        db.session.expire_all()
        (entry,) = db.session.query(AuditLog).filter_by(channel="mcp").all()  # token issuing is cli
        self.assertEqual(("no_conformidades", admin_user.id), (entry.entity_type, entry.actor_user_id))

class ConcurrentActorTestCase(HttpCase):
    """Concurrent requests run against a file database with one connection per thread.

    The in-memory SQLite of the other cases shares a single connection between
    threads, so concurrent sessions interleave on it and fail at random
    (``StaleDataError`` on ``last_used_at``); production uses PostgreSQL.
    """

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        uri = f"sqlite:///{directory.name}/mcp.db"
        self.app_config = {"SQLALCHEMY_DATABASE_URI": uri, "DATABASE_URI": uri}
        super().setUp()

    async def test_concurrent_requests_never_leak_actors(self) -> None:
        tokens = {"Administrador": self.issue_token(ADMIN)[0], "Operativo": self.issue_token(OPERATIVO)[0]}
        seen: list[tuple[str, str]] = []

        async with self.serve() as client:
            async def ask(role: str, n: int) -> None:
                response = await self.post(client, call_body("qms_modules", request_id=n), token=tokens[role])
                seen.append((role, response.json()["result"]["structuredContent"]["role"]))

            async with anyio.create_task_group() as group:
                for n in range(12):
                    group.start_soon(ask, ("Administrador", "Operativo")[n % 2], n)
        self.assertEqual(12, len(seen))
        self.assertTrue(all(expected == actual for expected, actual in seen), seen)


class TransportTestCase(HttpCase):
    async def test_a_disallowed_host_is_rejected_even_with_a_valid_token(self) -> None:
        good, _, _ = self.issue_token()
        async with self.serve() as client:
            response = await self.post(client, call_body("qms_modules"), token=good, Host="evil.example")
        self.assertEqual(421, response.status_code)

    async def test_legacy_initialize_is_answered_for_both_2025_versions(self) -> None:
        good, _, _ = self.issue_token()
        async with self.serve() as client:
            for version in ("2025-06-18", "2025-11-25"):
                with self.subTest(version=version):
                    response = await self.post(client, {
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": version, "capabilities": {},
                                   "clientInfo": CLIENT_INFO}}, token=good)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(version, response.json()["result"]["protocolVersion"])
                    listed = await self.post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                             token=good, **{"Mcp-Protocol-Version": version})
                    names = {t["name"] for t in listed.json()["result"]["tools"]}
                    self.assertEqual({"qms_modules", "qms_list", "qms_get", "qms_create", "qms_update"}, names)

    async def test_stateless_2026_07_28_requests_are_answered(self) -> None:
        good, _, _ = self.issue_token()
        modern = {"Mcp-Protocol-Version": "2026-07-28"}
        async with self.serve() as client:
            listed = await self.post(client, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": MODERN_META}},
                token=good, **modern, **{"Mcp-Method": "tools/list"})
            self.assertEqual(200, listed.status_code)
            self.assertEqual(5, len(listed.json()["result"]["tools"]))
            body = call_body("qms_modules")
            body["params"]["_meta"] = MODERN_META
            called = await self.post(client, body, token=good, **modern,
                                     **{"Mcp-Method": "tools/call", "Mcp-Name": "qms_modules"})
            self.assertEqual(200, called.status_code)
            self.assertEqual(12, len(called.json()["result"]["structuredContent"]["modules"]))


if __name__ == "__main__":
    unittest.main()
