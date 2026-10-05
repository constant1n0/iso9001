"""An inactive account is refused on every web authentication path (UM-1).

Deactivation never deletes the row, so attribution survives; these tests pin
that the account can no longer be used: no login, no session, no remember
cookie, no reset e-mail and no reset with an already issued link. Every
refusal looks exactly like the existing generic one, so the response never
reveals that the account exists or is inactive.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from flask_login.utils import encode_cookie
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import RoleEnum, User
from app.utils import password_reset_mail


PASSWORD = "CorrectPassword123!"
CANONICAL_RESET_BASE = "https://qms.example.invalid"
DASHBOARD = "/dashboard/"
INVALID_RESET_MESSAGE = "El enlace de recuperación es inválido o ha expirado."


class AccountStateTestCase(unittest.TestCase):
    """Exercise the web paths through real Flask and SQLite boundaries."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(PASSWORD_RESET_BASE_URL=CANONICAL_RESET_BASE)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            self.active_id = self._add_user("activa", active=True)
            self.inactive_id = self._add_user("inactiva", active=False)

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def _add_user(username: str, *, active: bool) -> int:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password=generate_password_hash(PASSWORD),
            role=RoleEnum.OPERATIVO,
            active=active,
        )
        db.session.add(user)
        db.session.commit()
        return user.id

    def _set_active(self, user_id: int, active: bool) -> None:
        with self.app.app_context():
            db.session.get(User, user_id).active = active
            db.session.commit()

    def _signature(self, response) -> tuple:
        with self.client.session_transaction() as session:
            flashes = tuple(session.pop("_flashes", ()))
        return response.status_code, response.location, flashes

    def _session_user_id(self) -> str | None:
        with self.client.session_transaction() as session:
            return session.get("_user_id")

    def _login(self, username: str, password: str = PASSWORD):
        return self.client.post(
            "/login", data={"username": username, "password": password}
        )

    def _redirects_to_login(self, response) -> bool:
        return response.status_code == 302 and "/login" in response.location


class ModelTestCase(AccountStateTestCase):
    def test_new_users_are_active_unless_told_otherwise(self) -> None:
        with self.app.app_context():
            user = User(username="nuevo", password="x", role=RoleEnum.OPERATIVO)
            db.session.add(user)
            db.session.commit()
            self.assertIs(True, user.active)
            self.assertTrue(user.is_active)

    def test_is_active_follows_the_column(self) -> None:
        with self.app.app_context():
            self.assertTrue(db.session.get(User, self.active_id).is_active)
            self.assertFalse(db.session.get(User, self.inactive_id).is_active)


class SessionTestCase(AccountStateTestCase):
    def test_existing_session_stops_working_once_the_user_is_deactivated(self) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.active_id)
        self.assertEqual(200, self.client.get(DASHBOARD).status_code)

        self._set_active(self.active_id, False)

        self.assertTrue(self._redirects_to_login(self.client.get(DASHBOARD)))

    def test_remember_cookie_of_an_inactive_user_is_ignored(self) -> None:
        for user_id, allowed in ((self.active_id, True), (self.inactive_id, False)):
            with self.subTest(allowed=allowed):
                client = self.app.test_client()
                with self.app.test_request_context():
                    cookie = encode_cookie(str(user_id))
                client.set_cookie("remember_token", cookie)
                response = client.get(DASHBOARD)
                if allowed:
                    self.assertEqual(200, response.status_code)
                else:
                    self.assertTrue(self._redirects_to_login(response))


class LoginTestCase(AccountStateTestCase):
    def test_active_user_still_logs_in(self) -> None:
        response = self._login("activa")

        self.assertTrue(response.location.endswith(DASHBOARD))
        self.assertEqual(str(self.active_id), self._session_user_id())

    def test_inactive_user_gets_the_same_refusal_as_wrong_credentials(self) -> None:
        wrong = self._signature(self._login("activa", "WrongPassword123!"))

        with self.assertLogs("security", level="WARNING") as logs:
            refused = self._signature(self._login("inactiva"))

        self.assertEqual(wrong, refused)
        self.assertTrue(refused[1].endswith("/login"))
        self.assertIsNone(self._session_user_id())
        self.assertTrue(
            any(
                "LOGIN_FAILED" in line
                and "user=inactiva" in line
                and "reason=inactive" in line
                for line in logs.output
            ),
            logs.output,
        )


class ResetTestCase(AccountStateTestCase):
    def _request_reset(self, email: str):
        return self.client.post("/reset_password_request", data={"email": email})

    def test_reset_request_for_an_inactive_user_sends_nothing(self) -> None:
        with patch.object(password_reset_mail.mail, "send") as mail_send:
            unknown = self._signature(self._request_reset("nadie@example.com"))
            with self.assertLogs("security", level="WARNING") as logs:
                refused = self._signature(self._request_reset("inactiva@example.com"))

        mail_send.assert_not_called()
        self.assertEqual(unknown, refused)
        self.assertTrue(
            any("reason=inactive" in line for line in logs.output), logs.output
        )

    def test_valid_reset_link_is_refused_once_the_user_is_inactive(self) -> None:
        with self.app.app_context():
            token = db.session.get(User, self.active_id).get_reset_token()
        self._set_active(self.active_id, False)
        new_password = "AfterPassword123!"

        shown = self.client.get(f"/reset_password/{token}")
        shown_signature = self._signature(shown)
        posted = self.client.post(
            f"/reset_password/{token}",
            data={"password": new_password, "confirm_password": new_password},
        )

        for response, signature in (
            (shown, shown_signature),
            (posted, self._signature(posted)),
        ):
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.location.endswith("/reset_password_request"))
            self.assertIn(("warning", INVALID_RESET_MESSAGE), signature[2])
        with self.app.app_context():
            user = db.session.get(User, self.active_id)
            self.assertTrue(check_password_hash(user.password, PASSWORD))

    def test_atomic_reset_update_does_not_touch_an_inactive_user(self) -> None:
        with self.app.app_context():
            user = db.session.get(User, self.inactive_id)
            original = user.password
            updated = User.update_password_from_reset(
                user.id, original, generate_password_hash("AfterPassword123!")
            )
            db.session.expire_all()
            self.assertFalse(updated)
            self.assertEqual(original, db.session.get(User, self.inactive_id).password)


if __name__ == "__main__":
    unittest.main()
