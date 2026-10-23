"""Interested parties web routes as thin adapters over the stakeholders service."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

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

    def test_writes_pass_the_local_date_and_a_json_body_gets_a_normal_result(self) -> None:
        """OC-1 review: a write that sets the next review needs the adapter's date."""
        today = date(2026, 10, 22)
        self.login()
        with (patch("app.routes.parte_interesada_routes.local_today", return_value=today),
              patch.object(stakeholders, "create", wraps=stakeholders.create) as create,
              patch.object(stakeholders, "update", wraps=stakeholders.update) as update):
            self.assertEqual(302, self.client.post(f"{self.BASE}/nueva", data=FORM).status_code)
            with self.app.app_context():
                record_id = ParteInteresada.query.one().id_interesado
            # A JSON client posting to the form goes through the same validation.
            edited = self.client.post(f"{self.BASE}/editar/{record_id}", json=FORM | {
                "objetivo_estrategico": "Crecer", "fecha_revision": "2026-01-01"})
            self.assertEqual(302, edited.status_code)
        self.assertEqual(today, create.call_args.kwargs["today"])
        self.assertEqual(today, update.call_args.kwargs["today"])
        self.assertEqual("Crecer", self.fetch(ParteInteresada, record_id).objetivo_estrategico)

    def test_list_is_ordered_by_name(self) -> None:
        self.seed_with(stakeholders, {"nombre": "Zapaterias"})
        self.seed_with(stakeholders, {"nombre": "Aseguradoras"})
        self.login()
        html = self.client.get(f"{self.BASE}/").get_data(as_text=True)
        self.assertLess(html.index("Aseguradoras"), html.index("Zapaterias"))


if __name__ == "__main__":
    unittest.main()
