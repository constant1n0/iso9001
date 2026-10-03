"""JSON registers of risks and training resources as thin adapters over their services."""

from __future__ import annotations

import unittest

from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import RecursoCapacitacion, RiesgoOportunidad
from app.services import risks_opportunities, training_resources


class RiskRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/riesgo_oportunidad/"
    MODEL = RiesgoOportunidad
    PK = "id_riesgo"
    CREATE = {"tipo": "Riesgo", "descripcion": "Corte de suministro", "plan_accion": "Stock"}
    UPDATE = {"plan_accion": "Doble proveedor"}
    UPDATED_KEY = "plan_accion"
    SERVICE = risks_opportunities
    SEED = {"tipo": "Oportunidad", "descripcion": "Nuevo mercado"}
    BAD = ({"tipo": "Riesgo"}, {"descripcion": "d"}, {"tipo": "Amenaza", "descripcion": "d"})
    PAGE_KEY = "descripcion"
    PAGE_VALUES = (CREATE | {"descripcion": "A"}, CREATE | {"descripcion": "B"}, CREATE | {"descripcion": "C"})
    test_a_duplicate_answers_409_and_leaves_the_session_usable = None  # no unique column


class ResourceRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/recurso_capacitacion/"
    MODEL = RecursoCapacitacion
    PK = "id_recurso"
    CREATE = {"recurso_necesario": "Sala de formación", "capacitacion_personal": True,
              "descripcion_documentacion": "Manual"}
    UPDATE = {"descripcion_documentacion": "Manual revisado"}
    UPDATED_KEY = "descripcion_documentacion"
    SERVICE = training_resources
    SEED = {"recurso_necesario": "Proyector"}
    BAD = ({"recurso_necesario": " "}, {"capacitacion_personal": True},
           {"recurso_necesario": "Ok", "capacitacion_personal": 1})
    PAGE_KEY = "recurso_necesario"
    PAGE_VALUES = ({"recurso_necesario": "Uno"}, {"recurso_necesario": "Dos"}, {"recurso_necesario": "Tres"})
    test_a_duplicate_answers_409_and_leaves_the_session_usable = None  # no unique column


if __name__ == "__main__":
    unittest.main()
