"""Interested parties web routes as thin adapters over the stakeholders service."""

from __future__ import annotations

import unittest

from register_routes import RegisterRoutesBase

from app.models import ParteInteresada, RoleEnum
from app.services import stakeholders

FORM = {"nombre": "Clientes", "necesidades_expectativas": "", "requisitos_identificados": "",
        "objetivo_estrategico": "Fidelizar"}


class StakeholderRoutesTestCase(RegisterRoutesBase):
    BASE = "/partes_interesadas"

    def test_create_edit_and_delete_are_audited_with_the_same_flashes(self) -> None:
        self.login()
        self.assertEqual(302, self.client.post(f"{self.BASE}/nueva", data=FORM).status_code)
        self.assertIn(("success", "Parte interesada creada exitosamente"), self.flashes())
        with self.app.app_context():
            created = ParteInteresada.query.one()
            self.assertEqual((None, self.ids[RoleEnum.OPERATIVO]), (created.necesidades_expectativas, created.created_by_id))
            record_id = created.id_interesado
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM)
        self.assertEqual(["create"], self.actions())  # unchanged edit writes nothing
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM | {"objetivo_estrategico": "Crecer"})
        self.assertIn(("success", "Parte interesada actualizada exitosamente"), self.flashes())
        self.login(RoleEnum.ADMINISTRADOR)  # only administrators delete
        self.client.post(f"{self.BASE}/eliminar/{record_id}")
        self.assertIn(("success", "Parte interesada eliminada correctamente"), self.flashes())
        self.assertEqual(["create", "update", "delete"], self.actions())
        self.assertEqual(404, self.client.get(f"{self.BASE}/editar/{record_id}").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=FORM | {"nombre": ""}).status_code)
        self.assertEqual(0, self.count(ParteInteresada))

    def test_a_duplicate_name_flashes_the_conflict_and_returns_to_the_form(self) -> None:
        self.seed_with(stakeholders, {"nombre": "Clientes"})
        other = self.seed_with(stakeholders, {"nombre": "Bancos"})
        self.login()
        for url, data in ((f"{self.BASE}/nueva", FORM), (f"{self.BASE}/editar/{other}", FORM)):
            with self.subTest(url=url):
                response = self.client.post(url, data=data, headers={"Referer": f"http://localhost{url}"})
                self.assertEqual((302, url), (response.status_code, response.headers["Location"]))
                self.assertIn(("danger", "Ya existe una parte interesada con ese nombre."), self.flashes())
        self.assertEqual(2, self.count(ParteInteresada))

    def test_list_is_ordered_by_name(self) -> None:
        self.seed_with(stakeholders, {"nombre": "Zapaterias"})
        self.seed_with(stakeholders, {"nombre": "Aseguradoras"})
        self.login()
        html = self.client.get(f"{self.BASE}/").get_data(as_text=True)
        self.assertLess(html.index("Aseguradoras"), html.index("Zapaterias"))


if __name__ == "__main__":
    unittest.main()
