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


if __name__ == "__main__":
    unittest.main()
