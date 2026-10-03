"""Improvement HTML routes as thin adapters; the JSON API is left to QF-8."""

from __future__ import annotations

import unittest

from register_routes import RegisterRoutesBase

from app.models import Mejora, RoleEnum
from app.services import improvements

FORM = {"no_conformidad": "Etiqueta ilegible", "accion_correctiva": "", "accion_preventiva": "Mantenimiento"}


class ImprovementRoutesTestCase(RegisterRoutesBase):
    BASE = "/mejoras"

    def test_create_edit_and_delete_are_audited_with_the_same_flashes(self) -> None:
        self.login()
        self.assertEqual(302, self.client.post(f"{self.BASE}/nueva", data=FORM).status_code)
        self.assertIn(("success", "Mejora registrada exitosamente"), self.flashes())
        with self.app.app_context():
            created = Mejora.query.one()
            self.assertEqual((None, self.ids[RoleEnum.OPERATIVO]), (created.accion_correctiva, created.created_by_id))
            self.assertIsNotNone(created.fecha_implementacion)
            record_id = created.id_mejora
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM)
        self.assertEqual(["create"], self.actions())  # unchanged edit writes nothing
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM | {"accion_correctiva": "Cambiar"})
        self.assertIn(("success", "Mejora actualizada exitosamente"), self.flashes())
        self.login(RoleEnum.ADMINISTRADOR)  # only administrators delete
        self.client.post(f"{self.BASE}/eliminar/{record_id}")
        self.assertIn(("success", "Mejora eliminada correctamente"), self.flashes())
        self.assertEqual(["create", "update", "delete"], self.actions())
        self.assertEqual(404, self.client.post(f"{self.BASE}/eliminar/{record_id}").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=FORM | {"no_conformidad": ""}).status_code)
        self.assertEqual(0, self.count(Mejora))

    def test_pagination_keeps_page_and_size_arguments(self) -> None:
        for n in range(12):
            self.seed_with(improvements, {"no_conformidad": f"Hallazgo{n:02d}"})
        self.login()

        def get(query=""):
            return self.client.get(f"{self.BASE}/?{query}").get_data(as_text=True)

        first, second = get(), get("page=2")
        self.assertIn("Hallazgo09", first)
        self.assertNotIn("Hallazgo10", first)
        self.assertIn("Página 1 de 2", first)
        self.assertIn("Hallazgo11", second)
        self.assertIn("Página 2 de 2", second)
        self.assertIn("Hallazgo04", get("per_page=5&page=1"))
        self.assertNotIn("Hallazgo05", get("per_page=5&page=1"))
        self.assertNotIn("Hallazgo00", get("page=99"))  # out of range: empty page, no error
        self.assertIn("Hallazgo00", get("page=0"))  # below 1 behaves as page 1


if __name__ == "__main__":
    unittest.main()
