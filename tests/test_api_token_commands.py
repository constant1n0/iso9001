"""CLI commands that issue, list and revoke API tokens.

The commands act as a CLI actor (channel ``cli``, administrator rights) and
own the transaction. The plaintext token is printed once, by ``create``.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import ApiToken, AuditLog, RoleEnum, User

SECRET_KEY = bootstrap.BASE_TEST_CONFIG["SECRET_KEY"]
SHAPE = re.compile(r"iso_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


class CommandBase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.runner = self.app.test_cli_runner()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(username="ana", password="x", role=RoleEnum.OPERATIVO))
        db.session.add(User(username="luis", password="x", role=RoleEnum.AUDITOR))
        db.session.commit()
        patcher = patch("app.commands.getpass.getuser", return_value="ops")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def run_cli(self, *args: str):
        return self.runner.invoke(args=list(args))

    def create(self, *args: str):
        return self.run_cli("create-api-token", "--user", "ana", "--name", "laptop", *args)

    def plaintext(self, result) -> str:
        (token,) = SHAPE.findall(result.output)
        return token


class SecretKeyTestCase(CommandBase):
    def assert_clear_failure(self) -> None:
        result = self.create()
        self.assertNotEqual(0, result.exit_code)
        self.assertIsInstance(result.exception, SystemExit)
        self.assertIn("SECRET_KEY", result.output)
        self.assertNotIn("Traceback", result.output)
        self.assertEqual(0, db.session.query(ApiToken).count())

    def test_a_missing_secret_key_is_a_clear_error(self) -> None:
        self.app.config.pop("SECRET_KEY", None)
        self.assert_clear_failure()

    def test_an_empty_secret_key_is_a_clear_error(self) -> None:
        self.app.config["SECRET_KEY"] = ""
        self.assert_clear_failure()


class CreateTestCase(CommandBase):
    def test_prints_the_token_once_with_a_warning_and_stores_no_plaintext(self) -> None:
        result = self.create()
        self.assertEqual(0, result.exit_code, result.output)
        token = self.plaintext(result)
        self.assertEqual(1, result.output.count(token))
        self.assertIn("only once", result.output)
        row = db.session.query(ApiToken).one()
        self.assertEqual(token.split("_", 2)[1], row.prefix)
        columns = {c.name: getattr(row, c.name) for c in ApiToken.__table__.columns}
        self.assertNotIn(token.split("_", 2)[2], json.dumps(columns, default=str))

    def test_defaults_are_read_scope_and_ninety_days(self) -> None:
        self.assertEqual(0, self.create().exit_code)
        row = db.session.query(ApiToken).one()
        expires = row.expires_at.replace(tzinfo=timezone.utc)
        self.assertLess(
            abs(expires - (datetime.now(timezone.utc) + timedelta(days=90))),
            timedelta(minutes=1),
        )
        self.assertEqual("read", row.scopes)

    def test_scopes_and_days_are_options(self) -> None:
        result = self.create("--scope", "read", "--scope", "write", "--days", "30")
        self.assertEqual(0, result.exit_code, result.output)
        row = db.session.query(ApiToken).one()
        self.assertEqual("read write", row.scopes)
        self.assertEqual("ana", db.session.get(User, row.user_id).username)

    def test_the_issue_is_audited_as_the_cli_actor(self) -> None:
        self.assertEqual(0, self.create().exit_code)
        entry = db.session.query(AuditLog).filter_by(entity_type="api_tokens").one()
        self.assertEqual(("create", "cli", "cli:ops", None),
                         (entry.action, entry.channel, entry.actor_label, entry.actor_user_id))
        self.assertEqual("cli:ops", db.session.query(ApiToken).one().created_by_label)

    def test_the_issue_is_security_logged_without_the_secret(self) -> None:
        with self.assertLogs("security", level="INFO") as logs:
            result = self.create()
        secret = self.plaintext(result).split("_", 2)[2]
        text = "\n".join(logs.output)
        self.assertIn("API_TOKEN_ISSUED", text)
        self.assertIn("user=ana", text)
        self.assertNotIn(secret, text)

    def test_invalid_input_fails_without_creating_anything(self) -> None:
        cases = [
            ("--user", "nobody", "--name", "x"),
            ("--user", "ana", "--name", "x", "--days", "0"),
            ("--user", "ana", "--name", "x", "--days", "366"),
            ("--user", "ana", "--name", "   "),
            ("--user", "ana", "--name", "x", "--scope", "admin"),
            ("--name", "x"),
            ("--user", "ana"),
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_cli("create-api-token", *args)
                self.assertNotEqual(0, result.exit_code)
                self.assertNotRegex(result.output, SHAPE)
        self.assertEqual(0, db.session.query(ApiToken).count())
        self.assertIn("not found", self.run_cli(
            "create-api-token", "--user", "nobody", "--name", "x").output)

    def test_a_database_failure_rolls_back_and_prints_no_token(self) -> None:
        with patch("app.commands.api_tokens.issue", side_effect=SQLAlchemyError("boom")):
            result = self.create()
        self.assertNotEqual(0, result.exit_code)
        self.assertNotRegex(result.output, SHAPE)
        self.assertNotIn("boom", result.output)
        self.assertEqual(0, db.session.query(ApiToken).count())


class ListAndRevokeTestCase(CommandBase):
    def test_list_shows_metadata_and_never_secrets_or_hashes(self) -> None:
        token = self.plaintext(self.create("--scope", "write"))
        row = db.session.query(ApiToken).one()
        result = self.run_cli("list-api-tokens")
        self.assertEqual(0, result.exit_code, result.output)
        for expected in (row.prefix, "ana", "laptop", "write", "active"):
            self.assertIn(expected, result.output)
        self.assertNotIn(token.split("_", 2)[2], result.output)
        self.assertNotIn(row.token_hash, result.output)
        self.assertNotRegex(result.output, SHAPE)

    def test_list_filters_by_user_and_reports_empty(self) -> None:
        self.assertEqual(0, self.create().exit_code)
        self.assertIn("No API tokens", self.run_cli("list-api-tokens", "--user", "luis").output)
        self.assertIn("laptop", self.run_cli("list-api-tokens", "--user", "ana").output)
        missing = self.run_cli("list-api-tokens", "--user", "nobody")
        self.assertNotEqual(0, missing.exit_code)
        self.assertIn("not found", missing.output)

    def test_list_marks_revoked_and_expired_tokens(self) -> None:
        self.create()
        self.run_cli("create-api-token", "--user", "luis", "--name", "old")
        old, first = db.session.query(ApiToken).order_by(ApiToken.id.desc()).all()
        old.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        self.run_cli("revoke-api-token", first.prefix)
        output = self.run_cli("list-api-tokens").output
        self.assertRegex(output, rf"{first.prefix}.*revoked")
        self.assertRegex(output, rf"{old.prefix}.*expired")

    def test_revoke_invalidates_the_token_and_is_audited(self) -> None:
        token = self.plaintext(self.create())
        prefix = token.split("_", 2)[1]
        with self.assertLogs("security", level="INFO") as logs:
            result = self.run_cli("revoke-api-token", prefix)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("API_TOKEN_REVOKED", "\n".join(logs.output))
        self.assertNotIn(token.split("_", 2)[2], "\n".join(logs.output) + result.output)
        from app.services import api_tokens
        from app.services.errors import AuthenticationFailed

        with self.assertRaises(AuthenticationFailed):
            api_tokens.authenticate(db.session, token, secret_key=SECRET_KEY)
        entry = db.session.query(AuditLog).filter_by(action="update").one()
        self.assertEqual(("api_tokens", "cli"), (entry.entity_type, entry.channel))

    def test_revoke_errors_exit_non_zero(self) -> None:
        prefix = self.plaintext(self.create()).split("_", 2)[1]
        self.assertEqual(0, self.run_cli("revoke-api-token", prefix).exit_code)
        again = self.run_cli("revoke-api-token", prefix)
        self.assertNotEqual(0, again.exit_code)
        self.assertIn("already", again.output)
        unknown = self.run_cli("revoke-api-token", "ffffffff")
        self.assertNotEqual(0, unknown.exit_code)
        self.assertIn("not found", unknown.output)
        self.assertEqual(2, self.run_cli("revoke-api-token").exit_code)


if __name__ == "__main__":
    unittest.main()
