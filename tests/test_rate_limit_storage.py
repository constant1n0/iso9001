"""Rate-limit counters live in the configured storage; only targeted limits apply."""

from __future__ import annotations

import importlib.util
import os
import unittest
from unittest.mock import patch

import dotenv
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from limits.storage import MemoryStorage, RedisStorage
from werkzeug.security import generate_password_hash

import test_auth_bootstrap as bootstrap

from app.config import Config
from app.extensions import db, limiter
from app.models import RoleEnum, User


CONFIG_PATH = bootstrap.PROJECT_ROOT / "app" / "config.py"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"
CREDENTIALS = {"username": "nadie", "password": "incorrecta"}


def _load_config(environ: dict[str, str]) -> type:
    """``Config`` from a private copy of ``app/config.py`` read under ``environ``.

    The imported ``app.config`` module, which ``create_app`` uses, is untouched.
    """
    spec = importlib.util.spec_from_file_location("_config_under_test", CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    with (
        patch.dict(os.environ, environ, clear=True),
        patch.object(dotenv, "load_dotenv", return_value=False),
    ):
        spec.loader.exec_module(module)
    return module.Config


class RateLimitConfigTestCase(unittest.TestCase):
    def test_defaults_count_in_memory_under_the_application_prefix(self) -> None:
        config = _load_config({})

        self.assertEqual("memory://", config.RATELIMIT_STORAGE_URI)
        self.assertEqual("iso9001", config.RATELIMIT_KEY_PREFIX)
        self.assertIs(True, config.RATELIMIT_IN_MEMORY_FALLBACK_ENABLED)

    def test_the_storage_uri_comes_from_the_environment(self) -> None:
        config = _load_config({"RATELIMIT_STORAGE_URI": "redis://localhost:6379/2"})

        self.assertEqual("redis://localhost:6379/2", config.RATELIMIT_STORAGE_URI)

    def test_an_empty_storage_uri_falls_back_to_memory(self) -> None:
        config = _load_config({"RATELIMIT_STORAGE_URI": "  "})

        self.assertEqual("memory://", config.RATELIMIT_STORAGE_URI)


class RateLimitWiringTestCase(unittest.TestCase):
    def _app(self, **overrides):
        app = bootstrap.build_app(**overrides)
        with app.app_context():
            db.create_all()
        self.addCleanup(self._drop, app)
        return app

    @staticmethod
    def _drop(app) -> None:
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_the_configured_storage_uri_reaches_the_limiter(self) -> None:
        # Building the storage does not connect, so no Redis server is needed.
        # A disabled limiter skips its set-up, so the storage is only built here.
        self._app(RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI=UNREACHABLE_REDIS)

        self.assertIsInstance(limiter.storage, RedisStorage)

    def test_counters_are_stored_under_the_application_prefix(self) -> None:
        app = self._app(RATELIMIT_ENABLED=True)

        app.test_client().post("/login", data=CREDENTIALS)

        self.assertIsInstance(limiter.storage, MemoryStorage)
        keys = list(limiter.storage.storage)
        self.assertTrue(keys)
        self.assertTrue(all(k.startswith("LIMITER/iso9001/127.0.0.1/") for k in keys))

    def test_ordinary_pages_are_never_limited(self) -> None:
        app = self._app(RATELIMIT_ENABLED=True)
        with app.app_context():
            user = User(
                username="operativo",
                email="operativo@example.com",
                password=generate_password_hash(bootstrap.VALID_PASSWORD),
                role=RoleEnum.OPERATIVO,
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

        # The removed blanket limit allowed 50 requests per hour.
        codes = {client.get("/perfil/").status_code for _ in range(75)}

        self.assertEqual({200}, codes)

    def test_the_login_limit_still_refuses_the_sixth_post(self) -> None:
        app = self._app(RATELIMIT_ENABLED=True)
        client = app.test_client()

        codes = [client.post("/login", data=CREDENTIALS).status_code for _ in range(6)]

        self.assertEqual([302] * 5 + [429], codes)


class InMemoryFallbackTestCase(unittest.TestCase):
    """The application settings keep limits working while the storage is down.

    A private ``Limiter`` is used so the shared one never records a dead
    storage, which would leak into later tests.
    """

    def test_an_unreachable_storage_falls_back_to_memory(self) -> None:
        app = Flask(__name__)
        app.config.from_object(Config)
        app.config.update(
            RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI=UNREACHABLE_REDIS
        )
        probe = Limiter(get_remote_address, app=app)
        view = probe.limit("2 per minute")(lambda: "ok")
        app.add_url_rule("/probe", "probe", view, methods=["POST"])
        client = app.test_client()

        with self.assertLogs("flask-limiter", level="WARNING") as logs:
            codes = [client.post("/probe").status_code for _ in range(3)]

        self.assertEqual([200, 200, 429], codes)
        self.assertTrue(
            any("falling back to in-memory" in line for line in logs.output)
        )


if __name__ == "__main__":
    unittest.main()
