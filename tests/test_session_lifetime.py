"""A web session lasts eight hours from the last request (DD4).

Signing in makes the session permanent, so the session cookie carries an
expiry eight hours ahead (``PERMANENT_SESSION_LIFETIME``) and, with Flask's
``SESSION_REFRESH_EACH_REQUEST``, every request pushes it forward: the lifetime
is an idle timeout. A cookie whose signature is older than eight hours is no
longer signed in. No "remember me" is offered.

Time is moved on the server only, without sleeping: Flask dates the cookie's
expiry with ``datetime.now`` in ``flask.sessions``, and itsdangerous stamps and
checks the cookie's signature age with ``TimestampSigner.get_timestamp``.
``shifted_clock`` moves both by the same offset, so a request "later" is signed,
dated and verified as if that much time had passed. The test client keeps
sending the cookie whatever its ``Expires``, so these tests pin the server-side
check as well as the attribute the browser relies on.
"""

from __future__ import annotations

import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from flask_login.config import COOKIE_NAME as REMEMBER_COOKIE
from itsdangerous import TimestampSigner
from werkzeug.security import generate_password_hash

from app.config import Config
from app.extensions import db
from app.models import RoleEnum, User

PASSWORD = "CorrectPassword123!"
DASHBOARD = "/dashboard/"
LIFETIME = timedelta(hours=8)
TOLERANCE = timedelta(minutes=1)


@contextmanager
def shifted_clock(offset: timedelta) -> Iterator[None]:
    """Run the server as if ``offset`` had passed: cookie expiry and signature age."""
    seconds = int(offset.total_seconds())

    class ShiftedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime.now(tz) + offset

    with (
        patch("flask.sessions.datetime", ShiftedDatetime),
        patch.object(
            TimestampSigner, "get_timestamp", lambda self: int(time.time()) + seconds
        ),
    ):
        yield


def session_cookie_expiry(response, cookie_name: str) -> datetime | None:
    """The expiry the response's ``Set-Cookie`` gives the session cookie, if any."""
    for header in response.headers.getlist("Set-Cookie"):
        name, _, rest = header.partition("=")
        if name != cookie_name:
            continue
        attributes = {}
        for part in rest.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            attributes[key.lower()] = value
        if "max-age" in attributes:
            return datetime.now(timezone.utc) + timedelta(
                seconds=int(attributes["max-age"])
            )
        if "expires" in attributes:
            return parsedate_to_datetime(attributes["expires"])
        return None
    raise AssertionError(f"the response sets no {cookie_name!r} cookie")


class SessionLifetimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        self.client = self.app.test_client()
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

    def assert_expires_near(self, response, expected: datetime) -> None:
        expiry = session_cookie_expiry(response, self.cookie_name)
        self.assertIsNotNone(expiry, "the session cookie carries no expiry")
        self.assertLessEqual(abs(expiry - expected), TOLERANCE)

    def assert_signed_out(self, response) -> None:
        self.assertEqual(302, response.status_code)
        self.assertIn("/login", response.location)

    def test_sign_in_gives_the_session_cookie_an_eight_hour_expiry(self) -> None:
        response = self.sign_in()

        self.assert_expires_near(response, datetime.now(timezone.utc) + LIFETIME)

    def test_each_request_pushes_the_expiry_forward(self) -> None:
        self.sign_in()
        self.assertEqual(200, self.client.get(DASHBOARD).status_code)

        with shifted_clock(timedelta(hours=1)):
            response = self.client.get(DASHBOARD)
        self.assertEqual(200, response.status_code)
        self.assert_expires_near(
            response, datetime.now(timezone.utc) + timedelta(hours=1) + LIFETIME
        )

        # Signed in eight and a half hours ago, but active seven and a half ago.
        with shifted_clock(timedelta(hours=8, minutes=30)):
            self.assertEqual(200, self.client.get(DASHBOARD).status_code)

    def test_a_session_idle_for_more_than_eight_hours_is_signed_out(self) -> None:
        cases = ((LIFETIME - TOLERANCE, True), (LIFETIME + TOLERANCE, False))
        for idle, signed_in in cases:
            with self.subTest(idle=idle):
                self.client = self.app.test_client()
                self.sign_in()
                with shifted_clock(idle):
                    response = self.client.get(DASHBOARD)
                if signed_in:
                    self.assertEqual(200, response.status_code)
                else:
                    self.assert_signed_out(response)

    def test_sessions_are_refreshed_on_each_request(self) -> None:
        self.assertIs(True, self.app.config["SESSION_REFRESH_EACH_REQUEST"])
        self.assertEqual(LIFETIME, self.app.permanent_session_lifetime)

    def test_no_remember_me_cookie_is_offered_or_configured(self) -> None:
        response = self.sign_in()

        remember = self.app.config.get("REMEMBER_COOKIE_NAME", REMEMBER_COOKIE)
        cookies = response.headers.getlist("Set-Cookie")
        self.assertFalse([c for c in cookies if c.startswith(f"{remember}=")])
        self.assertEqual(
            [], [name for name in vars(Config) if name.startswith("REMEMBER_COOKIE")]
        )

    def test_sign_out_ends_the_session(self) -> None:
        self.sign_in()
        self.assertEqual(200, self.client.get(DASHBOARD).status_code)

        self.assert_signed_out(self.client.get("/logout"))

        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        self.assert_signed_out(self.client.get(DASHBOARD))


if __name__ == "__main__":
    unittest.main()
