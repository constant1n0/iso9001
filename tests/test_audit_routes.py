"""Audit web routes as thin adapters over the audit service.

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
from app.models import Auditoria, AuditLog, EstadoAuditoriaEnum, RoleEnum, User
from app.services import audit, errors

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
BASE = "/auditorias"
FORM = {
    "area_auditada": "Compras",
    "fecha": "2026-10-05",
    "auditor": "Luis",
    "resultado": "Sin hallazgos",
    "accion_correctiva": "",
    "estado": "PENDIENTE",
}


class AuditRoutesTestCase(unittest.TestCase):
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

    def login(self, role: RoleEnum = RoleEnum.AUDITOR) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])

    def flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def seed(self, **values) -> int:
        """Create a record through the service, as production code would."""
        from app.services import audits
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            created = audits.create(
                db.session,
                who,
                {"area_auditada": "Semilla", "fecha": date(2026, 10, 1),
                 "auditor": "Eva", "resultado": "OK"} | values,
            )
            db.session.commit()
            return created.id

    def rows(self):
        with self.app.app_context():
            return [
                (r.action, r.entity_id, r.channel, r.actor_label, r.actor_user_id)
                for r in AuditLog.query.order_by(AuditLog.id)
            ]

    # -- writes are audited and stamped ------------------------------------

    def test_create_is_audited_stamped_and_redirects_with_the_same_flash(self) -> None:
        for role in (RoleEnum.AUDITOR, RoleEnum.ADMINISTRADOR):
            with self.subTest(role=role.name):
                self.login(role)
                response = self.client.post(f"{BASE}/nueva", data=FORM)
                self.assertEqual(302, response.status_code)
                self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
                self.assertIn(("success", "Auditoría creada exitosamente"), self.flashes())
        with self.app.app_context():
            last = Auditoria.query.order_by(Auditoria.id.desc()).first()
            self.assertEqual(EstadoAuditoriaEnum.PENDIENTE, last.estado)
            self.assertEqual(self.ids[RoleEnum.ADMINISTRADOR], last.created_by_id)
            self.assertEqual(
                [("create", last.id, "web", "administrador", self.ids[RoleEnum.ADMINISTRADOR])],
                self.rows()[-1:],
            )
            self.assertEqual(2, len(self.rows()))

    def test_edit_is_audited_and_stamps_the_editor(self) -> None:
        audit_id = self.seed()
        self.login(RoleEnum.AUDITOR)
        response = self.client.post(
            f"{BASE}/editar/{audit_id}", data=FORM | {"estado": "COMPLETADA"}
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Auditoría actualizada exitosamente"), self.flashes())
        with self.app.app_context():
            found = db.session.get(Auditoria, audit_id)
            self.assertEqual(EstadoAuditoriaEnum.COMPLETADA, found.estado)
            self.assertEqual(self.ids[RoleEnum.AUDITOR], found.updated_by_id)
            self.assertEqual("update", self.rows()[-1][0])

    def test_delete_is_audited_with_a_snapshot(self) -> None:
        audit_id = self.seed()
        self.login(RoleEnum.ADMINISTRADOR)
        response = self.client.post(f"{BASE}/eliminar/{audit_id}")
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Auditoría eliminada exitosamente"), self.flashes())
        with self.app.app_context():
            self.assertIsNone(db.session.get(Auditoria, audit_id))
            row = AuditLog.query.filter_by(action="delete", entity_id=audit_id).one()
            self.assertEqual("Semilla", row.before["area_auditada"])

    def test_an_auditor_can_edit_but_not_delete(self) -> None:
        audit_id = self.seed()
        self.login(RoleEnum.AUDITOR)
        response = self.client.post(f"{BASE}/eliminar/{audit_id}")
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
        self.assertIn(("danger", "No tienes permiso para acceder a esta página."), self.flashes())
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Auditoria, audit_id))
            self.assertEqual(["create"], [r.action for r in AuditLog.query.all()])

    def test_operativo_cannot_write_and_leaves_no_audit_rows(self) -> None:
        audit_id = self.seed()
        self.login(RoleEnum.OPERATIVO)
        for url in (f"{BASE}/nueva", f"{BASE}/editar/{audit_id}", f"{BASE}/eliminar/{audit_id}"):
            with self.subTest(url=url):
                self.assertEqual(302, self.client.post(url, data=FORM).status_code)
        with self.app.app_context():
            self.assertEqual(1, Auditoria.query.count())
            self.assertEqual("Eva", db.session.get(Auditoria, audit_id).auditor)
        self.assertEqual(["create"], [r[0] for r in self.rows()])

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        response = self.client.post(f"{BASE}/nueva", data=FORM | {"area_auditada": ""})
        self.assertEqual(200, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, Auditoria.query.count())

    def test_the_form_validates_without_consulting_the_current_user(self) -> None:
        """The role check lives in decorators and the service, not in the form."""
        from app.forms import AuditoriaForm

        with self.app.test_request_context(f"{BASE}/nueva", method="POST", data=FORM):
            self.assertTrue(AuditoriaForm().validate())

    # -- reads keep their behaviour ----------------------------------------

    def test_list_filters_and_pagination_are_unchanged(self) -> None:
        self.seed(area_auditada="Compras", auditor="Luis", fecha=date(2026, 9, 1))
        self.seed(area_auditada="Ventas", auditor="Eva", fecha=date(2026, 10, 1),
                  estado="COMPLETADA")
        self.login()
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        self.assertIn("Compras", html)
        self.assertIn("Ventas", html)

        def get(query):
            return self.client.get(f"{BASE}/?{query}").get_data(as_text=True)

        self.assertNotIn("Ventas", get("area=compr"))
        self.assertNotIn("Compras", get("auditor=EVA"))
        self.assertNotIn("Compras", get("estado=Completada"))
        self.assertIn("Compras", get("estado=no-existe"))  # invalid state ignored
        self.assertNotIn("Compras", get("fecha_inicio=2026-09-15"))
        self.assertNotIn("Ventas", get("fecha_fin=2026-09-15"))
        self.assertIn("Compras", get("fecha_inicio=2026-08-01&fecha_fin=2026-09-30"))

    def test_invalid_date_filters_are_ignored_instead_of_failing(self) -> None:
        self.seed()
        self.login()
        response = self.client.get(f"{BASE}/?fecha_inicio=no-es-fecha&fecha_fin=tampoco")
        self.assertEqual(200, response.status_code)
        self.assertIn("Semilla", response.get_data(as_text=True))

    def test_pagination_shows_ten_per_page(self) -> None:
        for n in range(12):
            self.seed(area_auditada=f"Area{n:02d}")
        self.login()
        first = self.client.get(f"{BASE}/").get_data(as_text=True)
        second = self.client.get(f"{BASE}/?page=2").get_data(as_text=True)
        self.assertIn("Area09", first)
        self.assertNotIn("Area10", first)
        self.assertIn("Area10", second)
        self.assertIn("Area11", second)

    def test_out_of_range_pages_answer_200_instead_of_overflowing(self) -> None:
        self.seed()
        self.login()
        for query in ("page=0", "page=-4", "page=abc", "page=" + "9" * 40):
            with self.subTest(query=query):
                self.assertEqual(200, self.client.get(f"{BASE}/?{query}").status_code)

    # -- empty form values --------------------------------------------------

    def test_unchanged_edit_writes_no_audit_row_and_keeps_stamps(self) -> None:
        audit_id = self.seed(area_auditada="Compras", fecha=date(2026, 10, 5),
                             auditor="Luis", resultado="Sin hallazgos")
        self.login(RoleEnum.ADMINISTRADOR)
        with self.app.app_context():
            before = db.session.get(Auditoria, audit_id).updated_at
        response = self.client.post(f"{BASE}/editar/{audit_id}", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertEqual(["create"], [r[0] for r in self.rows()])
        with self.app.app_context():
            found = db.session.get(Auditoria, audit_id)
            self.assertEqual(before, found.updated_at)
            self.assertIsNone(found.accion_correctiva)

    def test_an_empty_estado_cannot_pass_the_form(self) -> None:
        audit_id = self.seed()
        self.login()
        for url in (f"{BASE}/nueva", f"{BASE}/editar/{audit_id}"):
            with self.subTest(url=url):
                response = self.client.post(url, data=FORM | {"estado": ""})
                self.assertEqual(200, response.status_code)  # form re-rendered
        with self.app.app_context():
            self.assertEqual(1, Auditoria.query.count())
            self.assertEqual(EstadoAuditoriaEnum.PENDIENTE, db.session.get(Auditoria, audit_id).estado)
        self.assertEqual(["create"], [r[0] for r in self.rows()])

    # -- domain errors -----------------------------------------------------

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

    def test_service_validation_error_shows_the_form_again_with_the_input(self) -> None:
        audit_id = self.seed()
        self.login()
        for url in (f"{BASE}/nueva", f"{BASE}/editar/{audit_id}"):
            target = "create" if url.endswith("nueva") else "update"
            with self.subTest(url=url):
                with patch(
                    f"app.services.audits.{target}",
                    side_effect=errors.ValidationError("Dato rechazado."),
                ):
                    response = self.client.post(
                        url, data=FORM | {"area_auditada": "Texto conservado"}
                    )
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn("Dato rechazado.", html)
                self.assertIn("Texto conservado", html)
        self.assertEqual(["create"], [row[0] for row in self.rows()])


if __name__ == "__main__":
    unittest.main()

    def test_an_operativo_user_can_open_the_list_and_the_create_form(self) -> None:
        """The person picker reads PEOPLE, which every role may read."""
        self.login(RoleEnum.OPERATIVO)
        for url in ("/auditorias/", "/auditorias/nueva"):
            with self.subTest(url=url):
                self.assertEqual(200, self.client.get(url).status_code)
