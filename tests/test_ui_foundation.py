"""Tests for the shared layout, static assets and dashboard data."""

from __future__ import annotations

import json
import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import NoConformidad, RoleEnum, SatisfaccionCliente, User


PASSWORD = "StrongPassword123!"
DATA_SCRIPT = re.compile(
    r'<script type="application/json" id="dashboard-data">(.*?)</script>', re.S
)


class UiFoundationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            for username, role in (
                ("admin", RoleEnum.ADMINISTRADOR),
                ("operario", RoleEnum.OPERATIVO),
            ):
                db.session.add(
                    User(
                        username=username,
                        email=f"{username}@example.com",
                        password=generate_password_hash(PASSWORD),
                        role=role,
                    )
                )
            db.session.commit()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, username: str) -> None:
        response = self.client.post(
            "/login", data={"username": username, "password": PASSWORD}
        )
        self.assertEqual(302, response.status_code)

    def test_stylesheet_fonts_and_chart_library_are_served_locally(self) -> None:
        for path in (
            "/static/css/app.css",
            "/static/js/dashboard.js",
            "/static/vendor/chart.umd.min.js",
            "/static/fonts/barlow-condensed-latin-600.woff2",
            "/static/fonts/ibm-plex-sans-latin-400.woff2",
            "/static/fonts/ibm-plex-mono-latin-400.woff2",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                response.close()

    def test_login_page_uses_the_stylesheet_and_no_cdn(self) -> None:
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn("/static/css/app.css", html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn('autocomplete="current-password"', html)

    def test_content_security_policy_allows_no_external_sources(self) -> None:
        policy = self.client.get("/login").headers["Content-Security-Policy"]
        self.assertNotIn("https:", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("font-src 'self';", policy)

    def test_dashboard_embeds_chart_data_as_json_arrays(self) -> None:
        with self.app.app_context():
            for estado in ("Abierta", "Abierta", "Cerrada"):
                db.session.add(
                    NoConformidad(
                        descripcion="NC", fecha_detectada=date(2026, 9, 1), estado=estado
                    )
                )
            for day, score in ((date(2026, 8, 3), 6), (date(2026, 8, 20), 8),
                               (date(2026, 9, 1), 9)):
                db.session.add(
                    SatisfaccionCliente(fecha_encuesta=day, cliente="C", puntuacion=score)
                )
            db.session.commit()
        self._login("admin")

        html = self.client.get("/dashboard/").get_data(as_text=True)

        match = DATA_SCRIPT.search(html)
        self.assertIsNotNone(match, "dashboard data script is missing")
        data = json.loads(match.group(1))
        self.assertEqual({"abiertas": 2, "cerradas": 1}, data["no_conformidades"])
        self.assertEqual([8, 9], data["satisfaccion"]["meses"])
        self.assertEqual([7.0, 9.0], data["satisfaccion"]["promedios"])
        self.assertIn("/static/vendor/chart.umd.min.js", html)
        self.assertIn("/static/js/dashboard.js", html)
        self.assertNotIn("cdn.jsdelivr.net", html)

    def test_navigation_shows_only_modules_the_role_can_open(self) -> None:
        self._login("operario")
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertIn('href="/no_conformidades/"', html)
        self.assertIn('href="/capacitaciones/"', html)
        self.assertNotIn('href="/auditorias/"', html)
        self.assertNotIn('href="/documents/"', html)
        self.assertRegex(html, r'href="/dashboard/"\s+aria-current="page"')

        self.client.get("/logout")
        self._login("admin")
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertIn('href="/auditorias/"', html)
        self.assertIn('href="/documents/"', html)


if __name__ == "__main__":
    unittest.main()
