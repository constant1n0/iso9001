"""Quality module screens: auditorías, no conformidades and mejoras."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Auditoria, EstadoNoConformidad, Mejora, NoConformidad, RoleEnum, User,
)


PASSWORD = "StrongPassword123!"
DAY = date(2026, 10, 5)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


class CalidadScreensTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                User(username="admin", email="admin@example.com",
                     password=generate_password_hash(PASSWORD), role=RoleEnum.ADMINISTRADOR),
                Auditoria(area_auditada="Compras", fecha=DAY, auditor="A", resultado="R"),
                NoConformidad(descripcion="Etiqueta ilegible", fecha_detectada=DAY),
                Mejora(no_conformidad="Revisar etiquetado"),
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
            "/auditorias/", "/auditorias/nueva", "/auditorias/editar/1",
            "/no_conformidades/", "/no_conformidades/nueva", "/no_conformidades/editar/1",
            "/no_conformidades/1", "/no_conformidades/1/acciones/nueva",
            "/mejoras/", "/mejoras/nueva", "/mejoras/editar/1",
        ):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertTrue('class="page-header"' in html, "no themed page header")
                self.assertIsNone(INLINE_HANDLER.search(html), "inline JS handler")
                self.assertFalse('border="1"' in html, "legacy table markup")

    def test_empty_improvement_is_rejected_without_server_error(self) -> None:
        response = self.client.post("/mejoras/nueva", data={"no_conformidad": ""})

        self.assertEqual(200, response.status_code)
        self.assertTrue("field--invalid" in response.get_data(as_text=True), "no field error")
        with self.app.app_context():
            self.assertEqual(1, Mejora.query.count())

    def test_improvement_is_created_and_edited_through_the_form(self) -> None:
        response = self.client.post("/mejoras/nueva", data={
            "no_conformidad": "Calibración caducada", "accion_correctiva": "Calibrar",
        })
        self.assertEqual(302, response.status_code)
        response = self.client.post("/mejoras/editar/1", data={
            "no_conformidad": "Etiquetado revisado", "accion_preventiva": "Checklist",
        })
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertEqual(2, Mejora.query.count())
            mejora = db.session.get(Mejora, 1)
            self.assertEqual("Etiquetado revisado", mejora.no_conformidad)
            self.assertEqual("Checklist", mejora.accion_preventiva)

    def test_nonconformity_state_is_shown_but_never_posted(self) -> None:
        html = self.client.get("/no_conformidades/nueva").get_data(as_text=True)
        self.assertIsNone(re.search(r'name="estado"', html), "estado must not be editable")
        self.assertTrue(re.search(r'<select[^>]*name="origen"', html), "origen is not a select")
        self.assertTrue(re.search(r'<select[^>]*name="gravedad"', html), "gravedad is not a select")

        base = {"descripcion": "NC", "fecha_detectada": "2026-10-05"}
        rejected = self.client.post("/no_conformidades/nueva", data=base | {"origen": "Cliente"})
        self.assertEqual(200, rejected.status_code)
        accepted = self.client.post("/no_conformidades/nueva",
                                    data=base | {"estado": "cerrada", "origen": "cliente"})
        self.assertEqual(302, accepted.status_code)
        with self.app.app_context():
            states = sorted(nc.estado.name for nc in NoConformidad.query.all())
        self.assertEqual(["abierta", "abierta"], states)

    def test_state_filter_matches_exactly(self) -> None:
        with self.app.app_context():
            db.session.add(NoConformidad(descripcion="Cancelada por duplicada",
                                         fecha_detectada=DAY,
                                         estado=EstadoNoConformidad.cancelada))
            db.session.commit()

        html = self.client.get("/no_conformidades/?estado=abierta").get_data(as_text=True)

        self.assertIn("Etiqueta ilegible", html)
        self.assertNotIn("Cancelada por duplicada", html)


if __name__ == "__main__":
    unittest.main()
