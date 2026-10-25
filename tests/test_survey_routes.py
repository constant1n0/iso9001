"""Customer satisfaction survey web routes as thin adapters over the service."""

from __future__ import annotations

import unittest
from datetime import date

from register_routes import RegisterRoutesBase

from app.models import RoleEnum, SatisfaccionCliente
from app.extensions import db
from mcp_support import survey_values
from app.services import satisfaction

FORM = {"cliente_id": "1", "tipo_encuesta": "otra", "canal": "otro",
        "fecha_encuesta": "2026-10-05", "puntuacion": "9", "comentarios": ""}
SEED = {"cliente_id": 1, "tipo_encuesta": "otra", "canal": "otro",
        "fecha_encuesta": date(2026, 10, 5), "puntuacion": 9}


class SurveyRoutesTestCase(RegisterRoutesBase):
    BASE = "/satisfaccion_cliente"

    def setUp(self):
        super().setUp()
        with self.app.app_context():
            survey_values("Farmacia Sol")
            self.other_customer = survey_values("Bar Luna")["cliente_id"]
            db.session.commit()

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
        self.login(RoleEnum.ADMINISTRADOR)  # only administrators delete
        self.client.post(f"{self.BASE}/eliminar/{record_id}")
        self.assertIn(("success", "Encuesta eliminada exitosamente"), self.flashes())
        self.assertEqual(["create", "update", "delete"], self.actions())
        self.assertEqual(404, self.client.post(f"{self.BASE}/eliminar/{record_id}").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        for data in (FORM | {"cliente_id": ""}, FORM | {"puntuacion": "11"}, FORM | {"puntuacion": "0"}):
            self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=data).status_code)
        self.assertEqual(0, self.count(SatisfaccionCliente))

    def test_list_filters_ordering_and_the_invalid_score_warning_are_unchanged(self) -> None:
        self.seed_with(satisfaction, SEED | {"cliente_id": self.other_customer, "fecha_encuesta": date(2026, 9, 1), "puntuacion": 4})
        self.seed_with(satisfaction, SEED)
        self.login()

        def get(query=""):
            html = self.client.get(f"{self.BASE}/?{query}").get_data(as_text=True)
            # The customer picker always contains all names; inspect result rows.
            return html.split('<tbody>')[-1].split('</tbody>')[0] if '<tbody>' in html else html

        html = get()
        self.assertLess(html.index("Farmacia Sol"), html.index("Bar Luna"))  # newest first
        self.assertNotIn("Farmacia", get(f"cliente_id={self.other_customer}"))
        self.assertNotIn("Luna", get("puntuacion=5"))
        self.assertIn("Luna", get("puntuacion=4"))
        html = self.client.get(f"{self.BASE}/?puntuacion=abc").get_data(as_text=True)
        self.assertIn("Luna", html)
        self.assertIn("La puntuación debe ser un número entero.", html)


if __name__ == "__main__":
    unittest.main()
