"""Domain errors map to HTTP responses and are never masked as a 500."""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import RoleEnum, User

PASSWORD = "StrongPassword123!"


class DomainErrorHandlersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import errors

        self.errors = errors
        self.app = bootstrap.build_app()
        self.raising = errors.NotFound("x")

        @self.app.route("/_test/raise/<kind>", methods=["GET", "POST"])
        def _raise(kind):
            raise {
                "not_found": errors.NotFound("Falta el registro."),
                "conflict": errors.Conflict("Registro duplicado."),
                "denied": errors.PermissionDenied("Sin permiso."),
                "invalid": errors.ValidationError("Dato no válido."),
                "boom": RuntimeError("secret internals"),
            }[kind]

        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(
                User(
                    username="operativo",
                    email="operativo@example.com",
                    password=generate_password_hash(PASSWORD),
                    role=RoleEnum.OPERATIVO,
                )
            )
            db.session.commit()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self) -> None:
        self.client.post("/login", data={"username": "operativo", "password": PASSWORD})

    def test_json_requests_get_status_and_safe_message(self) -> None:
        expected = {
            "not_found": (404, "Falta el registro."),
            "conflict": (409, "Registro duplicado."),
            "denied": (403, "Sin permiso."),
            "invalid": (422, "Dato no válido."),
        }
        for kind, (status, message) in expected.items():
            with self.subTest(kind=kind):
                response = self.client.post(f"/_test/raise/{kind}", json={})
                self.assertEqual(status, response.status_code)
                self.assertEqual({"error": message}, response.get_json())

    def test_accept_json_is_treated_as_a_json_request(self) -> None:
        response = self.client.get(
            "/_test/raise/conflict", headers={"Accept": "application/json"}
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual({"error": "Registro duplicado."}, response.get_json())

    def test_html_permission_denied_flashes_and_redirects_to_dashboard(self) -> None:
        self._login()
        response = self.client.get("/_test/raise/denied")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
        with self.client.session_transaction() as session:
            self.assertIn(
                ("danger", "No tienes permiso para acceder a esta página."),
                session["_flashes"],
            )

    def test_unrelated_exceptions_still_use_the_global_handler(self) -> None:
        response = self.client.post("/_test/raise/boom", json={})
        self.assertEqual(500, response.status_code)
        self.assertNotIn("secret internals", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
