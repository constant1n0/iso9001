"""Integration tests for secure password recovery."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import RoleEnum, User
from app.routes import auth_routes


CANONICAL_RESET_BASE = "https://qms.example.invalid"
GENERIC_RESET_MESSAGE = (
    "Se ha enviado un correo con instrucciones para restablecer tu contraseña."
)
INVALID_RESET_MESSAGE = "El enlace de recuperación es inválido o ha expirado."


class AuthResetTestCase(unittest.TestCase):
    """Exercise password recovery through Flask and real SQLite boundaries."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(
            PASSWORD_RESET_BASE_URL=CANONICAL_RESET_BASE
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            self.user = User(
                username="resetuser",
                email="reset@example.com",
                password=generate_password_hash("BeforePassword123!"),
                role=RoleEnum.OPERATIVO,
            )
            db.session.add(self.user)
            db.session.commit()
            self.user_id = self.user.id
            self.original_hash = self.user.password

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def _response_signature(client, response):
        with client.session_transaction() as session:
            flashes = tuple(session.pop("_flashes", ()))
        return response.status_code, response.location, flashes

    def _signed_payload(self, payload: object) -> str:
        serializer = URLSafeTimedSerializer(self.app.config["SECRET_KEY"])
        return serializer.dumps(payload, salt="password-reset-salt")

    def test_missing_or_invalid_base_disables_email_without_breaking_app(self) -> None:
        for reset_base in (None, "http://qms.example.invalid"):
            with self.subTest(reset_base=reset_base):
                app = bootstrap.build_app(PASSWORD_RESET_BASE_URL=reset_base)
                client = app.test_client()
                with app.app_context():
                    db.create_all()
                    db.session.add(
                        User(
                            username="operator",
                            email="operator@example.com",
                            password=generate_password_hash("BeforePassword123!"),
                            role=RoleEnum.OPERATIVO,
                        )
                    )
                    db.session.commit()

                self.assertEqual(200, client.get("/login").status_code)
                with patch.object(auth_routes.mail, "send") as mail_send:
                    response = client.post(
                        "/reset_password_request",
                        data={"email": "operator@example.com"},
                    )

                self.assertEqual(302, response.status_code)
                self.assertTrue(response.location.endswith("/login"))
                mail_send.assert_not_called()
                with client.session_transaction() as session:
                    self.assertEqual(
                        [("info", GENERIC_RESET_MESSAGE)],
                        session.pop("_flashes"),
                    )
                with app.app_context():
                    db.session.remove()
                    db.drop_all()

    def test_reset_base_rejects_unsafe_or_malformed_values(self) -> None:
        invalid_values = (
            None,
            "",
            "http://qms.example.invalid",
            "https://user:password@qms.example.invalid",
            "https://qms.example.invalid?next=/reset",
            "https://qms.example.invalid#fragment",
            " https://qms.example.invalid",
            "https://qms.example.invalid\n",
            "https://qms.example.invalid\\reset",
            "https://qms.example.invalid:not-a-port",
            "https://-invalid.example",
            "qms.example.invalid",
            "https://qms.example.invalid/application-prefix",
        )

        with self.app.app_context():
            for value in invalid_values:
                with self.subTest(value=value):
                    self.app.config["PASSWORD_RESET_BASE_URL"] = value
                    with self.assertRaises(
                        auth_routes.ResetEmailConfigurationError
                    ):
                        auth_routes.build_password_reset_url("safe-token")

    def test_malicious_host_cannot_change_emailed_reset_origin(self) -> None:
        with patch.object(auth_routes.mail, "send") as mail_send:
            response = self.client.post(
                "/reset_password_request",
                base_url="https://attacker.example",
                data={"email": "reset@example.com"},
            )

        self.assertEqual(302, response.status_code)
        message = mail_send.call_args.args[0]
        reset_url = next(
            line for line in message.body.splitlines() if line.startswith("https://")
        )
        self.assertTrue(
            reset_url.startswith(f"{CANONICAL_RESET_BASE}/reset_password/")
        )
        self.assertNotIn("attacker.example", message.body)

    def test_reset_request_responses_do_not_enumerate_accounts_or_failures(self) -> None:
        signatures = []
        scenarios = (
            ("known", "reset@example.com", CANONICAL_RESET_BASE, None),
            ("unknown", "unknown@example.com", CANONICAL_RESET_BASE, None),
            ("invalid config", "reset@example.com", None, None),
            (
                "mail failure",
                "reset@example.com",
                CANONICAL_RESET_BASE,
                RuntimeError("sensitive mail detail"),
            ),
        )

        for label, email, reset_base, mail_error in scenarios:
            with self.subTest(label=label):
                self.app.config["PASSWORD_RESET_BASE_URL"] = reset_base
                with patch.object(
                    auth_routes.mail,
                    "send",
                    side_effect=mail_error,
                ):
                    response = self.client.post(
                        "/reset_password_request",
                        data={"email": email},
                    )
                signatures.append(self._response_signature(self.client, response))

        self.assertTrue(all(signature == signatures[0] for signature in signatures))
        self.assertEqual(("info", GENERIC_RESET_MESSAGE), signatures[0][2][0])

    def test_reset_failure_logs_do_not_include_token_or_reset_url(self) -> None:
        self.app.config["PASSWORD_RESET_BASE_URL"] = None

        with self.assertLogs("security", level="WARNING") as captured:
            response = self.client.post(
                "/reset_password_request",
                data={"email": "reset@example.com"},
            )

        self.assertEqual(302, response.status_code)
        output = "\n".join(captured.output)
        self.assertNotIn("/reset_password/", output)
        self.assertNotIn("eyJ", output)

    def test_token_payload_hides_password_hash_and_verifies_current_state(self) -> None:
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            token = user.get_reset_token()
            payload = URLSafeTimedSerializer(
                self.app.config["SECRET_KEY"]
            ).loads(token, salt="password-reset-salt")
            verification = User.verify_reset_token(token)

            self.assertEqual(
                {"user_id", "password_fingerprint"}, set(payload)
            )
            self.assertNotIn(user.password, repr(payload))
            self.assertEqual(64, len(payload["password_fingerprint"]))
            self.assertEqual(user.id, verification.user.id)
            self.assertEqual(user.password, verification.expected_password_hash)

    def test_invalid_expired_unknown_and_legacy_tokens_are_rejected(self) -> None:
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            with patch("itsdangerous.timed.time.time", return_value=1_000):
                expiring_token = user.get_reset_token()

            invalid_tokens = (
                "not-a-token",
                self._signed_payload({}),
                self._signed_payload(
                    {"user_id": True, "password_fingerprint": "0" * 64}
                ),
                self._signed_payload(
                    {"user_id": user.id, "password_fingerprint": "é" * 64}
                ),
                self._signed_payload(
                    {"user_id": user.id, "password_fingerprint": "g" * 64}
                ),
                self._signed_payload({"user_id": user.id}),
                self._signed_payload(
                    {"user_id": 999_999, "password_fingerprint": "0" * 64}
                ),
                f"{user.get_reset_token()}tampered",
            )

            signatures = []
            for token in invalid_tokens:
                with self.subTest(token=token[:24]):
                    self.assertIsNone(User.verify_reset_token(token))
                    response = self.client.post(
                        f"/reset_password/{token}",
                        data={
                            "password": "RejectedPassword123!",
                            "confirm_password": "RejectedPassword123!",
                        },
                    )
                    signatures.append(
                        self._response_signature(self.client, response)
                    )

            with patch("itsdangerous.timed.time.time", return_value=4_601):
                self.assertIsNone(User.verify_reset_token(expiring_token))
                expired_response = self.client.post(
                    f"/reset_password/{expiring_token}",
                    data={
                        "password": "RejectedPassword123!",
                        "confirm_password": "RejectedPassword123!",
                    },
                )
                signatures.append(
                    self._response_signature(self.client, expired_response)
                )

            self.assertTrue(
                all(signature == signatures[0] for signature in signatures)
            )
            self.assertTrue(
                signatures[0][1].endswith("/reset_password_request")
            )
            self.assertEqual(
                ("warning", INVALID_RESET_MESSAGE),
                signatures[0][2][0],
            )
            self.assertEqual(self.original_hash, user.password)

    def test_successful_reset_changes_password_and_rejects_replay(self) -> None:
        new_password = "AfterPassword123!"
        replay_password = "ReplayPassword123!"
        with self.app.app_context():
            token = db.session.get(User, self.user_id).get_reset_token()

        response = self.client.post(
            f"/reset_password/{token}",
            data={"password": new_password, "confirm_password": new_password},
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith("/login"))

        replay = self.client.post(
            f"/reset_password/{token}",
            data={
                "password": replay_password,
                "confirm_password": replay_password,
            },
        )
        self.assertEqual(302, replay.status_code)
        self.assertTrue(replay.location.endswith("/reset_password_request"))
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertTrue(check_password_hash(user.password, new_password))
            self.assertFalse(check_password_hash(user.password, replay_password))

    def test_independent_password_change_invalidates_all_prior_tokens(self) -> None:
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            first_token = user.get_reset_token()
            second_token = user.get_reset_token()
            user.password = generate_password_hash("IndependentChange123!")
            db.session.commit()

            self.assertIsNone(User.verify_reset_token(first_token))
            self.assertIsNone(User.verify_reset_token(second_token))

    def test_atomic_reset_update_allows_only_first_stale_snapshot(self) -> None:
        winner_password = generate_password_hash("WinnerPassword123!")
        loser_password = generate_password_hash("LoserPassword123!")

        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            verification = User.verify_reset_token(user.get_reset_token())

            first_updated = User.update_password_from_reset(
                verification.user.id,
                verification.expected_password_hash,
                winner_password,
            )
            second_updated = User.update_password_from_reset(
                verification.user.id,
                verification.expected_password_hash,
                loser_password,
            )

            stored_user = db.session.get(User, self.user_id)
            self.assertTrue(first_updated)
            self.assertFalse(second_updated)
            self.assertEqual(winner_password, stored_user.password)

    def test_database_failure_rolls_back_without_sensitive_response(self) -> None:
        new_password = "AfterFailure123!"
        with self.app.app_context():
            token = db.session.get(User, self.user_id).get_reset_token()

        with patch.object(
            db.session,
            "commit",
            side_effect=SQLAlchemyError("sensitive database detail"),
        ):
            response = self.client.post(
                f"/reset_password/{token}",
                data={"password": new_password, "confirm_password": new_password},
            )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith("/reset_password_request"))
        self.assertNotIn(b"sensitive database detail", response.data)
        with self.app.app_context():
            stored_user = db.session.get(User, self.user_id)
            self.assertEqual(self.original_hash, stored_user.password)

    def test_database_outage_after_rollback_keeps_invalid_link_response(self) -> None:
        """Do not reload expired user fields when the database remains unavailable."""
        password = "AfterFailure123!"
        with self.app.app_context():
            token = db.session.get(User, self.user_id).get_reset_token()
            session = db.session()
            original_execute = session.execute
            update_attempted = False

            def fail_after_update(
                statement: Any, *args: Any, **kwargs: Any
            ) -> Any:
                nonlocal update_attempted
                if statement.is_update:
                    update_attempted = True
                if update_attempted:
                    raise SQLAlchemyError("sensitive database detail")
                return original_execute(statement, *args, **kwargs)

            with patch.object(session, "execute", side_effect=fail_after_update):
                response = self.client.post(
                    f"/reset_password/{token}",
                    data={"password": password, "confirm_password": password},
                )

        self.assertTrue(update_attempted)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith("/reset_password_request"))
        self.assertNotIn(b"sensitive database detail", response.data)

    def test_rollback_failure_after_update_error_keeps_invalid_link(self) -> None:
        """Return the generic response even if cleanup fails after an update error."""
        password = "AfterFailure123!"
        with self.app.app_context():
            token = db.session.get(User, self.user_id).get_reset_token()
            session = db.session()
            original_execute = session.execute

            def fail_update(statement: Any, *args: Any, **kwargs: Any) -> Any:
                if statement.is_update:
                    raise SQLAlchemyError("sensitive update detail")
                return original_execute(statement, *args, **kwargs)

            with (
                patch.object(session, "execute", side_effect=fail_update),
                patch.object(
                    session,
                    "rollback",
                    side_effect=SQLAlchemyError("sensitive rollback detail"),
                ),
            ):
                response = self.client.post(
                    f"/reset_password/{token}",
                    data={"password": password, "confirm_password": password},
                )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith("/reset_password_request"))
        self.assertNotIn(b"sensitive rollback detail", response.data)
        self.assertEqual(
            (("warning", INVALID_RESET_MESSAGE),),
            self._response_signature(self.client, response)[2],
        )

    def test_rollback_failure_after_zero_row_returns_false(self) -> None:
        """Keep the conditional update's failure contract if cleanup fails."""
        with self.app.app_context():
            with patch.object(
                db.session(),
                "rollback",
                side_effect=SQLAlchemyError("sensitive rollback detail"),
            ) as rollback:
                updated = User.update_password_from_reset(
                    self.user_id,
                    "stale password hash",
                    generate_password_hash("AfterFailure123!"),
                )

        self.assertFalse(updated)
        rollback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
