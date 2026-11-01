"""Improvements JSON API as a thin adapter over the improvements service, without caching."""

from __future__ import annotations

import unittest
from datetime import date
from functools import partial
from types import SimpleNamespace

import test_auth_bootstrap as bootstrap
from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import Mejora
from app.services import improvements


class ImprovementJsonRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/mejoras/api/"
    MODEL = Mejora
    PK = "id_mejora"
    CREATE = {"descripcion": "Retraso en entregas", "fecha_identificacion": "2026-10-05",
              "beneficio": "Stock de seguridad", "origen": "otro"}
    UPDATE = {"beneficio": "Cambiar de transportista"}
    UPDATED_KEY = "beneficio"
    SERVICE = SimpleNamespace(create=partial(improvements.create, today=date(2026, 10, 5)))
    SEED = {"descripcion": "Hallazgo", "fecha_identificacion": date(2026, 10, 5)}
    BAD = ({"beneficio": "x"}, {"descripcion": "  "},
           CREATE | {"fecha_implementacion": "2026-10-05"}, CREATE | {"estado": "aprobada"},
           CREATE | {"fecha_identificacion": "2999-01-01"})
    OPERATIVO_WRITES = True
    PAGE_KEY = "descripcion"
    PAGE_VALUES = tuple({"descripcion": label, "fecha_identificacion": "2026-10-05"}
                        for label in ("Uno", "Dos", "Tres"))
    test_a_duplicate_answers_409_and_leaves_the_session_usable = None  # no unique column

    def test_the_implementation_date_is_absent_until_implementation_and_fks_are_exposed(self) -> None:
        self.login()
        body = self._post(self.CREATE).get_json()
        self.assertIsNone(body["fecha_implementacion"])
        self.assertEqual("propuesta", body["estado"])
        for key in ("responsable_id", "no_conformidad_id", "hallazgo_id", "accion_revision_id",
                    "oportunidad_id", "objetivo_id", "aprobado_por_id", "revisor_eficacia_id"):
            self.assertIn(key, body)
            self.assertIsNone(body[key])

    def test_json_round_trips_person_and_origin_ids_and_rejects_mismatched_origin(self):
        from app.services import nonconformities, people

        person_id = self.seed_with(people, {'nombre': 'Ana'})
        nc_id = self.seed_with(nonconformities, {
            'descripcion': 'NC', 'fecha_detectada': date(2026, 10, 5)})
        self.login()
        response = self._post(self.CREATE | {'origen': 'no_conformidad',
                                            'no_conformidad_id': nc_id, 'responsable_id': person_id})
        self.assertEqual(201, response.status_code)
        body = response.get_json()
        self.assertEqual((person_id, nc_id, 'no_conformidad'),
                         (body['responsable_id'], body['no_conformidad_id'], body['origen']))
        before = self.actions()
        url = f"{self.BASE}{body['id_mejora']}"
        rejected = self.client.put(url, json={'origen': 'otro'})
        self.assertEqual(422, rejected.status_code)
        self.assertEqual(before, self.actions())
        self.assertEqual(body, self._rows()[0])
        accepted = self.client.put(url, json={'origen': 'otro', 'no_conformidad_id': None})
        self.assertEqual(200, accepted.status_code)
        self.assertIsNone(accepted.get_json()['no_conformidad_id'])

    def test_the_html_list_and_the_json_api_share_one_register(self) -> None:
        self.login()
        self._post(self.CREATE)
        self.assertIn("Retraso en entregas", self.client.get("/mejoras/").get_data(as_text=True))


class CacheExtensionRemovedTestCase(unittest.TestCase):
    def test_the_app_no_longer_carries_a_cache_extension(self) -> None:
        app = bootstrap.build_app()
        self.assertNotIn("cache", app.extensions)
        from app import extensions

        self.assertFalse(hasattr(extensions, "cache"))


if __name__ == "__main__":
    unittest.main()
