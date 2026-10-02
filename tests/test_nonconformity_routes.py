"""Nonconformity web routes as thin adapters over the service.

Every test runs with the audit flush guard installed on the session factory,
so a web write that skips its audit row fails with ``AuditGuardViolation``.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import AuditLog, NoConformidad, RoleEnum, User
from app.services import audit, errors

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
BASE = "/no_conformidades"
FORM = {
    "descripcion": "Pieza fuera de tolerancia",
    "fecha_detectada": "2026-10-05",
    "responsable": "Ana",
    "estado": "Abierta",
    "accion_correctiva": "",
}


class NonconformityRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(
                    User(
                        username=role.name.lower(),
                        email=f"{role.name.lower()}@example.com",
                        password=PASSWORD_HASH,
                        role=role,
                    )
                )
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
            self.remove_guard = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        self.remove_guard()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, role: RoleEnum = RoleEnum.OPERATIVO) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])

    def flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def seed(self, **values) -> int:
        """Create a record through the service, as production code would."""
        from app.services import nonconformities
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            nc = nonconformities.create(
                db.session,
                who,
                {"descripcion": "Semilla", "fecha_detectada": date(2026, 10, 1)} | values,
            )
            db.session.commit()
            return nc.id

    def rows(self):
        with self.app.app_context():
            return [
                (r.action, r.entity_id, r.channel, r.actor_label, r.actor_user_id)
                for r in AuditLog.query.order_by(AuditLog.id)
            ]

    # -- writes are audited and stamped ------------------------------------

    def test_create_is_audited_stamped_and_redirects_with_the_same_flash(self) -> None:
        self.login(RoleEnum.OPERATIVO)
        response = self.client.post(f"{BASE}/nueva", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(("success", "No conformidad registrada exitosamente"), self.flashes())
        with self.app.app_context():
            nc = NoConformidad.query.one()
            self.assertEqual(self.ids[RoleEnum.OPERATIVO], nc.created_by_id)
            self.assertEqual(
                [("create", nc.id, "web", "operativo", self.ids[RoleEnum.OPERATIVO])],
                self.rows(),
            )

    def test_edit_is_audited_and_stamps_the_editor(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.AUDITOR)
        response = self.client.post(
            f"{BASE}/editar/{nc_id}", data=FORM | {"estado": "En proceso"}
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "No conformidad actualizada exitosamente"), self.flashes())
        with self.app.app_context():
            nc = db.session.get(NoConformidad, nc_id)
            self.assertEqual("En proceso", nc.estado)
            self.assertEqual(self.ids[RoleEnum.AUDITOR], nc.updated_by_id)
            self.assertEqual("update", self.rows()[-1][0])

    def test_admin_delete_is_audited_with_a_snapshot(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.ADMINISTRADOR)
        response = self.client.post(f"{BASE}/eliminar/{nc_id}")
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "No conformidad eliminada exitosamente"), self.flashes())
        with self.app.app_context():
            self.assertIsNone(db.session.get(NoConformidad, nc_id))
            row = AuditLog.query.filter_by(action="delete").one()
            self.assertEqual("Semilla", row.before["descripcion"])

    def test_operativo_delete_is_still_refused_without_audit_rows(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(302, self.client.post(f"{BASE}/eliminar/{nc_id}").status_code)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(NoConformidad, nc_id))
        self.assertEqual(["create"], [r[0] for r in self.rows()])

    def test_the_guard_covers_request_sessions(self) -> None:
        """Sanity check: an unaudited write inside a request is rejected."""

        @self.app.route("/_test/unaudited", methods=["POST"])
        def _unaudited():
            db.session.add(NoConformidad(descripcion="x", fecha_detectada=date(2026, 1, 1)))
            db.session.commit()
            return "ok"

        self.login()
        self.assertEqual(500, self.client.post("/_test/unaudited").status_code)
        with self.app.app_context():
            self.assertEqual(0, NoConformidad.query.count())

    # -- reads keep their behaviour ----------------------------------------

    def test_list_filters_and_state_dropdown_keep_legacy_values(self) -> None:
        self.seed(descripcion="Ruido en linea", fecha_detectada=date(2026, 9, 1))
        self.seed(descripcion="Fuga de aceite", estado="Cerrada")
        with self.app.app_context():
            db.session.execute(
                NoConformidad.__table__.insert().values(
                    descripcion="Antigua", fecha_detectada=date(2026, 1, 1), estado="Pendiente"
                )
            )
            db.session.commit()
        self.login()
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        for text in ("Ruido en linea", "Fuga de aceite", "Antigua", "Pendiente"):
            self.assertIn(text, html)
        only_noise = self.client.get(f"{BASE}/?descripcion=RUIDO").get_data(as_text=True)
        self.assertIn("Ruido en linea", only_noise)
        self.assertNotIn("Fuga de aceite", only_noise)
        closed = self.client.get(f"{BASE}/?estado=Cerrada").get_data(as_text=True)
        self.assertIn("Fuga de aceite", closed)
        self.assertNotIn("Ruido en linea", closed)
        by_date = self.client.get(f"{BASE}/?fecha_detectada=2026-09-01").get_data(as_text=True)
        self.assertIn("Ruido en linea", by_date)
        self.assertNotIn("Fuga de aceite", by_date)

    def test_invalid_date_filter_is_ignored_instead_of_failing(self) -> None:
        self.seed()
        self.login()
        response = self.client.get(f"{BASE}/?fecha_detectada=no-es-fecha")
        self.assertEqual(200, response.status_code)
        self.assertIn("Semilla", response.get_data(as_text=True))

    def test_edit_form_offers_the_legacy_state_of_the_record(self) -> None:
        with self.app.app_context():
            db.session.execute(
                NoConformidad.__table__.insert().values(
                    id=50, descripcion="Antigua", fecha_detectada=date(2026, 1, 1),
                    estado="Pendiente",
                )
            )
            db.session.commit()
        self.login()
        response = self.client.get(f"{BASE}/editar/50")
        self.assertEqual(200, response.status_code)
        self.assertIn("Pendiente (heredado)", response.get_data(as_text=True))

    # -- domain errors on HTML posts ---------------------------------------

    def test_missing_records_answer_404_as_before(self) -> None:
        self.login(RoleEnum.ADMINISTRADOR)
        for method, url in (
            ("get", f"{BASE}/editar/999"),
            ("post", f"{BASE}/editar/999"),
            ("post", f"{BASE}/eliminar/999"),
            ("get", f"{BASE}/exportar_pdf/999"),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, data=FORM)
                self.assertEqual(404, response.status_code)
                self.assertEqual({"message": "Recurso no encontrado"}, response.get_json())

    def test_service_validation_error_flashes_and_returns_to_the_form(self) -> None:
        nc_id = self.seed()
        self.login()
        for url in (f"{BASE}/nueva", f"{BASE}/editar/{nc_id}"):
            target = "create" if url.endswith("nueva") else "update"
            with self.subTest(url=url):
                with patch(
                    f"app.services.nonconformities.{target}",
                    side_effect=errors.ValidationError("Dato rechazado."),
                ):
                    response = self.client.post(
                        url, data=FORM, headers={"Referer": f"http://localhost{url}"}
                    )
                self.assertEqual(302, response.status_code)
                self.assertEqual(url, response.headers["Location"])
                self.assertIn(("danger", "Dato rechazado."), self.flashes())

    def test_service_conflict_flashes_and_returns_to_the_list(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.ADMINISTRADOR)
        with patch(
            "app.services.nonconformities.delete",
            side_effect=errors.Conflict("En uso."),
        ):
            response = self.client.post(
                f"{BASE}/eliminar/{nc_id}", headers={"Referer": f"http://localhost{BASE}/"}
            )
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/", response.headers["Location"])
        self.assertIn(("danger", "En uso."), self.flashes())

    def test_json_clients_still_get_a_json_domain_error(self) -> None:
        self.login()
        with patch(
            "app.services.nonconformities.create",
            side_effect=errors.Conflict("Duplicado."),
        ):
            response = self.client.post(
                f"{BASE}/nueva", data=FORM, headers={"Accept": "application/json"}
            )
        self.assertEqual(409, response.status_code)
        self.assertEqual({"error": "Duplicado."}, response.get_json())


if __name__ == "__main__":
    unittest.main()
