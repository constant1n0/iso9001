"""JSON register of roles as thin adapters over their services."""

from __future__ import annotations

import unittest

from json_register_routes import JsonRegisterContract
from register_routes import RegisterRoutesBase

from app.models import RolResponsabilidad
from app.services import roles_responsibilities


class RoleRoutesTestCase(JsonRegisterContract, RegisterRoutesBase):
    BASE = "/rol_responsabilidad/"
    MODEL = RolResponsabilidad
    PK = "id_rol"
    CREATE = {"rol": "Responsable de calidad", "compromiso_calidad": True,
              "descripcion_politica_calidad": "Política"}
    UPDATE = {"descripcion_politica_calidad": "Política revisada"}
    UPDATED_KEY = "descripcion_politica_calidad"
    SERVICE = roles_responsibilities
    SEED = {"rol": "Dirección"}
    BAD = ({"rol": "  "}, {"compromiso_calidad": True}, {"rol": "x" * 51}, {"rol": "Ok", "compromiso_calidad": "si"})
    UNIQUE = ("rol", "Dirección")
    PAGE_KEY = "rol"
    PAGE_VALUES = ({"rol": "Uno"}, {"rol": "Dos"}, {"rol": "Tres"})

if __name__ == "__main__":
    unittest.main()
