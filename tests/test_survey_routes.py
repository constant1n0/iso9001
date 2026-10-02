"""Customer satisfaction survey web routes as thin adapters over the service."""

from __future__ import annotations

import unittest
from datetime import date

from register_routes import RegisterRoutesBase

from app.models import RoleEnum, SatisfaccionCliente
from app.services import satisfaction

FORM = {"cliente": "Farmacia Sol", "fecha_encuesta": "2026-10-05", "puntuacion": "9", "comentarios": ""}
SEED = {"cliente": "Farmacia Sol", "fecha_encuesta": date(2026, 10, 5), "puntuacion": 9}


class SurveyRoutesTestCase(RegisterRoutesBase):
    BASE = "/satisfaccion_cliente"

    def test_create_edit_and_delete_are_audited_with_the_same_flashes(self) -> None:
        self.login()
        response = self.client.post(f"{self.BASE}/nueva", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Encuesta de satisfacción registrada exitosamente"), self.flashes())
        with self.app.app_context():
            created = SatisfaccionCliente.query.one()
            self.assertEqual((None, self.ids[RoleEnum.OPERATIVO]), (created.comentarios, created.created_by_id))
            record_id = created.id
        self.assertEqual(302, self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM).status_code)
        self.assertEqual(["create"], self.actions())  # unchanged edit writes nothing
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM | {"puntuacion": "7"})
        self.assertIn(("success", "Encuesta actualizada exitosamente"), self.flashes())
        self.client.post(f"{self.BASE}/eliminar/{record_id}")
        self.assertIn(("success", "Encuesta eliminada exitosamente"), self.flashes())
        self.assertEqual(["create", "update", "delete"], self.actions())
        self.assertEqual(404, self.client.post(f"{self.BASE}/eliminar/{record_id}").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        for data in (FORM | {"cliente": ""}, FORM | {"puntuacion": "11"}, FORM | {"puntuacion": "0"}):
            self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=data).status_code)
        self.assertEqual(0, self.count(SatisfaccionCliente))

    def test_list_filters_ordering_and_the_invalid_score_warning_are_unchanged(self) -> None:
        self.seed_with(satisfaction, SEED | {"cliente": "Bar Luna", "fecha_encuesta": date(2026, 9, 1), "puntuacion": 4})
        self.seed_with(satisfaction, SEED)
        self.login()

        def get(query=""):
            return self.client.get(f"{self.BASE}/?{query}").get_data(as_text=True)

        html = get()
        self.assertLess(html.index("Farmacia Sol"), html.index("Bar Luna"))  # newest first
        self.assertNotIn("Farmacia", get("cliente=luna"))
        self.assertNotIn("Luna", get("puntuacion=5"))
        self.assertIn("Luna", get("puntuacion=4"))
        html = get("puntuacion=abc")  # flashed messages are rendered by the page
        self.assertIn("Luna", html)
        self.assertIn("La puntuación debe ser un número entero.", html)


if __name__ == "__main__":
    unittest.main()
