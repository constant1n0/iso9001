"""Improvements JSON API as a thin adapter over the improvements service, without caching."""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap
from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import Mejora
from app.services import improvements


class ImprovementJsonRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/mejoras/api/"
    MODEL = Mejora
    PK = "id_mejora"
    CREATE = {"no_conformidad": "Retraso en entregas", "accion_correctiva": "Revisar rutas",
              "accion_preventiva": "Stock de seguridad"}
    UPDATE = {"accion_correctiva": "Cambiar de transportista"}
    UPDATED_KEY = "accion_correctiva"
    SERVICE = improvements
    SEED = {"no_conformidad": "Hallazgo"}
    BAD = ({"accion_correctiva": "x"}, {"no_conformidad": "  "},
           {"no_conformidad": "Ok", "fecha_implementacion": "2026-10-05T00:00:00"})  # not writable
    OPERATIVO_WRITES = True
    PAGE_KEY = "no_conformidad"
    PAGE_VALUES = ({"no_conformidad": "Uno"}, {"no_conformidad": "Dos"}, {"no_conformidad": "Tres"})
    test_a_duplicate_answers_409_and_leaves_the_session_usable = None  # no unique column

    def test_the_implementation_date_is_set_by_the_default_and_listed(self) -> None:
        self.login()
        body = self._post(self.CREATE).get_json()
        self.assertTrue(body["fecha_implementacion"])

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
