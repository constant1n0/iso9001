"""Training web routes as thin adapters over the training service."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from register_routes import RegisterRoutesBase

from app.extensions import db
from app.models import AuditLog, Capacitacion, RoleEnum
from app.services import errors, training

FORM = {"tema": "Seguridad", "fecha": "2026-10-05", "personal": "Ana",
        "duracion_horas": "4", "evaluacion_final": ""}
SEED = {"tema": "Seguridad", "fecha": date(2026, 10, 5), "personal": "Ana", "duracion_horas": 4}


class TrainingRoutesTestCase(RegisterRoutesBase):
    BASE = "/capacitaciones"

    def test_create_is_audited_stamped_and_redirects_with_the_same_flash(self) -> None:
        self.login(RoleEnum.OPERATIVO)  # every role may write until QF-9
        response = self.client.post(f"{self.BASE}/nueva", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{self.BASE}/"))
        self.assertIn(("success", "Capacitación registrada exitosamente"), self.flashes())
        with self.app.app_context():
            record = Capacitacion.query.one()
            self.assertEqual((4, None), (record.duracion_horas, record.evaluacion_final))
            self.assertEqual(self.ids[RoleEnum.OPERATIVO], record.created_by_id)
            row = AuditLog.query.one()
            self.assertEqual(("create", "capacitaciones", "web"), (row.action, row.entity_type, row.channel))

    def test_edit_audits_the_change_and_an_unchanged_edit_writes_nothing(self) -> None:
        record_id = self.seed_with(training, SEED)
        self.login()
        self.assertEqual(302, self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM).status_code)
        self.assertEqual(["create"], self.actions())
        response = self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM | {"tema": "Calidad"})
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Capacitación actualizada exitosamente"), self.flashes())
        self.assertEqual(["create", "update"], self.actions())
        self.assertEqual(self.ids[RoleEnum.OPERATIVO], self.fetch(Capacitacion, record_id).updated_by_id)

    def test_delete_is_audited_and_missing_records_answer_404(self) -> None:
        record_id = self.seed_with(training, SEED)
        self.login(RoleEnum.ADMINISTRADOR)  # only administrators delete
        self.assertEqual(302, self.client.post(f"{self.BASE}/eliminar/{record_id}").status_code)
        self.assertIn(("success", "Capacitación eliminada exitosamente"), self.flashes())
        self.assertEqual(0, self.count(Capacitacion))
        self.assertEqual(["create", "delete"], self.actions())
        for method, url in (("get", "/editar/999"), ("post", "/editar/999"),
                            ("post", "/eliminar/999"), ("get", "/exportar_pdf/999")):
            with self.subTest(url=url):
                self.assertEqual(404, getattr(self.client, method)(self.BASE + url).status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        for data in (FORM | {"tema": ""}, FORM | {"duracion_horas": "-1"}):
            self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=data).status_code)
        self.assertEqual(0, self.count(Capacitacion))

    def test_service_validation_error_shows_the_form_again_with_the_input(self) -> None:
        record_id = self.seed_with(training, SEED)
        self.login()
        for url, target in (("/nueva", "create"), (f"/editar/{record_id}", "update")):
            with self.subTest(url=url):
                with patch(f"app.services.training.{target}",
                           side_effect=errors.ValidationError("Dato rechazado.")):
                    response = self.client.post(self.BASE + url,
                                                data=FORM | {"tema": "Tema conservado"})
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn("Dato rechazado.", html)
                self.assertIn('value="Tema conservado"', html)
        self.assertEqual(["create"], self.actions())

    def test_list_filters_and_ordering_are_unchanged(self) -> None:
        self.seed_with(training, SEED | {"tema": "Curso viejo", "fecha": date(2026, 9, 1), "personal": "Eva"})
        self.seed_with(training, SEED | {"tema": "Curso nuevo"})
        self.login()

        def get(query=""):
            return self.client.get(f"{self.BASE}/?{query}").get_data(as_text=True)

        html = get()
        self.assertLess(html.index("Curso nuevo"), html.index("Curso viejo"))  # newest first
        self.assertNotIn("Curso nuevo", get("tema=VIEJ"))
        self.assertNotIn("Curso viejo", get("personal=ana"))
        self.assertNotIn("Curso nuevo", get("fecha=2026-09-01"))
        self.assertIn("Curso viejo", get("fecha=2026-09-01"))  # a record matches its own date
        self.assertIn("Curso nuevo", get("fecha=no-es-fecha"))  # invalid date ignored


if __name__ == "__main__":
    unittest.main()
