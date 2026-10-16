"""A web session ends with the browser, after 8 idle hours or 12 hours after sign-in (DD4).

The session cookie is a browser-session cookie: no ``Expires`` or ``Max-Age``.
Sign-in stamps the signed session with the sign-in time and the last-seen time
(UTC epoch seconds), and a ``before_request`` hook signs out a session idle for
more than ``SESSION_IDLE_TIMEOUT`` or signed in more than
``SESSION_ABSOLUTE_LIFETIME`` ago, with the expiry message. Flask also refuses
any session cookie signed more than ``PERMANENT_SESSION_LIFETIME`` ago; that
limit matches the absolute one, so the idle limit is the hook's to enforce.

Time is moved on the server only, without sleeping: the hook reads
``app.session_lifetime._clock`` and itsdangerous stamps and checks the cookie's
signature age with ``TimestampSigner.get_timestamp``. ``shifted_clock`` moves
both by the same offset, so a request "later" is stamped, signed and verified
as if that much time had passed.
"""

from __future__ import annotations

import json
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import httpx2
import test_auth_bootstrap as bootstrap
from flask_login.config import COOKIE_NAME as REMEMBER_COOKIE
from itsdangerous import TimestampSigner
from mcp_support import McpDbCase
from werkzeug.security import generate_password_hash

from app.config import Config
from app.extensions import db
from app.mcp_server import http
from app.models import RoleEnum, User

PASSWORD = "CorrectPassword123!"
DASHBOARD = "/dashboard/"
IDLE = timedelta(hours=8)
ABSOLUTE = timedelta(hours=12)
MINUTE = timedelta(minutes=1)
SIGNED_IN_AT, SEEN_AT = "_signed_in_at", "_seen_at"
EXPIRED = "La sesión ha caducado. Inicie sesión de nuevo."


@contextmanager
def shifted_clock(offset: timedelta) -> Iterator[None]:
    """Run the server as if ``offset`` had passed: session stamps and signature age."""
    seconds = int(offset.total_seconds())
    with (
        patch("app.session_lifetime._clock", lambda: time.time() + seconds),
        patch.object(
            TimestampSigner, "get_timestamp", lambda self: int(time.time()) + seconds
        ),
    ):
        yield


def session_cookie_attributes(response, cookie_name: str) -> dict[str, str] | None:
    """The lower-cased attributes the response's ``Set-Cookie`` gives the session cookie."""
    for header in response.headers.getlist("Set-Cookie"):
        name, _, rest = header.partition("=")
        if name == cookie_name:
            attributes = {}
            for part in rest.split(";")[1:]:
                key, _, value = part.strip().partition("=")
                attributes[key.lower()] = value
            return attributes
    return None


class SessionLifetimeTestCase(unittest.TestCase):
    #: Extra ``build_app`` configuration.
    app_config: dict = {}

    def setUp(self) -> None:
        self.app = bootstrap.build_app(**self.app_config)
        self.cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        self.client = self.app.test_client()
        self.elapsed = timedelta(0)
        with self.app.app_context():
            db.create_all()
            db.session.add(
                User(
                    username="operativa",
                    email="operativa@example.com",
                    password=generate_password_hash(PASSWORD),
                    role=RoleEnum.OPERATIVO,
                )
            )
            db.session.commit()
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def sign_in(self):
        response = self.client.post(
            "/login", data={"username": "operativa", "password": PASSWORD}
        )
        self.assertTrue(response.location.endswith(DASHBOARD))
        return response

    def stored(self) -> dict:
        """The client's session, read at the test's current virtual time."""
        with shifted_clock(self.elapsed), self.client.session_transaction() as session:
            return dict(session)

    def get_at(self, elapsed: timedelta, path: str = DASHBOARD, **kwargs):
        """GET ``path`` as if ``elapsed`` had passed since the test began."""
        self.elapsed = elapsed
        with shifted_clock(elapsed):
            return self.client.get(path, **kwargs)

    def assert_signed_out(self, response, *, message: str | None = EXPIRED) -> None:
        self.assertEqual(302, response.status_code)
        self.assertIn("/login", response.location)
        stored = self.stored()
        self.assertNotIn("_user_id", stored)
        if message is not None:
            self.assertIn(("warning", message), stored.get("_flashes", []))


class BrowserSessionCookieTestCase(SessionLifetimeTestCase):
    def test_the_session_cookie_ends_when_the_browser_closes(self) -> None:
        attributes = session_cookie_attributes(self.sign_in(), self.cookie_name)

        self.assertIsNotNone(attributes, "sign-in sets no session cookie")
        self.assertNotIn("expires", attributes)
        self.assertNotIn("max-age", attributes)

    def test_sign_in_stamps_the_session_in_utc_epoch_seconds(self) -> None:
        before = int(time.time())
        self.sign_in()

        stored = self.stored()
        self.assertIsInstance(stored[SIGNED_IN_AT], int)
        self.assertEqual(stored[SIGNED_IN_AT], stored[SEEN_AT])
        self.assertLessEqual(before, stored[SIGNED_IN_AT])
        self.assertLessEqual(stored[SIGNED_IN_AT], int(time.time()))

    def test_the_limits_are_configured(self) -> None:
        self.assertEqual(IDLE, Config.SESSION_IDLE_TIMEOUT)
        self.assertEqual(ABSOLUTE, Config.SESSION_ABSOLUTE_LIFETIME)
        self.assertEqual(ABSOLUTE, self.app.permanent_session_lifetime)

    def test_no_remember_me_cookie_is_offered_or_configured(self) -> None:
        response = self.sign_in()

        remember = self.app.config.get("REMEMBER_COOKIE_NAME", REMEMBER_COOKIE)
        cookies = response.headers.getlist("Set-Cookie")
        self.assertFalse([c for c in cookies if c.startswith(f"{remember}=")])
        self.assertEqual(
            [], [name for name in vars(Config) if name.startswith("REMEMBER_COOKIE")]
        )

    def test_sign_out_clears_the_session(self) -> None:
        self.sign_in()
        self.assertEqual(200, self.client.get(DASHBOARD).status_code)

        self.assert_signed_out(self.client.get("/logout"), message=None)

        stored = self.stored()
        self.assertNotIn(SIGNED_IN_AT, stored)
        self.assertNotIn(SEEN_AT, stored)
        self.assert_signed_out(self.client.get(DASHBOARD), message=None)


