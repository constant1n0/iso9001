"""MCP HTTP rate limits: per token after authentication, per address for failed tokens."""

from __future__ import annotations

import asyncio
import io
import json
import time
import unittest
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager, redirect_stderr
from unittest.mock import patch

import httpx2

from mcp_support import McpDbCase
from test_mcp_http import ACCEPT, HOST, call_body

from app.mcp_server import __main__ as cli
from app.mcp_server import http, rate_limit

BAD_TOKEN = f"iso_{'0' * 8}_{'A' * 43}"
MESSAGE = "Demasiadas solicitudes. Inténtelo de nuevo más tarde."


class RateLimitCase(McpDbCase):
    """Small limits on in-memory storage; each ``serve`` builds a fresh limiter."""

    def setUp(self) -> None:
        super().setUp()
        self.app.config.update(
            MCP_TOKEN_RATE_LIMIT="2/minute", MCP_AUTH_FAILURE_RATE_LIMIT="2/minute"
        )

    @asynccontextmanager
    async def serve(self, *addresses: str):
        """One ASGI app, one client per source address (default ``127.0.0.1``)."""
        asgi = http.create_http_app(self.app, allowed_hosts=[HOST])
        async with asgi.router.lifespan_context(asgi), AsyncExitStack() as stack:
            clients = []
            for address in addresses or ("127.0.0.1",):
                transport = httpx2.ASGITransport(app=asgi, client=(address, 123))
                clients.append(await stack.enter_async_context(
                    httpx2.AsyncClient(transport=transport, base_url=f"http://{HOST}")))
            yield clients[0] if len(clients) == 1 else clients

    async def post(self, client, token=None):
        headers = ACCEPT | ({"Authorization": f"Bearer {token}"} if token else {})
        return await client.post("/mcp", content=json.dumps(call_body("qms_modules")),
                                 headers=headers)

    def assert_refused(self, response) -> None:
        self.assertEqual(429, response.status_code)
        self.assertEqual({"error": MESSAGE}, response.json())
        self.assertTrue(1 <= int(response.headers["retry-after"]) <= 60)


class TokenLimitTestCase(RateLimitCase):
    async def test_a_token_over_its_limit_gets_429_and_a_log_line(self) -> None:
        good, row, _ = self.issue_token()
        async with self.serve() as client:
            for _ in range(2):
                self.assertEqual(200, (await self.post(client, good)).status_code)
            with self.assertLogs("security", level="WARNING") as logged:
                response = await self.post(client, good)
        self.assert_refused(response)
        output = "\n".join(logged.output)
        self.assertIn(f"API_TOKEN_RATE_LIMITED | prefix={row.prefix} | ip=127.0.0.1", output)
        self.assertNotIn(good, output)
        self.assertNotIn(good.split("_", 2)[2], output)
        self.assertNotIn(good, response.text)

    async def test_each_token_has_its_own_budget(self) -> None:
        first, _, _ = self.issue_token()
        second, _, _ = self.issue_token()
        async with self.serve() as client:
            for _ in range(2):
                await self.post(client, first)
            with self.assertLogs("security", level="WARNING"):
                self.assert_refused(await self.post(client, first))
            for _ in range(2):
                self.assertEqual(200, (await self.post(client, second)).status_code)

    async def test_successful_requests_do_not_spend_the_failure_budget(self) -> None:
        self.app.config["MCP_TOKEN_RATE_LIMIT"] = "10/minute"
        good, _, _ = self.issue_token()
        async with self.serve() as client:
            for _ in range(4):
                self.assertEqual(200, (await self.post(client, good)).status_code)

    def test_counters_use_the_application_key_prefix(self) -> None:
        limiter = rate_limit.McpRateLimiter.from_app(self.app)
        self.assertIsNone(limiter.hit_token("abcd1234"))
        stats = limiter.strategy.get_window_stats(limiter.token_limit, "iso9001:mcp:token:abcd1234")
        self.assertEqual(1, stats.remaining)
        limiter.hit_auth_failure("10.0.0.7")
        stats = limiter.strategy.get_window_stats(
            limiter.failure_limit, "iso9001:mcp:auth-fail:10.0.0.7")
        self.assertEqual(1, stats.remaining)


