"""JSON registers of processes and audit indicators as thin adapters over their services."""

from __future__ import annotations

import unittest

from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import AuditoriaIndicador, ProcesoOperacion
from app.services import audit_indicators, process_operations


class ProcessRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/proceso_operacion/"
    MODEL = ProcesoOperacion
    PK = "id_proceso"
    CREATE = {"proceso": "Compras", "criterio_calidad": "Homologados", "control_proveedor": True,
              "no_conformidad": "Retraso"}
    UPDATE = {"criterio_calidad": "Evaluación anual"}
    UPDATED_KEY = "criterio_calidad"
    SERVICE = process_operations
    SEED = {"proceso": "Producción"}
    BAD = ({"criterio_calidad": "x"}, {"proceso": " "}, {"proceso": "x" * 101},
           {"proceso": "Ok", "control_proveedor": "no"})
    UNIQUE = ("proceso", "Producción")
    PAGE_KEY = "proceso"
    PAGE_VALUES = ({"proceso": "Uno"}, {"proceso": "Dos"}, {"proceso": "Tres"})


class IndicatorRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/auditoria_indicador/"
    MODEL = AuditoriaIndicador
    PK = "id_auditoria"
    CREATE = {"area_auditoria": "Ventas", "fecha_auditoria": "2026-10-05T09:30:00",
              "resultado": "Conforme", "indicador_desempeno": "95 %"}
    UPDATE = {"resultado": "Con observaciones"}
    UPDATED_KEY = "resultado"
    SERVICE = audit_indicators
    SEED = {"area_auditoria": "Compras"}
    BAD = ({"resultado": "x"}, {"area_auditoria": " "}, {"area_auditoria": "x" * 51},
           {"area_auditoria": "Ok", "fecha_auditoria": "mañana"})
    PAGE_KEY = "area_auditoria"
    PAGE_VALUES = ({"area_auditoria": "Uno"}, {"area_auditoria": "Dos"}, {"area_auditoria": "Tres"})
    test_a_duplicate_answers_409_and_leaves_the_session_usable = None  # no unique column

    def test_the_audit_date_defaults_on_create_and_is_serialized_as_iso(self) -> None:
        self.login()
        body = self._post({"area_auditoria": "Calidad"}).get_json()
        self.assertTrue(body["fecha_auditoria"])
        self.assertEqual("2026-10-05T09:30:00", self._post(self.CREATE).get_json()["fecha_auditoria"])

    def test_an_explicit_null_date_is_a_clean_422_and_leaves_the_row_untouched(self) -> None:
        self.login()
        response = self._post({"area_auditoria": "Calidad", "fecha_auditoria": None})
        self.assertEqual(422, response.status_code)
        self.assertIn("fecha_auditoria", response.get_json()["error"])
        self.assertEqual(0, self.count(AuditoriaIndicador))
        record_id = self._post(self.CREATE).get_json()["id_auditoria"]
        response = self.client.put(f"{self.BASE}{record_id}", json={"fecha_auditoria": None})
        self.assertEqual(422, response.status_code)
        self.assertEqual("2026-10-05T09:30:00", self._rows()[0]["fecha_auditoria"])

    def test_utc_suffixes_are_stored_as_the_same_naive_utc_instant(self) -> None:
        self.login()
        for text in ("2026-10-05T09:30:00Z", "2026-10-05T09:30:00+00:00", "2026-10-05T11:30:00+02:00"):
            with self.subTest(text=text):
                response = self._post({"area_auditoria": "Calidad", "fecha_auditoria": text})
                self.assertEqual(201, response.status_code)
                self.assertEqual("2026-10-05T09:30:00", response.get_json()["fecha_auditoria"])


if __name__ == "__main__":
    unittest.main()
