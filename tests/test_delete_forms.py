"""Deletion forms must work with CSRF protection enabled, as in production."""

from __future__ import annotations

import unittest
from datetime import date
from html.parser import HTMLParser

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Auditoria,
    Capacitacion,
    Mejora,
    NoConformidad,
    ParteInteresada,
    RoleEnum,
    SatisfaccionCliente,
    User,
)


PASSWORD = "StrongPassword123!"
DAY = date(2026, 10, 5)

# (list URL, model, factory)
MODULES = (
    ("/auditorias/", Auditoria,
     lambda: Auditoria(area_auditada="Compras", fecha=DAY, auditor="A", resultado="R")),
    ("/no_conformidades/", NoConformidad,
     lambda: NoConformidad(descripcion="NC", fecha_detectada=DAY)),
    ("/capacitaciones/", Capacitacion,
     lambda: Capacitacion(tema="ISO", fecha=DAY, personal="Equipo")),
    ("/satisfaccion_cliente/", SatisfaccionCliente,
     lambda: SatisfaccionCliente(fecha_encuesta=DAY, cliente="C", puntuacion=8)),
    ("/mejoras/", Mejora, lambda: Mejora(no_conformidad="NC")),
    ("/partes_interesadas/", ParteInteresada, lambda: ParteInteresada(nombre="Cliente")),
)  # documents are withdrawn, never deleted (DC6 of document-control)


class _PostForms(HTMLParser):
    """Collect POST forms and the inputs a browser would submit."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("method", "get").lower() == "post":
            self._current = {"action": attrs.get("action"), "fields": {}, "text": ""}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and attrs.get("name"):
            self._current["fields"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data


class DeleteFormsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app(WTF_CSRF_ENABLED=True)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(
                User(
                    username="admin",
                    email="admin@example.com",
                    password=generate_password_hash(PASSWORD),
                    role=RoleEnum.ADMINISTRADOR,
                )
            )
            for _, _, factory in MODULES:
                db.session.add(factory())
            db.session.commit()
        self._login()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self) -> None:
        page = _PostForms()
        page.feed(self.client.get("/login").get_data(as_text=True))
        fields = dict(page.forms[0]["fields"], username="admin", password=PASSWORD)
        response = self.client.post("/login", data=fields)
        self.assertEqual(302, response.status_code)

    def test_every_delete_form_submits_a_valid_csrf_token(self) -> None:
        for url, model, _ in MODULES:
            with self.subTest(url=url):
                page = _PostForms()
                page.feed(self.client.get(url).get_data(as_text=True))
                # Each list shows one record, so its only POST form deletes it.
                (form,) = page.forms

                self.assertTrue(form["fields"].get("csrf_token"), "hidden csrf_token missing")
                self.assertNotIn(form["fields"]["csrf_token"], form["text"],
                                 "token is printed as visible text")

                response = self.client.post(form["action"], data=form["fields"])

                self.assertEqual(302, response.status_code)
                with self.app.app_context():
                    self.assertEqual(0, model.query.count())


if __name__ == "__main__":
    unittest.main()