class FailureLimitTestCase(RateLimitCase):
    async def test_failures_over_the_limit_get_429_and_a_log_line(self) -> None:
        async with self.serve() as client:
            for _ in range(2):
                with self.assertLogs("security", level="WARNING"):
                    self.assertEqual(401, (await self.post(client, BAD_TOKEN)).status_code)
            with self.assertLogs("security", level="WARNING") as logged:
                refused = await self.post(client, BAD_TOKEN)
        self.assert_refused(refused)
        output = "\n".join(logged.output)
        self.assertIn("MCP_AUTH_RATE_LIMITED | ip=127.0.0.1", output)
        self.assertNotIn(BAD_TOKEN, output)

    async def test_a_valid_token_from_the_same_address_is_not_refused(self) -> None:
        good, _, _ = self.issue_token()
        async with self.serve() as client:
            with self.assertLogs("security", level="WARNING"):
                for _ in range(2):
                    await self.post(client, BAD_TOKEN)
                self.assert_refused(await self.post(client, BAD_TOKEN))
            self.assertEqual(200, (await self.post(client, good)).status_code)
            with self.assertLogs("security", level="WARNING"):
                self.assert_refused(await self.post(client, BAD_TOKEN))

    async def test_parallel_failures_never_pass_the_limit(self) -> None:
        """A slow lookup widens the race a check-then-count gate would lose."""
        real = http.context.authenticate

        def slow_authenticate(*args, **kwargs):
            time.sleep(0.05)
            return real(*args, **kwargs)

        async with self.serve() as client:
            with (patch.object(http.context, "authenticate", slow_authenticate),
                  self.assertLogs("security", level="WARNING")):
                responses = await asyncio.gather(*(self.post(client) for _ in range(8)))
        self.assertEqual({401: 2, 429: 6}, Counter(r.status_code for r in responses))

    async def test_missing_tokens_count_as_failures(self) -> None:
        async with self.serve() as client:
            with self.assertLogs("security", level="WARNING"):
                for _ in range(2):
                    self.assertEqual(401, (await self.post(client)).status_code)
                self.assert_refused(await self.post(client))

    async def test_another_address_is_unaffected(self) -> None:
        good, _, _ = self.issue_token()
        async with self.serve("10.0.0.1", "10.0.0.2") as (attacker, neighbour):
            with self.assertLogs("security", level="WARNING"):
                for _ in range(2):
                    await self.post(attacker, BAD_TOKEN)
                self.assert_refused(await self.post(attacker, BAD_TOKEN))
            self.assertEqual(200, (await self.post(neighbour, good)).status_code)
            with self.assertLogs("security", level="WARNING"):
                self.assertEqual(401, (await self.post(neighbour, BAD_TOKEN)).status_code)


class StorageOutageTestCase(RateLimitCase):
    async def test_an_unreachable_storage_fails_open_with_one_warning(self) -> None:
        self.app.config.update(
            MCP_TOKEN_RATE_LIMIT="1/minute", MCP_AUTH_FAILURE_RATE_LIMIT="1/minute",
            RATELIMIT_STORAGE_URI="redis://:s3cr3t-pw@127.0.0.1:1/0",
            RATELIMIT_STORAGE_OPTIONS={"socket_connect_timeout": 1, "socket_timeout": 1},
        )
        good, _, _ = self.issue_token()
        async with self.serve() as client:
            with self.assertLogs("app.mcp_server.rate_limit", level="WARNING") as logged:
                for _ in range(3):
                    self.assertEqual(200, (await self.post(client, good)).status_code)
                with self.assertLogs("security", level="WARNING") as security:
                    for _ in range(3):
                        self.assertEqual(401, (await self.post(client, BAD_TOKEN)).status_code)
        self.assertEqual(1, len(logged.records))
        self.assertNotIn("s3cr3t-pw", "\n".join(logged.output))
        self.assertNotIn("RATE_LIMITED", "\n".join(security.output))

    def test_the_limiter_recovers_when_the_storage_answers_again(self) -> None:
        limiter = rate_limit.McpRateLimiter.from_app(self.app)
        with (patch.object(limiter.strategy, "hit", side_effect=rate_limit.StorageError(OSError())),
              self.assertLogs("app.mcp_server.rate_limit", level="WARNING")):
            self.assertIsNone(limiter.hit_token("abcd1234"))
        with self.assertLogs("app.mcp_server.rate_limit", level="INFO") as logged:
            self.assertIsNone(limiter.hit_token("abcd1234"))
        self.assertEqual(["INFO"], [record.levelname for record in logged.records])

    def test_redis_storage_gets_short_timeouts_unless_configured(self) -> None:
        cases = (({}, (1, 1)), ({"socket_timeout": 5}, (1, 5)))
        for scheme in ("redis", "rediss"):
            for options, expected in cases:
                with self.subTest(scheme=scheme, options=options):
                    before = dict(options)
                    self.app.config.update(RATELIMIT_STORAGE_URI=f"{scheme}://127.0.0.1:1/0",
                                           RATELIMIT_STORAGE_OPTIONS=options)
                    limiter = rate_limit.McpRateLimiter.from_app(self.app)
                    kwargs = limiter.strategy.storage.storage.connection_pool.connection_kwargs
                    self.assertEqual(expected, (kwargs.get("socket_connect_timeout"),
                                                kwargs.get("socket_timeout")))
                    self.assertEqual(before, options)  # the configuration is not mutated


