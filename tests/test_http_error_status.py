"""HTTP errors without a handler of their own keep their status code."""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap

from app.extensions import db


class HttpErrorStatusTestCase(unittest.TestCase):
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

    def test_a_wrong_method_answers_405_with_the_allowed_methods(self) -> None:
        app = self._app()
        app.add_url_rule("/_post_only", "post_only", lambda: "ok", methods=["POST"])

        with self.assertNoLogs("app.utils.error_handlers", level="ERROR"):
            response = app.test_client().get("/_post_only")

        self.assertEqual(405, response.status_code)
        self.assertIn("POST", response.headers.get("Allow", ""))

    def test_an_exceeded_login_rate_limit_answers_429_and_is_logged(self) -> None:
        app = self._app(RATELIMIT_ENABLED=True)
        client = app.test_client()
        credentials = {"username": "nadie", "password": "incorrecta"}
        for _ in range(5):  # the login allows 5 POSTs per minute
            client.post("/login", data=credentials)

        with self.assertLogs("security", level="WARNING") as logs, \
                self.assertNoLogs("app.utils.error_handlers", level="ERROR"):
            response = client.post("/login", data=credentials)

        self.assertEqual(429, response.status_code)
        self.assertTrue(any("RATE_LIMIT_EXCEEDED" in line for line in logs.output))

    def test_an_unexpected_exception_still_answers_500(self) -> None:
        app = self._app()

        def boom():
            raise RuntimeError("secret detail")

        app.add_url_rule("/_boom", "boom", boom)

        with self.assertLogs("app.utils.error_handlers", level="ERROR"):
            response = app.test_client().get("/_boom")

        self.assertEqual(500, response.status_code)
        self.assertNotIn("secret detail", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
