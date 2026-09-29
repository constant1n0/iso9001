"""UI phase 2 closing: landing page, Spanish validation, strict CSP, ordering."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Capacitacion, NoConformidad, RoleEnum, SatisfaccionCliente, User


PASSWORD = "StrongPassword123!"
TEMPLATES = bootstrap.PROJECT_ROOT / "app" / "templates"
# Rendered by WeasyPrint into PDFs, never served to a browser.
PDF_ONLY = {"pdf_template.html", "reporte_mensual.html"}


class BrowserTemplatesTestCase(unittest.TestCase):
    """The strict CSP only holds if no template reintroduces inline code."""

    def test_no_inline_scripts_handlers_or_styles(self) -> None:
        for template in sorted(TEMPLATES.rglob("*.html")):
            if template.name in PDF_ONLY:
                continue
            source = template.read_text()
            with self.subTest(template=str(template.relative_to(TEMPLATES))):
                self.assertIsNone(re.search(r"\son[a-z]+\s*=", source), "inline handler")
                self.assertIsNone(re.search(r"\sstyle\s*=", source), "inline style attribute")
                self.assertIsNone(re.search(r"<style[\s>]", source), "inline <style> block")
                for tag in re.findall(r"<script\b[^>]*>", source):
                    self.assertTrue(
                        "src=" in tag or 'type="application/json"' in tag,
                        f"inline executable script: {tag}",
                    )


class UiClosingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(User(username="admin", email="admin@example.com",
                                password=generate_password_hash(PASSWORD),
                                role=RoleEnum.ADMINISTRADOR))
            for day in (date(2026, 3, 1), date(2026, 9, 1), date(2026, 6, 1)):
                db.session.add(NoConformidad(descripcion=f"NC {day}", fecha_detectada=day))
                db.session.add(Capacitacion(tema=f"Tema {day}", fecha=day, personal="P"))
                db.session.add(SatisfaccionCliente(fecha_encuesta=day, cliente=f"C {day}",
                                                   puntuacion=8))
            db.session.commit()
        self.client.post("/login", data={"username": "admin", "password": PASSWORD})

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_home_redirects_to_the_dashboard(self) -> None:
        response = self.client.get("/")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith("/dashboard/"))

    def test_validation_messages_are_in_spanish(self) -> None:
        html = self.client.post("/capacitaciones/nueva", data={
            "tema": "", "fecha": "2026-10-10", "personal": "Equipo", "duracion_horas": "muchas",
        }).get_data(as_text=True)
        self.assertIn("Este campo es obligatorio.", html)
        self.assertIn("No es un valor entero válido.", html)
        self.assertNotIn("This field is required", html)

    def test_content_security_policy_has_no_unsafe_inline(self) -> None:
        policy = self.client.get("/login").headers["Content-Security-Policy"]
        self.assertIn("script-src 'self';", policy)
        self.assertIn("style-src 'self';", policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_dated_lists_show_the_most_recent_first(self) -> None:
        expected = ["2026-09-01", "2026-06-01", "2026-03-01"]
        for path, prefix in (
            ("/no_conformidades/", "NC "),
            ("/capacitaciones/", "Tema "),
            ("/satisfaccion_cliente/", "C "),
        ):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                cells = re.findall(r"<td>" + re.escape(prefix) + r"(\d{4}-\d{2}-\d{2})", html)
                self.assertEqual(expected, cells)


if __name__ == "__main__":
    unittest.main()
