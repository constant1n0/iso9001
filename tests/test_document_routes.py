"""Document web routes as thin adapters over the document service.

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
from app.models import AuditLog, Document, DocumentCategory, RoleEnum, User
from app.services import audit, errors

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
BASE = "/documents"
FORM = {
    "title": "Manual de calidad",
    "code": "MC-001",
    "category": "MANUAL_CALIDAD",
    "version": "1.0",
    "issued_date": "2026-10-05",
    "approved_by": "Direccion",
    "content": "Contenido",
}


class DocumentRoutesTestCase(unittest.TestCase):
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

    def login(self, role: RoleEnum = RoleEnum.ADMINISTRADOR) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])

    def flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def seed(self, **values) -> int:
        """Create a record through the service, as production code would."""
        from app.services import documents
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            created = documents.create(
                db.session,
                who,
                {"title": "Semilla", "code": "SEED-1", "category": DocumentCategory.OTRO,
                 "version": "1.0", "issued_date": date(2026, 10, 1),
                 "content": "x"} | values,
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
        self.login()
        response = self.client.post(f"{BASE}/new", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(("success", "Documento creado exitosamente"), self.flashes())
        with self.app.app_context():
            doc = Document.query.one()
            self.assertEqual(DocumentCategory.MANUAL_CALIDAD, doc.category)
            self.assertEqual(self.ids[RoleEnum.ADMINISTRADOR], doc.created_by_id)
            self.assertEqual(
                [("create", doc.id, "web", "administrador", self.ids[RoleEnum.ADMINISTRADOR])],
                self.rows(),
            )

    def test_edit_is_audited_and_stamps_the_editor(self) -> None:
        doc_id = self.seed()
        self.login()
        response = self.client.post(
            f"{BASE}/edit/{doc_id}", data=FORM | {"code": "SEED-1", "version": "2.0"}
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Documento actualizado exitosamente"), self.flashes())
        with self.app.app_context():
            doc = db.session.get(Document, doc_id)
            self.assertEqual("2.0", doc.version)
            self.assertEqual(self.ids[RoleEnum.ADMINISTRADOR], doc.updated_by_id)
            self.assertEqual("update", self.rows()[-1][0])

    def test_delete_is_audited_with_a_snapshot(self) -> None:
        doc_id = self.seed()
        self.login()
        response = self.client.post(f"{BASE}/delete/{doc_id}")
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Documento eliminado exitosamente"), self.flashes())
        with self.app.app_context():
            self.assertIsNone(db.session.get(Document, doc_id))
            row = AuditLog.query.filter_by(action="delete").one()
            self.assertEqual("SEED-1", row.before["code"])

    def test_non_admin_roles_cannot_write_and_leave_no_audit_rows(self) -> None:
        doc_id = self.seed()
        for role in (RoleEnum.AUDITOR, RoleEnum.OPERATIVO):
            self.login(role)
            for url in (f"{BASE}/new", f"{BASE}/edit/{doc_id}", f"{BASE}/delete/{doc_id}"):
                with self.subTest(role=role.name, url=url):
                    self.assertEqual(302, self.client.post(url, data=FORM).status_code)
        with self.app.app_context():
            self.assertEqual(1, Document.query.count())
            self.assertEqual("Semilla", db.session.get(Document, doc_id).title)
        self.assertEqual(["create"], [r[0] for r in self.rows()])

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        response = self.client.post(f"{BASE}/new", data=FORM | {"title": ""})
        self.assertEqual(200, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, Document.query.count())

    def test_duplicate_code_flashes_a_conflict_instead_of_failing(self) -> None:
        self.seed()
        self.login()
        response = self.client.post(
            f"{BASE}/new", data=FORM | {"code": "SEED-1"},
            headers={"Referer": f"http://localhost{BASE}/new"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/new", response.headers["Location"])
        self.assertIn(("danger", "Ya existe un documento con ese código."), self.flashes())
        with self.app.app_context():
            self.assertEqual(1, Document.query.count())

    # -- reads keep their behaviour ----------------------------------------

    def test_list_is_ordered_by_code_and_forms_render(self) -> None:
        self.seed(code="B-2", title="Segundo")
        doc_id = self.seed(code="A-1", title="Primero")
        self.login()
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        self.assertLess(html.index("Primero"), html.index("Segundo"))
        self.assertEqual(200, self.client.get(f"{BASE}/new").status_code)
        edit = self.client.get(f"{BASE}/edit/{doc_id}")
        self.assertEqual(200, edit.status_code)
        self.assertIn("A-1", edit.get_data(as_text=True))

    # -- domain errors -----------------------------------------------------

    def test_missing_records_answer_404_as_before(self) -> None:
        self.login()
        for method, url in (
            ("get", f"{BASE}/edit/999"),
            ("post", f"{BASE}/edit/999"),
            ("post", f"{BASE}/delete/999"),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, data=FORM)
                self.assertEqual(404, response.status_code)

    def test_service_validation_error_flashes_and_returns_to_the_form(self) -> None:
        doc_id = self.seed()
        self.login()
        for url in (f"{BASE}/new", f"{BASE}/edit/{doc_id}"):
            target = "create" if url.endswith("new") else "update"
            with self.subTest(url=url):
                with patch(
                    f"app.services.documents.{target}",
                    side_effect=errors.ValidationError("Dato rechazado."),
                ):
                    response = self.client.post(
                        url, data=FORM, headers={"Referer": f"http://localhost{url}"}
                    )
                self.assertEqual(302, response.status_code)
                self.assertEqual(url, response.headers["Location"])
                self.assertIn(("danger", "Dato rechazado."), self.flashes())


if __name__ == "__main__":
    unittest.main()
