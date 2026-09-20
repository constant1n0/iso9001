"""Integration tests for local administrator bootstrap."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import dotenv
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECURITY_LOG_DIR = PROJECT_ROOT / "logs"
SECURITY_LOG_DIR_EXISTED = SECURITY_LOG_DIR.exists()


def _clean_generated_security_log() -> None:
    """Remove only logging artifacts created by this test module import."""
    if SECURITY_LOG_DIR_EXISTED:
        return

    logger = logging.getLogger("security")
    for handler in list(logger.handlers):
        filename = getattr(handler, "baseFilename", None)
        if filename and Path(filename).is_relative_to(SECURITY_LOG_DIR):
            logger.removeHandler(handler)
            handler.close()
    shutil.rmtree(SECURITY_LOG_DIR, ignore_errors=True)


unittest.addModuleCleanup(_clean_generated_security_log)

ISOLATED_IMPORT_ENV = {
    "DATABASE_URI": "sqlite:///:memory:",
    "FLASK_DEBUG": "False",
    "SECRET_KEY": "test-only-secret",
}

if sys.platform == "darwin":
    native_library_dirs = [
        prefix / formula / "lib"
        for prefix in (Path("/opt/homebrew/opt"), Path("/usr/local/opt"))
        for formula in (
            "cairo",
            "fontconfig",
            "freetype",
            "gdk-pixbuf",
            "glib",
            "harfbuzz",
            "libffi",
            "pango",
        )
        if (prefix / formula / "lib").is_dir()
    ]
    if native_library_dirs:
        ISOLATED_IMPORT_ENV["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(
            str(path) for path in native_library_dirs
        )

with (
    patch.dict(os.environ, ISOLATED_IMPORT_ENV, clear=True),
    patch.object(dotenv, "load_dotenv", return_value=False),
):
    from app import create_app
    from app.config import Config
    from app.extensions import db
    from app.models import RoleEnum, User


BASE_TEST_CONFIG = {
    "CELERY_BROKER_URL": None,
    "MAIL_SUPPRESS_SEND": True,
    "RATELIMIT_ENABLED": False,
    "SECRET_KEY": "test-only-secret",
    "SECURITY_LOG_ENABLED": False,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "TESTING": True,
    "WTF_CSRF_ENABLED": False,
}

VALID_PASSWORD = "StrongPassword123!"


def build_app(**overrides: object):
    """Build an isolated app while remaining usable for the initial RED run."""
    config = BASE_TEST_CONFIG | overrides
    with ExitStack() as stack:
        for key, value in config.items():
            stack.enter_context(patch.object(Config, key, value, create=True))
        return create_app()


class AuthBootstrapTestCase(unittest.TestCase):
    """Exercise bootstrap behavior through real Flask and SQLAlchemy boundaries."""

    def setUp(self) -> None:
        self.app = build_app()
        self.client = self.app.test_client()
        self.runner = self.app.test_cli_runner()
        with self.app.app_context():
            db.create_all()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_public_registration_get_and_post_are_absent(self) -> None:
        get_response = self.client.get("/register")
        post_response = self.client.post(
            "/register",
            data={
                "username": "attacker",
                "email": "attacker@example.com",
                "password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
        )

        self.assertEqual(404, get_response.status_code)
        self.assertEqual(404, post_response.status_code)
        self.assertNotIn("auth.register", self.app.view_functions)
        with self.app.app_context():
            self.assertEqual(0, User.query.count())

    def test_empty_database_can_reach_login(self) -> None:
        response = self.client.get("/login")

        self.assertEqual(200, response.status_code)
        self.assertNotIn(b"/register", response.data)

    def test_create_admin_help_is_available_without_prompts(self) -> None:
        result = self.runner.invoke(args=["create-admin", "--help"])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("Create the initial administrator", result.output)

    def test_create_admin_prompts_and_hashes_password(self) -> None:
        result = self.runner.invoke(
            args=["create-admin"],
            input=(
                "adminuser\n"
                "admin@example.com\n"
                f"{VALID_PASSWORD}\n"
                f"{VALID_PASSWORD}\n"
            ),
        )

        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn(VALID_PASSWORD, result.output)
        with self.app.app_context():
            user = User.query.one()
            self.assertEqual("adminuser", user.username)
            self.assertEqual("admin@example.com", user.email)
            self.assertEqual(RoleEnum.ADMINISTRADOR, user.role)
            self.assertNotEqual(VALID_PASSWORD, user.password)
            self.assertTrue(check_password_hash(user.password, VALID_PASSWORD))

    def test_existing_account_blocks_cli_without_modification(self) -> None:
        existing_hash = generate_password_hash("ExistingPassword123!")
        with self.app.app_context():
            existing = User(
                username="operator",
                email="operator@example.com",
                password=existing_hash,
                role=RoleEnum.OPERATIVO,
            )
            db.session.add(existing)
            db.session.commit()
            existing_id = existing.id

        result = self.runner.invoke(args=["create-admin"])

        self.assertNotEqual(0, result.exit_code)
        self.assertIn("already exists", result.output.lower())
        with self.app.app_context():
            users = User.query.all()
            self.assertEqual(1, len(users))
            self.assertEqual(existing_id, users[0].id)
            self.assertEqual(existing_hash, users[0].password)
            self.assertEqual(RoleEnum.OPERATIVO, users[0].role)

    def test_invalid_cli_inputs_leave_database_empty(self) -> None:
        invalid_inputs = (
            (
                "short username",
                f"abc\nadmin@example.com\n{VALID_PASSWORD}\n{VALID_PASSWORD}\n",
            ),
            (
                "invalid email",
                f"adminuser\nnot-an-email\n{VALID_PASSWORD}\n{VALID_PASSWORD}\n",
            ),
            (
                "password mismatch",
                f"adminuser\nadmin@example.com\n{VALID_PASSWORD}\nDifferent123!\n",
            ),
            (
                "long username",
                f"{'u' * 151}\nadmin@example.com\n{VALID_PASSWORD}\n{VALID_PASSWORD}\n",
            ),
            (
                "long email",
                f"adminuser\n{'a' * 244}@example.com\n{VALID_PASSWORD}\n{VALID_PASSWORD}\n",
            ),
            (
                "short password",
                "adminuser\nadmin@example.com\nshort\nshort\n",
            ),
        )

        for label, cli_input in invalid_inputs:
            with self.subTest(label=label):
                result = self.runner.invoke(args=["create-admin"], input=cli_input)
                self.assertNotEqual(0, result.exit_code)
                self.assertNotIn(VALID_PASSWORD, result.output)
                with self.app.app_context():
                    self.assertEqual(0, User.query.count())

    def test_database_failure_rolls_back_without_sensitive_output(self) -> None:
        with patch.object(
            db.session,
            "commit",
            side_effect=SQLAlchemyError("sensitive database detail"),
        ):
            result = self.runner.invoke(
                args=["create-admin"],
                input=(
                    "adminuser\n"
                    "admin@example.com\n"
                    f"{VALID_PASSWORD}\n"
                    f"{VALID_PASSWORD}\n"
                ),
            )

        self.assertNotEqual(0, result.exit_code)
        self.assertIn("could not be created", result.output.lower())
        self.assertNotIn("sensitive database detail", result.output)
        self.assertNotIn(VALID_PASSWORD, result.output)
        with self.app.app_context():
            self.assertEqual(0, User.query.count())


class AppFactoryAndLoggingTestCase(unittest.TestCase):
    """Verify test configuration and security logging lifecycle."""

    def test_factory_applies_overrides_before_extension_initialization(self) -> None:
        app = create_app(BASE_TEST_CONFIG)

        with app.app_context():
            self.assertEqual("sqlite:///:memory:", str(db.engine.url))
        self.assertTrue(app.config["TESTING"])
        self.assertFalse(app.config["SECURITY_LOG_ENABLED"])

    def test_import_does_not_create_security_log(self) -> None:
        self.assertFalse(
            SECURITY_LOG_DIR.exists(),
            "Importing the application must not create logging artifacts",
        )

    def test_repeated_factories_do_not_duplicate_security_file_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "security.log"
            config = BASE_TEST_CONFIG | {
                "SECURITY_LOG_ENABLED": True,
                "SECURITY_LOG_FILE": str(log_path),
            }

            create_app(config)
            create_app(config)

            matching_handlers = [
                handler
                for handler in logging.getLogger("security").handlers
                if getattr(handler, "baseFilename", None) == str(log_path)
            ]
            self.assertTrue(log_path.exists())
            self.assertEqual(1, len(matching_handlers))
            create_app(BASE_TEST_CONFIG)


if __name__ == "__main__":
    unittest.main()
