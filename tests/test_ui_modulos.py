"""Customer, people and documentation screens."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Capacitacion,
    Document,
    DocumentCategory,
    ParteInteresada,
    RoleEnum,
    SatisfaccionCliente,
    User,
)


PASSWORD = "StrongPassword123!"
DAY = date(2026, 10, 5)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


class ModuleScreensTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                User(username="admin", email="admin@example.com",
                     password=generate_password_hash(PASSWORD), role=RoleEnum.ADMINISTRADOR),
                SatisfaccionCliente(fecha_encuesta=DAY, cliente="Farmacia Sol", puntuacion=8),
                ParteInteresada(nombre="Proveedores"),
                Capacitacion(tema="ISO", fecha=DAY, personal="Equipo", duracion_horas=4),
                Document(title="Control de registros", code="PO-04",
                         category=DocumentCategory.PROCEDIMIENTO_OPERATIVO,
                         version="2.1", issued_date=DAY, content="Texto"),
            ])
            db.session.commit()
        response = self.client.post("/login", data={"username": "admin", "password": PASSWORD})
        self.assertEqual(302, response.status_code)

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_screens_use_the_theme_without_inline_handlers(self) -> None:
        for path in (
            "/satisfaccion_cliente/", "/satisfaccion_cliente/nueva", "/satisfaccion_cliente/editar/1",
            "/partes_interesadas/", "/partes_interesadas/nueva", "/partes_interesadas/editar/1",
            "/capacitaciones/", "/capacitaciones/nueva", "/capacitaciones/editar/1",
            "/documents/", "/documents/new", "/documents/edit/1",
        ):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertTrue('class="page-header"' in html, "no themed page header")
                self.assertIsNone(INLINE_HANDLER.search(html), "inline JS handler")
                self.assertFalse('border="1"' in html, "legacy table markup")

    def test_document_category_is_shown_by_name_and_kept_when_editing(self) -> None:
        listing = self.client.get("/documents/").get_data(as_text=True)
        self.assertIn("Procedimiento Operativo", listing)
        self.assertNotIn("DocumentCategory.", listing)

        edit = self.client.get("/documents/edit/1").get_data(as_text=True)
        self.assertTrue(
            re.search(r'<option selected value="PROCEDIMIENTO_OPERATIVO">', edit),
            "current category is not preselected",
        )

    def test_training_duration_is_optional(self) -> None:
        response = self.client.post("/capacitaciones/nueva", data={
            "tema": "Acogida", "fecha": "2026-10-10", "personal": "Nuevas incorporaciones",
            "duracion_horas": "", "evaluacion_final": "",
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            nueva = Capacitacion.query.filter_by(tema="Acogida").one()
            self.assertIsNone(nueva.duracion_horas)


if __name__ == "__main__":
    unittest.main()
