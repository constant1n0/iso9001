"""The training resources JSON register as a thin adapter over its service.

The risks JSON endpoints went away in RO-2 of ``risks-opportunities``.
"""

from __future__ import annotations

import unittest

from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import RecursoCapacitacion
from app.services import training_resources


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