class StartupTestCase(McpDbCase):
    INVALID = ("lots", "", "2/fortnight", "0/minute", "2/minute;3/hour")

    def test_invalid_limits_fail_when_the_app_is_built(self) -> None:
        for setting in ("MCP_TOKEN_RATE_LIMIT", "MCP_AUTH_FAILURE_RATE_LIMIT"):
            for value in self.INVALID:
                with self.subTest(setting=setting, value=value):
                    self.app.config.update(
                        MCP_TOKEN_RATE_LIMIT="120/minute", MCP_AUTH_FAILURE_RATE_LIMIT="20/minute")
                    self.app.config[setting] = value
                    with self.assertRaises(rate_limit.RateLimitConfigError) as raised:
                        http.create_http_app(self.app, allowed_hosts=[HOST])
                    self.assertIn(setting, str(raised.exception))

    def test_the_http_command_refuses_to_start_with_an_invalid_limit(self) -> None:
        self.app.config["MCP_TOKEN_RATE_LIMIT"] = "lots"
        stderr = io.StringIO()
        with patch.object(cli.uvicorn, "run") as run, redirect_stderr(stderr):
            code = cli.main(["--transport", "http"], {}, app_factory=lambda: self.app)
        self.assertEqual(2, code)
        run.assert_not_called()
        self.assertIn("MCP_TOKEN_RATE_LIMIT", stderr.getvalue())

    def test_the_http_command_refuses_an_unusable_storage_without_its_secret(self) -> None:
        unusable = (
            "bogus://user:s3cr3t-pw@cache:1/0",           # unknown scheme
            "redis://user:s3cr3t-pw@cache:notaport/0",    # malformed
            "memcached://user:s3cr3t-pw@cache:11211",     # driver not installed
        )
        for uri in unusable:
            with self.subTest(uri=uri.split(":", 1)[0]):
                self.app.config["RATELIMIT_STORAGE_URI"] = uri
                stderr = io.StringIO()
                with patch.object(cli.uvicorn, "run") as run, redirect_stderr(stderr):
                    code = cli.main(["--transport", "http"], {}, app_factory=lambda: self.app)
                self.assertEqual(2, code)
                run.assert_not_called()
                self.assertIn("RATELIMIT_STORAGE_URI", stderr.getvalue())
                self.assertIn("cache", stderr.getvalue())
                self.assertNotIn("s3cr3t-pw", stderr.getvalue())
                self.assertNotIn("user", stderr.getvalue())

    def test_the_defaults_come_from_the_configuration(self) -> None:
        self.assertEqual(("120/minute", "20/minute"), (
            self.app.config["MCP_TOKEN_RATE_LIMIT"], self.app.config["MCP_AUTH_FAILURE_RATE_LIMIT"]))
        limiter = rate_limit.McpRateLimiter.from_app(self.app)
        self.assertEqual((120, 20), (limiter.token_limit.amount, limiter.failure_limit.amount))


if __name__ == "__main__":
    unittest.main()
