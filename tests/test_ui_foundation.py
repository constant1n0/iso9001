"""Tests for the shared layout, static assets and dashboard data."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    EstadoNoConformidad, NoConformidad, RoleEnum, SatisfaccionCliente, User,
)


PASSWORD = "StrongPassword123!"
DATA_SCRIPT = re.compile(
    r'<script type="application/json" id="dashboard-data">(.*?)</script>', re.S
)


STATIC_DIR = bootstrap.PROJECT_ROOT / "app" / "static"


class StaticAssetsTrackedTestCase(unittest.TestCase):
    """Every static file referenced by templates or CSS must be committed.

    A file can exist locally but be excluded by .gitignore (this happened
    with a generic ``vendor/`` rule), which breaks it only after deploy.
    """

    def test_referenced_static_files_are_tracked_by_git(self) -> None:
        tracked = set(
            subprocess.run(
                ["git", "ls-files", "app/static"],
                cwd=bootstrap.PROJECT_ROOT, capture_output=True, text=True, check=True,
            ).stdout.split()
        )
        referenced = set()
        for template in (bootstrap.PROJECT_ROOT / "app" / "templates").rglob("*.html"):
            for name in re.findall(
                r"url_for\('static',\s*filename='([^']+)'\)", template.read_text()
            ):
                referenced.add(f"app/static/{name}")
        for css in STATIC_DIR.rglob("*.css"):
            for name in re.findall(r'url\("\.\./([^"]+)"\)', css.read_text()):
                referenced.add(f"app/static/{name}")

        self.assertGreater(len(referenced), 5)
        self.assertEqual(set(), referenced - tracked)


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
            "/static/lib/chart.umd.min.js",
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
            for estado in ("abierta", "abierta", "cerrada", "accion_planificada",
                           "en_verificacion", "cancelada"):
                db.session.add(
                    NoConformidad(
                        descripcion=f"NC {estado}", fecha_detectada=date(2026, 9, 1),
                        estado=EstadoNoConformidad[estado],
                    )
                )
            for day, score in ((date(2026, 8, 3), 6), (date(2026, 8, 20), 8),
                               (date(2026, 9, 1), 9)):
                db.session.add(
                    SatisfaccionCliente(fecha_encuesta=day, cliente="C", puntuacion=score)
                )
            db.session.commit()
        self._login("admin")

        # Pin "today" so the 12-month chart window always contains the seeded surveys.
        with patch("app.routes.dashboard_routes.local_today", return_value=date(2026, 9, 15)):
            html = self.client.get("/dashboard/").get_data(as_text=True)

        match = DATA_SCRIPT.search(html)
        self.assertIsNotNone(match, "dashboard data script is missing")
        data = json.loads(match.group(1))
        # Open means neither closed nor cancelled; a cancelled one is not "closed" either.
        self.assertEqual({"abiertas": 4, "cerradas": 1}, data["no_conformidades"])
        self.assertIn("4<span class=\"kpi__unit\">/ 6</span>", html)
        self.assertIn("NC accion_planificada", html)  # pending list holds every open state
        self.assertNotIn("NC cancelada", html)
        self.assertEqual(["2026-08", "2026-09"], data["satisfaccion"]["meses"])
        self.assertEqual([7.0, 9.0], data["satisfaccion"]["promedios"])
        self.assertIn("1 cerrada<", html)
        self.assertIn("/static/lib/chart.umd.min.js", html)
        self.assertIn("/static/js/dashboard.js", html)
        self.assertNotIn("cdn.jsdelivr.net", html)

    def test_footer_credits_the_author_with_the_current_year(self) -> None:
        from app.audit_notifications import local_today

        self._login("admin")
        with self.app.app_context():
            year = local_today().year
        html = self.client.get("/dashboard/").get_data(as_text=True)
        footer = re.search(r'<footer class="site-footer">(.*?)</footer>', html, re.S).group(1)

        self.assertIn(f"&copy; {year} ", footer)
        self.assertRegex(
            footer,
            r'<a href="https://github.com/constant1n0" rel="author noopener" '
            r'target="_blank">constant1n0</a>',
        )
        self.assertNotIn("Dámaso", footer)

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
