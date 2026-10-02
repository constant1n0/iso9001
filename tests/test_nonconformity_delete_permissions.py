"""Only administrators may hard-delete nonconformities."""

from __future__ import annotations

import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import NoConformidad, RoleEnum, User

PASSWORD = "StrongPassword123!"
LIST_URL = "/no_conformidades/"
DELETE_MARKER = "/no_conformidades/eliminar/"


class NonconformityDeletePermissionsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(
                    User(
                        username=role.name.lower(),
                        email=f"{role.name.lower()}@example.com",
                        password=generate_password_hash(PASSWORD),
                        role=role,
                    )
                )
            nc = NoConformidad(descripcion="NC", fecha_detectada=date(2026, 10, 5))
            db.session.add(nc)
            db.session.commit()
            self.nc_id = nc.id

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, role: RoleEnum) -> None:
        response = self.client.post(
            "/login", data={"username": role.name.lower(), "password": PASSWORD}
        )
        self.assertEqual(302, response.status_code)

    def _delete(self):
        return self.client.post(f"{DELETE_MARKER}{self.nc_id}")

    def _survives(self) -> bool:
        with self.app.app_context():
            return db.session.get(NoConformidad, self.nc_id) is not None

    def test_operativo_delete_is_refused_and_record_survives(self) -> None:
        self._login(RoleEnum.OPERATIVO)
        self.assertEqual(302, self._delete().status_code)
        self.assertTrue(self._survives(), "OPERATIVO deleted the nonconformity")

    def test_auditor_delete_is_refused_and_record_survives(self) -> None:
        self._login(RoleEnum.AUDITOR)
        self.assertEqual(302, self._delete().status_code)
        self.assertTrue(self._survives(), "AUDITOR deleted the nonconformity")

    def test_admin_can_still_delete(self) -> None:
        self._login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self._delete().status_code)
        self.assertFalse(self._survives())

    def test_list_hides_delete_control_from_operativo(self) -> None:
        self._login(RoleEnum.OPERATIVO)
        html = self.client.get(LIST_URL).get_data(as_text=True)
        self.assertIn("Editar", html)
        self.assertNotIn(DELETE_MARKER, html)

    def test_list_shows_delete_control_to_admin(self) -> None:
        self._login(RoleEnum.ADMINISTRADOR)
        html = self.client.get(LIST_URL).get_data(as_text=True)
        self.assertIn(f"{DELETE_MARKER}{self.nc_id}", html)


if __name__ == "__main__":
    unittest.main()
