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
from app.models import (
    AuditLog, EstadoNoConformidad, GravedadNoConformidad, NoConformidad, OrigenNoConformidad,
    RoleEnum, User,
)
from app.services import audit, errors

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
BASE = "/no_conformidades"
FORM = {
    "descripcion": "Pieza fuera de tolerancia",
    "fecha_detectada": "2026-10-05",
    "responsable": "Ana",
    "origen": "auditoria",
    "gravedad": "menor",
    "contencion": "Lote retenido",
    "causa_raiz": "",
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
            f"{BASE}/editar/{nc_id}",
            data=FORM | {"gravedad": "mayor", "causa_raiz": "Molde gastado",
                         "estado": "cerrada"},
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "No conformidad actualizada exitosamente"), self.flashes())
        with self.app.app_context():
            nc = db.session.get(NoConformidad, nc_id)
            self.assertEqual(
                (EstadoNoConformidad.abierta, OrigenNoConformidad.auditoria,
                 GravedadNoConformidad.mayor, "Lote retenido", "Molde gastado"),
                (nc.estado, nc.origen, nc.gravedad, nc.contencion, nc.causa_raiz),
            )
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

    def force_state(self, nc_id: int, estado: EstadoNoConformidad, **values) -> None:
        """A state the NC-1 screens cannot reach yet (closing comes in NC-2)."""
        with self.app.app_context():
            db.session.execute(
                NoConformidad.__table__.update().where(NoConformidad.id == nc_id)
                .values(estado=estado, **values)
            )
            db.session.commit()

    def test_list_filters_by_state_name_and_shows_spanish_labels(self) -> None:
        self.seed(descripcion="Ruido en linea", fecha_detectada=date(2026, 9, 1))
        self.force_state(self.seed(descripcion="Fuga de aceite"), EstadoNoConformidad.cerrada)
        self.force_state(self.seed(descripcion="Plan en marcha"),
                         EstadoNoConformidad.accion_planificada)
        self.login()
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        for text in ("Ruido en linea", "Fuga de aceite", "Acción planificada",
                     '<option value="en_verificacion">En verificación</option>'):
            self.assertIn(text, html)
        self.assertNotIn("EstadoNoConformidad", html)
        only_noise = self.client.get(f"{BASE}/?descripcion=RUIDO").get_data(as_text=True)
        self.assertIn("Ruido en linea", only_noise)
        self.assertNotIn("Fuga de aceite", only_noise)
        closed = self.client.get(f"{BASE}/?estado=cerrada").get_data(as_text=True)
        self.assertIn("Fuga de aceite", closed)
        self.assertNotIn("Ruido en linea", closed)
        self.assertIn('<option value="cerrada" selected>', closed)
        by_date = self.client.get(f"{BASE}/?fecha_detectada=2026-09-01").get_data(as_text=True)
        self.assertIn("Ruido en linea", by_date)
        self.assertNotIn("Fuga de aceite", by_date)

    def test_an_unknown_state_filter_is_ignored_instead_of_failing(self) -> None:
        self.seed()
        self.login()
        response = self.client.get(f"{BASE}/?estado=Cerrada")
        self.assertEqual(200, response.status_code)
        self.assertIn("Semilla", response.get_data(as_text=True))

    def test_invalid_date_filter_is_ignored_instead_of_failing(self) -> None:
        self.seed()
        self.login()
        response = self.client.get(f"{BASE}/?fecha_detectada=no-es-fecha")
        self.assertEqual(200, response.status_code)
        self.assertIn("Semilla", response.get_data(as_text=True))

    # -- states: shown, never edited; cancel and reopen are explicit ------

    def test_forms_show_the_state_without_a_state_field(self) -> None:
        nc_id = self.seed()
        self.login()
        for url in (f"{BASE}/nueva", f"{BASE}/editar/{nc_id}"):
            with self.subTest(url=url):
                html = self.client.get(url).get_data(as_text=True)
                self.assertNotIn('name="estado"', html)
                for name in ("origen", "gravedad", "contencion", "causa_raiz"):
                    self.assertIn(f'name="{name}"', html)
        edit = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertIn("Abierta", edit)
        self.assertIn('<option selected value="auditoria">', self.client.get(
            f"{BASE}/editar/{self.seed(origen='auditoria')}").get_data(as_text=True))

    def test_auditor_cancels_with_a_reason_and_admin_reopens(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.AUDITOR)
        edit = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertIn(f'action="{BASE}/cancelar/{nc_id}"', edit)
        self.assertNotIn(f"{BASE}/reabrir/", edit)
        response = self.client.post(f"{BASE}/cancelar/{nc_id}",
                                    data={"motivo_cancelacion": "Registrada dos veces"})
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "No conformidad cancelada."), self.flashes())
        with self.app.app_context():
            nc = db.session.get(NoConformidad, nc_id)
            self.assertEqual((EstadoNoConformidad.cancelada, "Registrada dos veces"),
                             (nc.estado, nc.motivo_cancelacion))
            self.assertIsNotNone(nc.fecha_cierre)
        page = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertIn("Registrada dos veces", page)
        self.assertNotIn('name="descripcion"', page)  # read-only: no edit form
        self.assertNotIn(f"{BASE}/reabrir/", page)  # only administrators reopen

        self.login(RoleEnum.ADMINISTRADOR)
        page = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertIn(f'action="{BASE}/reabrir/{nc_id}"', page)
        self.assertEqual(302, self.client.post(f"{BASE}/reabrir/{nc_id}").status_code)
        self.assertIn(("success", "No conformidad reabierta."), self.flashes())
        with self.app.app_context():
            nc = db.session.get(NoConformidad, nc_id)
            self.assertEqual((EstadoNoConformidad.abierta, None, None),
                             (nc.estado, nc.motivo_cancelacion, nc.fecha_cierre))
        self.assertEqual(["create", "update", "update"], [r[0] for r in self.rows()])

    def test_cancel_without_a_reason_is_refused_with_a_message(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.ADMINISTRADOR)
        response = self.client.post(
            f"{BASE}/cancelar/{nc_id}", data={"motivo_cancelacion": "  "},
            headers={"Referer": f"http://localhost{BASE}/editar/{nc_id}"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/editar/{nc_id}", response.headers["Location"])
        self.assertIn(("danger", "El motivo de la cancelación es obligatorio."), self.flashes())
        with self.app.app_context():
            self.assertEqual(EstadoNoConformidad.abierta,
                             db.session.get(NoConformidad, nc_id).estado)

    def test_operativo_cannot_cancel_and_auditor_cannot_reopen(self) -> None:
        nc_id = self.seed()
        self.login(RoleEnum.OPERATIVO)
        edit = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertNotIn(f"{BASE}/cancelar/", edit)
        self.client.post(f"{BASE}/cancelar/{nc_id}", data={"motivo_cancelacion": "x"})
        self.force_state(nc_id, EstadoNoConformidad.cerrada)
        self.login(RoleEnum.AUDITOR)
        self.client.post(f"{BASE}/reabrir/{nc_id}")
        with self.app.app_context():
            self.assertEqual(EstadoNoConformidad.cerrada,
                             db.session.get(NoConformidad, nc_id).estado)
        self.assertEqual(["create"], [r[0] for r in self.rows()])

    def test_a_closed_record_refuses_edits_with_a_message(self) -> None:
        nc_id = self.seed()
        self.force_state(nc_id, EstadoNoConformidad.cerrada, fecha_cierre=date(2026, 10, 9))
        self.login(RoleEnum.ADMINISTRADOR)
        page = self.client.get(f"{BASE}/editar/{nc_id}").get_data(as_text=True)
        self.assertIn("09/10/2026", page)
        response = self.client.post(f"{BASE}/editar/{nc_id}", data=FORM)
        self.assertEqual(200, response.status_code)
        # The service's refusal is flashed (the page's own notice words it differently).
        self.assertIn("Esta no conformidad está cerrada o cancelada",
                      response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual("Semilla", db.session.get(NoConformidad, nc_id).descripcion)

    def test_pdf_shows_the_state_label_and_the_new_fields(self) -> None:
        nc_id = self.seed(origen="cliente", gravedad="mayor", contencion="Lote retenido",
                          causa_raiz="Molde gastado")
        with self.app.test_request_context():
            from flask import render_template

            with self.app.app_context():
                nc = db.session.get(NoConformidad, nc_id)
                html = render_template("no_conformidades/pdf_template.html",
                                       no_conformidad=nc, generado=date(2026, 10, 9))
        for text in ("Abierta", "Cliente", "Mayor", "Lote retenido", "Molde gastado"):
            self.assertIn(text, html)
        self.assertNotIn("EstadoNoConformidad", html)

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

    def test_service_validation_error_shows_the_form_again_with_the_input(self) -> None:
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
                        url, data=FORM | {"descripcion": "Texto conservado"}
                    )
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn("Dato rechazado.", html)
                self.assertIn("Texto conservado", html)
        self.assertEqual(["create"], [row[0] for row in self.rows()])

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

    def test_an_operativo_user_can_open_the_list_and_the_create_form(self) -> None:
        """The person picker reads PEOPLE, which every role may read."""
        self.login(RoleEnum.OPERATIVO)
        for url in ("/no_conformidades/", "/no_conformidades/nueva"):
            with self.subTest(url=url):
                self.assertEqual(200, self.client.get(url).status_code)