class ActivityTestCase(SessionLifetimeTestCase):
    def test_active_use_keeps_the_session_past_eight_hours(self) -> None:
        self.sign_in()
        signed_in_at = self.stored()[SIGNED_IN_AT]

        for hours in (5, 10, 11.75):
            with self.subTest(hours=hours):
                response = self.get_at(timedelta(hours=hours))
                self.assertEqual(200, response.status_code)
                self.assertEqual(signed_in_at, self.stored()[SIGNED_IN_AT])
                self.assertAlmostEqual(
                    signed_in_at + hours * 3600, self.stored()[SEEN_AT], delta=5
                )

    def test_the_last_seen_time_is_rewritten_at_most_once_a_minute(self) -> None:
        self.sign_in()
        self.assertEqual(200, self.client.get(DASHBOARD).status_code)  # shows the flash
        seen_at = self.stored()[SEEN_AT]

        response = self.get_at(timedelta(seconds=30))
        self.assertEqual(200, response.status_code)
        self.assertIsNone(session_cookie_attributes(response, self.cookie_name))
        self.assertEqual(seen_at, self.stored()[SEEN_AT])

        response = self.get_at(timedelta(minutes=2))
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(session_cookie_attributes(response, self.cookie_name))
        self.assertAlmostEqual(seen_at + 120, self.stored()[SEEN_AT], delta=5)

    def test_an_idle_gap_over_eight_hours_signs_out_with_the_expiry_message(self) -> None:
        self.sign_in()
        self.assertEqual(200, self.get_at(IDLE - MINUTE).status_code)
        self.client, self.elapsed = self.app.test_client(), timedelta(0)
        self.sign_in()

        with self.assertLogs("security", level="INFO") as logged:
            response = self.get_at(IDLE + MINUTE)

        self.assert_signed_out(response)
        self.assertIn("reason=idle", "\n".join(logged.output))

    def test_twelve_hours_after_sign_in_signs_out_despite_activity(self) -> None:
        self.sign_in()
        for hours in (4, 8, 11.9):
            self.assertEqual(200, self.get_at(timedelta(hours=hours)).status_code)

        with self.assertLogs("security", level="INFO") as logged:
            response = self.get_at(ABSOLUTE + MINUTE)

        self.assert_signed_out(response)
        self.assertIn("SESSION_EXPIRED | user=operativa", "\n".join(logged.output))
        self.assertIn("reason=absolute", "\n".join(logged.output))
        self.assertNotIn(SIGNED_IN_AT, self.stored())
        self.assert_signed_out(self.get_at(ABSOLUTE + 2 * MINUTE), message=None)

    def test_a_session_without_stamps_is_signed_out(self) -> None:
        self.sign_in()
        with self.client.session_transaction() as session:
            del session[SIGNED_IN_AT], session[SEEN_AT]

        self.assert_signed_out(self.client.get(DASHBOARD))

    def test_a_json_request_is_answered_like_an_unauthenticated_one(self) -> None:
        headers = {"Accept": "application/json"}
        anonymous = self.app.test_client().get(DASHBOARD, headers=headers)
        self.sign_in()
        self.assertEqual(200, self.get_at(timedelta(hours=6)).status_code)

        response = self.get_at(ABSOLUTE + MINUTE, headers=headers)

        self.assert_signed_out(response)
        self.assertEqual(anonymous.location, response.location)


class TokenRequestsTestCase(McpDbCase):
    """Bearer-token (MCP) requests carry no session, so the limits never apply."""

    async def test_a_token_request_is_unaffected_by_the_session_limits(self) -> None:
        token, _, _ = self.issue_token()
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "qms_modules", "arguments": {}}}
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        asgi = http.create_http_app(self.app, allowed_hosts=["127.0.0.1:8000"])
        with shifted_clock(ABSOLUTE + IDLE):
            async with asgi.router.lifespan_context(asgi):
                transport = httpx2.ASGITransport(app=asgi)
                async with httpx2.AsyncClient(
                    transport=transport, base_url="http://127.0.0.1:8000"
                ) as client:
                    response = await client.post("/mcp", content=json.dumps(body),
                                                 headers=headers)

        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["result"]["isError"])
        self.assertNotIn("set-cookie", response.headers)


if __name__ == "__main__":
    unittest.main()
