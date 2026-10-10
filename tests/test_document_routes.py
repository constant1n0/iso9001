"""Document web routes as thin adapters over the document service.

Every test runs with the audit flush guard installed on the session factory,
so a web write that skips its audit row fails with ``AuditGuardViolation``.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import time
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError
from test_document_files import PDF, docx as docx_bytes
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (AuditLog, Document, DocumentCategory, DocumentRevision, Person,
                        RoleEnum, User)
from app.services import audit, document_files, document_revisions, errors
from app.utils import security_logger

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
BASE = "/documents"
ROUTES_LOGGER = "app.routes.document_routes"
FORM = {
    "title": "Manual de calidad",
    "code": "MC-001",
    "category": "MANUAL_CALIDAD",
    "owner_id": "1",
    "next_review_date": "",
    "author_id": "1",
    "content": "Contenido",
}


class DocumentRoutesBase(unittest.TestCase):
    """Users of every role, one person and helpers; defines no tests of its own."""

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
            db.session.add(Person(nombre="Ana"))
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
                 "owner_id": 1, "author_id": 1, "content": "x", "change_summary": "Alta"}
                | values,
            )
            db.session.commit()
            return created.id

    def put_in_force(self, doc_id: int) -> None:
        from app.services import document_revisions as revisions
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(None, "seed", RoleEnum.ADMINISTRADOR, "system")
            db.session.execute(Person.__table__.insert().values(nombre="Eva"))  # id 2
            (first,) = revisions.list_(db.session, who, doc_id)
            revisions.submit(db.session, who, first.id)
            revisions.approve(db.session, who, first.id, approver_id=2, today=date(2026, 10, 5))
            revisions.publish(db.session, who, first.id, today=date(2026, 10, 5))
            db.session.commit()

    def rows(self):
        with self.app.app_context():
            return [
                (r.entity_type, r.action, r.entity_id, r.channel, r.actor_label,
                 r.actor_user_id)
                for r in AuditLog.query.order_by(AuditLog.id)
            ]


class DocumentRoutesTestCase(DocumentRoutesBase):
    # -- writes are audited and stamped ------------------------------------

    def test_create_is_audited_stamped_and_lands_on_the_document_page(self) -> None:
        self.login()
        response = self.client.post(f"{BASE}/new", data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Documento creado exitosamente"), self.flashes())
        with self.app.app_context():
            doc = Document.query.one()
            self.assertEqual(f"{BASE}/{doc.id}", response.headers["Location"])
            self.assertEqual(DocumentCategory.MANUAL_CALIDAD, doc.category)
            self.assertEqual(self.ids[RoleEnum.ADMINISTRADOR], doc.created_by_id)
            admin = ("web", "administrador", self.ids[RoleEnum.ADMINISTRADOR])
            self.assertEqual(
                [("documents", "create", doc.id, *admin),
                 ("document_revisions", "create", 1, *admin)],
                self.rows(),
            )

    def test_edit_is_audited_and_stamps_the_editor(self) -> None:
        doc_id = self.seed()
        self.login()
        response = self.client.post(
            f"{BASE}/edit/{doc_id}", data=FORM | {"code": "SEED-1", "title": "Nuevo"}
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Documento actualizado exitosamente"), self.flashes())
        with self.app.app_context():
            doc = db.session.get(Document, doc_id)
            self.assertEqual("Nuevo", doc.title)
            self.assertEqual(self.ids[RoleEnum.ADMINISTRADOR], doc.updated_by_id)
            self.assertEqual(("documents", "update"), self.rows()[-1][:2])

    def test_there_is_no_delete_route(self) -> None:
        doc_id = self.seed()
        self.login()
        self.assertEqual(404, self.client.post(f"{BASE}/delete/{doc_id}").status_code)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Document, doc_id))

    def test_operativos_cannot_write_and_leave_no_audit_rows(self) -> None:
        doc_id = self.seed()
        self.login(RoleEnum.OPERATIVO)
        for url in (f"{BASE}/new", f"{BASE}/edit/{doc_id}"):
            with self.subTest(url=url):
                self.assertEqual(302, self.client.post(url, data=FORM).status_code)
        with self.app.app_context():
            self.assertEqual(1, Document.query.count())
            self.assertEqual("Semilla", db.session.get(Document, doc_id).title)
        self.assertEqual(["create", "create"], [r[1] for r in self.rows()])

    def test_every_role_reads_the_effective_revision_of_a_document_in_force(self) -> None:
        in_force = self.seed(code="A-1", title="Vigente", content="Texto en vigor")
        self.put_in_force(in_force)
        self.seed(code="B-2", title="En preparación")
        for role in RoleEnum:
            self.login(role)
            with self.subTest(role=role.name):
                listing = self.client.get(f"{BASE}/").get_data(as_text=True)
                self.assertIn("Vigente", listing)
                self.assertEqual(role is not RoleEnum.OPERATIVO, "En preparación" in listing)
                page = self.client.get(f"{BASE}/{in_force}").get_data(as_text=True)
                self.assertIn("Texto en vigor", page)
                self.assertIn("05/10/2026", page)
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(404, self.client.get(f"{BASE}/2").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        response = self.client.post(f"{BASE}/new", data=FORM | {"title": ""})
        self.assertEqual(200, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, Document.query.count())

    def test_duplicate_code_shows_the_conflict_on_the_form_it_keeps(self) -> None:
        self.seed()
        self.login()
        response = self.client.post(f"{BASE}/new", data=FORM | {"code": "SEED-1"})
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Ya existe un documento con ese código.", html)
        self.assertIn('value="Manual de calidad"', html)
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
            ("get", f"{BASE}/999"),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, data=FORM)
                self.assertEqual(404, response.status_code)

    def test_service_validation_error_shows_the_form_again(self) -> None:
        doc_id = self.seed()
        self.login()
        for url in (f"{BASE}/new", f"{BASE}/edit/{doc_id}"):
            target = "create" if url.endswith("new") else "update"
            with self.subTest(url=url):
                with patch(
                    f"app.services.documents.{target}",
                    side_effect=errors.ValidationError("Dato rechazado."),
                ):
                    response = self.client.post(url, data=FORM)
                self.assertEqual(200, response.status_code)
                self.assertIn("Dato rechazado.", response.get_data(as_text=True))


class AttachmentRoutesBase(DocumentRoutesBase):
    """A temporary storage directory and upload helpers; defines no tests of its own."""

    def setUp(self) -> None:
        self.storage = tempfile.mkdtemp(prefix="iso9001-docs-test-")
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        with patch.dict(bootstrap.BASE_TEST_CONFIG, {"DOCUMENT_STORAGE_DIR": self.storage,
                                                     "DOCUMENT_MAX_BYTES": 200_000}):
            super().setUp()

    def stored(self) -> list[str]:
        return sorted(os.listdir(self.storage))

    def revision(self, doc_id: int, numero: int = 1) -> DocumentRevision:
        with self.app.app_context():
            found = DocumentRevision.query.filter_by(document_id=doc_id, numero=numero).one()
            db.session.expunge(found)
            return found

    def url(self, doc_id: int, rev_id: int, action: str = "attachment") -> str:
        return f"{BASE}/{doc_id}/revisions/{rev_id}/{action}"

    def upload(self, doc_id: int, rev_id: int, content: bytes = PDF, name: str = "Plan técnico.pdf"):
        return self.client.post(
            self.url(doc_id, rev_id), data={"file": (io.BytesIO(content), name)},
            content_type="multipart/form-data",
            headers={"Referer": f"http://localhost{BASE}/{doc_id}"})

    def draft_with_file(self, content: bytes = PDF):
        """A new document (revision 1 in draft) with ``content`` attached by an administrator."""
        doc_id = self.seed()
        rev_id = self.revision(doc_id).id
        self.login()
        self.assertEqual(302, self.upload(doc_id, rev_id, content).status_code)
        return doc_id, rev_id

    def later_draft(self, doc_id: int) -> int:
        """Start revision 2, a draft, of a document in force; return its id."""
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            draft = document_revisions.start_draft(
                db.session, who, doc_id, {"author_id": 1, "change_summary": "Cambio"})
            db.session.commit()
            return draft.id

    def failing_commit(self):
        """Make the next commit raise, as a lost connection would."""
        return patch.object(db.session, "commit", side_effect=SQLAlchemyError("down"))


class AttachmentRoutesTestCase(AttachmentRoutesBase):
    """Uploading, downloading and discarding attachments of revisions (DC7)."""

    # -- upload ------------------------------------------------------------

    def test_an_administrator_uploads_to_a_draft_and_the_event_is_logged(self) -> None:
        doc_id = self.seed()
        rev_id = self.revision(doc_id).id
        self.login()
        with self.assertLogs("security", "INFO") as logged:
            response = self.upload(doc_id, rev_id)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/{doc_id}"))
        self.assertIn(("success", "Fichero adjuntado."), self.flashes())
        rev = self.revision(doc_id)
        digest = hashlib.sha256(PDF).hexdigest()
        self.assertEqual(("Plan técnico.pdf", len(PDF), digest, "application/pdf"),
                         (rev.attachment_name, rev.attachment_size, rev.attachment_sha256,
                          rev.attachment_mime))
        self.assertEqual([rev.attachment_path], self.stored())
        self.assertEqual(1, len(logged.output))
        for part in ("DOCUMENT_ATTACHMENT_UPLOADED", "user=administrador", f"document={doc_id}",
                     f"revision={rev_id}", "name=Plan técnico.pdf", f"size={len(PDF)}",
                     f"sha256={digest}"):
            self.assertIn(part, logged.output[0])
        self.assertEqual(("document_revisions", "update"), self.rows()[-1][:2])

    def test_auditors_upload_and_operativos_cannot(self) -> None:
        doc_id = self.seed()
        rev_id = self.revision(doc_id).id
        self.login(RoleEnum.OPERATIVO)
        response = self.upload(doc_id, rev_id)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
        self.assertEqual([], self.stored())
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.upload(doc_id, rev_id).status_code)
        self.assertEqual(1, len(self.stored()))

    def test_refused_content_leaves_no_file_and_no_audit_row(self) -> None:
        doc_id = self.seed()
        rev_id = self.revision(doc_id).id
        self.login()
        written = len(self.rows())
        for content, name, message in (
            (b"MZ\x90\x00" + b"\x00" * 60, "setup.pdf", document_files.UNRECOGNIZED),
            (b"<html><script>alert(1)</script></html>", "page.docx", document_files.UNRECOGNIZED),
            (PDF, "plan.exe", document_files.UNSUPPORTED),
            (PDF, "plan.docx", document_files.EXTENSION_MISMATCH),
            (b"", "vacio.pdf", document_files.EMPTY),
            (PDF + b"0" * 200_000, "grande.pdf", document_files.too_large(200_000)),
        ):
            with self.subTest(name=name):
                with self.assertLogs("security", "WARNING") as logged:
                    response = self.upload(doc_id, rev_id, content, name)
                self.assertEqual(302, response.status_code)
                self.assertEqual(f"{BASE}/{doc_id}", response.headers["Location"])
                self.assertIn(("danger", message), self.flashes())
                self.assertIn("DOCUMENT_ATTACHMENT_REJECTED", logged.output[0])
                self.assertEqual([], self.stored())
        self.assertEqual(written, len(self.rows()))
        self.assertIsNone(self.revision(doc_id).attachment_path)

    def test_a_missing_file_field_is_refused(self) -> None:
        doc_id = self.seed()
        self.login()
        response = self.client.post(self.url(doc_id, self.revision(doc_id).id), data={},
                                    headers={"Referer": f"http://localhost{BASE}/{doc_id}"})
        self.assertEqual(302, response.status_code)
        self.assertIn(("danger", "Selecciona un fichero."), self.flashes())

    def test_uploading_to_an_effective_revision_deletes_the_new_file(self) -> None:
        doc_id = self.seed()
        self.put_in_force(doc_id)
        self.login()
        response = self.upload(doc_id, self.revision(doc_id).id)
        self.assertEqual(302, response.status_code)
        self.assertIn(("danger", "Una revisión vigente u obsoleta no se puede modificar."),
                      self.flashes())
        self.assertEqual([], self.stored())

    def test_a_revision_of_another_document_answers_404(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        other = self.seed(code="OTRO-1")
        other_rev = self.revision(other).id
        for method, url in (("post", self.url(other, rev_id)),
                            ("get", self.url(other, rev_id)),
                            ("post", self.url(doc_id, other_rev, "attachment/delete")),
                            ("post", self.url(other, rev_id, "discard"))):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(
                    url, data={"file": (io.BytesIO(PDF), "a.pdf")})
                self.assertEqual(404, response.status_code)
        self.assertEqual(1, len(self.stored()))
        self.assertIsNotNone(self.revision(doc_id).attachment_path)

    def test_replacing_deletes_the_old_file_after_the_commit(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        old = self.revision(doc_id).attachment_path
        self.assertEqual(302, self.upload(doc_id, rev_id, docx_bytes(), "Plan.docx").status_code)
        new = self.revision(doc_id).attachment_path
        self.assertNotEqual(old, new)
        self.assertEqual([new], self.stored())

    def test_a_failed_commit_keeps_both_files_and_logs_the_new_one(self) -> None:
        # The commit may have succeeded with its acknowledgement lost: the new
        # file stays, and the cleanup command removes it if nothing references it.
        doc_id, rev_id = self.draft_with_file()
        old = self.revision(doc_id).attachment_path
        with self.failing_commit(), self.assertLogs(ROUTES_LOGGER, "ERROR") as logged:
            response = self.upload(doc_id, rev_id, docx_bytes(), "Plan.docx")
        self.assertEqual(500, response.status_code)
        self.assertEqual(2, len(self.stored()))
        self.assertEqual(old, self.revision(doc_id).attachment_path)
        (new,) = set(self.stored()) - {old}
        self.assertIn(new, logged.output[0])

    def test_a_refusal_before_the_commit_deletes_the_new_file(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        old = self.revision(doc_id).attachment_path
        refusal = errors.ValidationError(document_revisions.NOT_A_DRAFT)
        with patch.object(document_revisions, "attach", side_effect=refusal):
            response = self.upload(doc_id, rev_id, docx_bytes(), "Plan.docx")
        self.assertEqual(302, response.status_code)
        self.assertIn(("danger", document_revisions.NOT_A_DRAFT), self.flashes())
        self.assertEqual([old], self.stored())

    def test_a_failed_commit_of_detach_or_discard_deletes_no_file(self) -> None:
        doc_id = self.seed()
        self.put_in_force(doc_id)
        draft_id = self.later_draft(doc_id)
        self.login()
        self.assertEqual(302, self.upload(doc_id, draft_id).status_code)
        kept = self.revision(doc_id, 2).attachment_path
        for action in ("attachment/delete", "discard"):
            with self.subTest(action=action):
                with self.failing_commit(), self.assertLogs(ROUTES_LOGGER, "ERROR") as logged:
                    response = self.client.post(self.url(doc_id, draft_id, action))
                self.assertEqual(500, response.status_code)
                self.assertIn(kept, logged.output[0])
                self.assertEqual([kept], self.stored())
                self.assertEqual(kept, self.revision(doc_id, 2).attachment_path)

    def test_an_upload_to_a_revision_that_is_not_a_draft_is_never_stored(self) -> None:
        doc_id = self.seed()
        self.put_in_force(doc_id)
        effective = self.revision(doc_id).id
        submitted = self.later_draft(doc_id)
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            document_revisions.submit(db.session, who, submitted)
            db.session.commit()
        self.login()
        cases = [(effective, document_revisions.IMMUTABLE),
                 (submitted, document_revisions.NOT_A_DRAFT)]
        with patch.object(document_files, "store", wraps=document_files.store) as store:
            for rev_id, message in cases:
                with self.subTest(message=message), \
                        self.assertLogs("security", "WARNING") as logged:
                    self.assertEqual(302, self.upload(doc_id, rev_id).status_code)
                    self.assertIn(("danger", message), self.flashes())
                    self.assertIn("DOCUMENT_ATTACHMENT_REJECTED", logged.output[0])
            with self.app.app_context():
                document_revisions.withdraw(db.session, who, doc_id, "Baja",
                                            today=date(2026, 10, 9))
                db.session.commit()
            self.upload(doc_id, submitted)
            self.assertIn(("danger", document_revisions.WITHDRAWN), self.flashes())
        store.assert_not_called()
        self.assertEqual([], self.stored())

    def test_user_supplied_names_cannot_forge_log_fields(self) -> None:
        doc_id = self.seed()
        rev_id = self.revision(doc_id).id
        self.login()
        with self.assertLogs("security", "INFO") as logged:
            self.upload(doc_id, rev_id, PDF, "plan | sha256=0000 | user=root.pdf")
            self.upload(doc_id, rev_id, b"MZ\x90\x00", "x | reason=ok | user=root.pdf")
        lines = [line.split(":", 2)[2] for line in logged.output]
        parsed = [dict(part.partition("=")[::2] for part in line.split(" | ")[1:])
                  for line in lines]
        self.assertEqual([8, 7], [len(line.split(" | ")) for line in lines])
        self.assertEqual(("administrador", hashlib.sha256(PDF).hexdigest(),
                          "plan _ sha256=0000 _ user=root.pdf"),
                         (parsed[0]["user"], parsed[0]["sha256"], parsed[0]["name"]))
        self.assertEqual(("administrador", document_files.UNRECOGNIZED),
                         (parsed[1]["user"], parsed[1]["reason"]))
        self.assertEqual("a_b_c", security_logger._field("a|b\nc"))

    def test_a_file_that_cannot_be_deleted_after_the_commit_is_left_for_cleanup(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        with patch.object(document_files, "remove", side_effect=OSError("busy")):
            response = self.upload(doc_id, rev_id, docx_bytes(), "Plan.docx")
        self.assertEqual(302, response.status_code)
        self.assertEqual(2, len(self.stored()))
        self.assertEqual("Plan.docx", self.revision(doc_id).attachment_name)

    def test_detach_clears_the_attachment_and_deletes_the_file(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        with self.assertLogs("security", "INFO") as logged:
            response = self.client.post(self.url(doc_id, rev_id, "attachment/delete"))
        self.assertEqual(302, response.status_code)
        self.assertIn(("success", "Fichero quitado."), self.flashes())
        self.assertIn("DOCUMENT_ATTACHMENT_DETACHED", logged.output[0])
        self.assertIsNone(self.revision(doc_id).attachment_path)
        self.assertEqual([], self.stored())
        self.login(RoleEnum.OPERATIVO)
        self.client.post(self.url(doc_id, rev_id, "attachment/delete"))
        self.assertIn(("danger", "No tienes permiso para acceder a esta página."),
                      self.flashes())

    def test_too_large_a_request_answers_413_and_stores_nothing(self) -> None:
        doc_id = self.seed()
        self.login()
        limit = self.app.config["MAX_CONTENT_LENGTH"]
        self.assertEqual(200_000 + 1024 * 1024, limit)
        response = self.upload(doc_id, self.revision(doc_id).id, PDF + b"0" * limit)
        self.assertEqual(413, response.status_code)
        self.assertEqual([], self.stored())
        self.assertIsNone(self.revision(doc_id).attachment_path)

    # -- discard -----------------------------------------------------------

    def test_discarding_a_later_draft_deletes_it_and_its_file(self) -> None:
        doc_id = self.seed()
        self.put_in_force(doc_id)
        from app.services import document_revisions as revisions
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system")
            draft_id = revisions.start_draft(db.session, who, doc_id, {"author_id": 1}).id
            db.session.commit()
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.upload(doc_id, draft_id).status_code)
        with self.assertLogs("security", "INFO") as logged:
            response = self.client.post(self.url(doc_id, draft_id, "discard"))
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/{doc_id}"))
        self.assertIn(("success", "Borrador descartado."), self.flashes())
        self.assertIn("DOCUMENT_DRAFT_DISCARDED", logged.output[0])
        self.assertEqual([], self.stored())
        with self.app.app_context():
            self.assertIsNone(db.session.get(DocumentRevision, draft_id))
        self.assertEqual(("document_revisions", "delete"), self.rows()[-1][:2])

    def test_revision_1_of_a_new_document_is_not_discarded(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        response = self.client.post(self.url(doc_id, rev_id, "discard"),
                                    headers={"Referer": f"http://localhost{BASE}/{doc_id}"})
        self.assertEqual(302, response.status_code)
        self.assertIn(("danger", document_revisions.FIRST_DRAFT), self.flashes())
        self.assertEqual(1, len(self.stored()))
        self.assertIsNotNone(self.revision(doc_id).attachment_path)

    # -- download ----------------------------------------------------------

    def test_every_role_downloads_the_effective_attachment_with_safe_headers(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        self.put_in_force(doc_id)
        for role in RoleEnum:
            self.login(role)
            with self.subTest(role=role.name):
                response = self.client.get(self.url(doc_id, rev_id))
                self.assertEqual(200, response.status_code)
                self.assertEqual(PDF, response.get_data())
                self.assertEqual("application/pdf", response.mimetype)
                self.assertEqual(
                    "attachment; filename=\"Plan tecnico.pdf\"; "
                    "filename*=UTF-8''Plan%20t%C3%A9cnico.pdf",
                    response.headers["Content-Disposition"])
                self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
                self.assertEqual("private, no-cache", response.headers["Cache-Control"])
                page = self.client.get(f"{BASE}/{doc_id}").get_data(as_text=True)
                self.assertIn(self.url(doc_id, rev_id), page)
                self.assertIn("Plan técnico.pdf", page)

    def test_drafts_are_downloaded_by_administrators_and_auditors_only_uncached(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        for role in (RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR):
            self.login(role)
            with self.subTest(role=role.name):
                response = self.client.get(self.url(doc_id, rev_id))
                self.assertEqual(200, response.status_code)
                self.assertEqual("no-store", response.headers["Cache-Control"])
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)

    def test_obsolete_attachments_are_for_administrators_and_auditors(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        self.put_in_force(doc_id)
        from app.services import document_revisions as revisions
        from app.services.actor import Actor

        with self.app.app_context():
            who = Actor(None, "seed", RoleEnum.ADMINISTRADOR, "system")
            revisions.withdraw(db.session, who, doc_id, "Sustituido", today=date(2026, 10, 9))
            db.session.commit()
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)
        self.login(RoleEnum.AUDITOR)
        response = self.client.get(self.url(doc_id, rev_id))
        self.assertEqual((200, "private, no-cache"),
                         (response.status_code, response.headers["Cache-Control"]))

    def test_a_revision_without_file_or_with_its_file_missing_answers_404(self) -> None:
        doc_id = self.seed()
        self.login()
        rev_id = self.revision(doc_id).id
        self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)
        self.upload(doc_id, rev_id)
        os.unlink(os.path.join(self.storage, self.revision(doc_id).attachment_path))
        self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)
        self.assertEqual(404, self.client.get(self.url(doc_id, 999)).status_code)

    def test_downloads_answer_conditional_and_range_requests(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        url, etag = self.url(doc_id, rev_id), f'"{hashlib.sha256(PDF).hexdigest()}"'
        draft = self.client.get(url)
        self.assertEqual((200, etag, "no-store"),
                         (draft.status_code, draft.headers["ETag"],
                          draft.headers["Cache-Control"]))
        self.put_in_force(doc_id)
        self.login(RoleEnum.OPERATIVO)
        unchanged = self.client.get(url, headers={"If-None-Match": etag})
        self.assertEqual((304, b""), (unchanged.status_code, unchanged.get_data()))
        changed = self.client.get(url, headers={"If-None-Match": '"other"'})
        self.assertEqual((200, PDF, len(PDF)),
                         (changed.status_code, changed.get_data(), changed.content_length))
        part = self.client.get(url, headers={"Range": "bytes=0-4"})
        self.assertEqual((206, b"%PDF-", f"bytes 0-4/{len(PDF)}"),
                         (part.status_code, part.get_data(), part.headers["Content-Range"]))
        unsatisfiable = self.client.get(url, headers={"Range": f"bytes={len(PDF) + 10}-"})
        self.assertEqual(416, unsatisfiable.status_code)

    def test_operativos_never_see_a_pending_draft_or_its_file(self) -> None:
        doc_id = self.seed()
        self.put_in_force(doc_id)
        draft_id = self.later_draft(doc_id)
        content = docx_bytes()
        self.login()
        self.assertEqual(302, self.upload(doc_id, draft_id, content, "Borrador reservado.docx")
                         .status_code)
        secrets = ("en preparación", "Borrador reservado.docx",
                   document_files.human_size(len(content)), hashlib.sha256(content).hexdigest())
        page = self.client.get(f"{BASE}/{doc_id}").get_data(as_text=True)
        for secret in secrets:
            self.assertIn(secret, page)  # what an administrator sees
        self.login(RoleEnum.OPERATIVO)
        response = self.client.get(f"{BASE}/{doc_id}")
        self.assertEqual(200, response.status_code)
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertEqual(404, self.client.get(self.url(doc_id, draft_id)).status_code)
        from app.services.actor import Actor

        with self.app.app_context():
            operativo = Actor(self.ids[RoleEnum.OPERATIVO], "operativo", RoleEnum.OPERATIVO,
                              "web")
            self.assertIsNone(document_revisions.in_preparation(db.session, operativo, doc_id))

    def test_a_corrupted_stored_name_is_logged_apart_from_a_missing_file(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        os.unlink(os.path.join(self.storage, self.revision(doc_id).attachment_path))
        with self.assertLogs(ROUTES_LOGGER, "ERROR") as missing:
            self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)
        with self.app.app_context():
            db.session.execute(DocumentRevision.__table__.update()
                               .where(DocumentRevision.id == rev_id)
                               .values(attachment_path="../../etc/passwd"))
            db.session.commit()
        with self.assertLogs(ROUTES_LOGGER, "ERROR") as corrupted:
            self.assertEqual(404, self.client.get(self.url(doc_id, rev_id)).status_code)
        self.assertIn("is missing", missing.output[0])
        self.assertIn("invalid stored file name", corrupted.output[0])
        self.assertNotIn("../", corrupted.output[0])

    def test_the_detail_page_offers_the_draft_controls_to_writers_only(self) -> None:
        doc_id, rev_id = self.draft_with_file()
        page = self.client.get(f"{BASE}/{doc_id}").get_data(as_text=True)
        self.assertIn("máximo 195,3 KB", page)
        self.assertIn(f"Plan técnico.pdf</a> · {len(PDF)} B", page)
        self.assertIn('enctype="multipart/form-data"', page)
        self.assertIn(f'action="{self.url(doc_id, rev_id)}"', page)
        self.assertIn(self.url(doc_id, rev_id, "attachment/delete"), page)
        self.assertNotIn(self.url(doc_id, rev_id, "discard"), page)  # revision 1 stays
        self.assertIn('accept=".pdf,.docx,.xlsx,.odt"', page)
        self.login(RoleEnum.OPERATIVO)
        self.put_in_force(doc_id)
        page = self.client.get(f"{BASE}/{doc_id}").get_data(as_text=True)
        self.assertNotIn("multipart/form-data", page)
        self.assertNotIn("attachment/delete", page)


class StorageConfigTestCase(unittest.TestCase):
    def test_the_default_directory_is_created_under_the_instance_path(self) -> None:
        instance = tempfile.mkdtemp(prefix="iso9001-instance-")
        self.addCleanup(shutil.rmtree, instance, ignore_errors=True)
        with patch.object(Flask, "auto_find_instance_path", return_value=instance):
            app = bootstrap.build_app(DOCUMENT_STORAGE_DIR=None)
        expected = os.path.join(instance, "documents")
        self.assertEqual(expected, app.config["DOCUMENT_STORAGE_DIR"])
        self.assertEqual(0o700, os.stat(expected).st_mode & 0o777)
        self.assertEqual(20 * 1024 * 1024, app.config["DOCUMENT_MAX_BYTES"])
        self.assertEqual(21 * 1024 * 1024, app.config["MAX_CONTENT_LENGTH"])

    def test_a_configured_directory_is_created_and_the_limit_parsed(self) -> None:
        parent = tempfile.mkdtemp(prefix="iso9001-storage-")
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        target = os.path.join(parent, "nested", "docs")
        app = bootstrap.build_app(DOCUMENT_STORAGE_DIR=target, DOCUMENT_MAX_BYTES="1048576")
        self.assertTrue(os.path.isdir(target))
        self.assertEqual((target, 1048576, 2 * 1048576),
                         (app.config["DOCUMENT_STORAGE_DIR"], app.config["DOCUMENT_MAX_BYTES"],
                          app.config["MAX_CONTENT_LENGTH"]))

    def test_a_directory_under_static_or_a_bad_limit_stops_start_up(self) -> None:
        static = os.path.join(bootstrap.PROJECT_ROOT, "app", "static", "documents")
        with self.assertRaises(RuntimeError):
            bootstrap.build_app(DOCUMENT_STORAGE_DIR=static)
        self.assertFalse(os.path.exists(static))
        for bad in ("abc", "0", "-5", 0, True):
            with self.subTest(limit=bad):
                with self.assertRaises(RuntimeError):
                    bootstrap.build_app(DOCUMENT_MAX_BYTES=bad)


class CleanupCommandTestCase(AttachmentRoutesBase):
    def orphan(self, name: str, age_seconds: int) -> str:
        path = os.path.join(self.storage, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        moment = time.time() - age_seconds
        os.utime(path, (moment, moment))
        return name

    def test_dry_run_reports_and_a_real_run_deletes_old_orphans_only(self) -> None:
        doc_id, _rev_id = self.draft_with_file()
        referenced = self.revision(doc_id).attachment_path
        old = self.orphan("a" * 32, 2 * 3600)
        recent = self.orphan("b" * 32, 60)
        self.orphan(".upload-x.tmp", 2 * 3600)
        rows = len(self.rows())
        runner = self.app.test_cli_runner()
        with patch.object(db.session, "commit", side_effect=AssertionError("no writes")):
            dry = runner.invoke(args=["cleanup-document-files", "--dry-run"])
        self.assertEqual(0, dry.exit_code, dry.output)
        self.assertIn("1 referenced, 1 orphaned, 1 too recent, 1 ignored.", dry.output)
        self.assertIn(f"Dry run: would delete 1 orphaned file(s): {old}", dry.output)
        self.assertEqual(4, len(self.stored()))
        with patch.object(db.session, "commit", side_effect=AssertionError("no writes")):
            real = runner.invoke(args=["cleanup-document-files"])
        self.assertEqual(0, real.exit_code, real.output)
        self.assertIn("Deleted 1 orphaned file(s).", real.output)
        self.assertEqual(sorted([".upload-x.tmp", referenced, recent]), self.stored())
        self.assertEqual(rows, len(self.rows()))

    def test_a_database_error_aborts_before_any_deletion(self) -> None:
        old = self.orphan("a" * 32, 2 * 3600)
        with patch.object(db.session, "scalars", side_effect=SQLAlchemyError("down")):
            result = self.app.test_cli_runner().invoke(args=["cleanup-document-files"])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("The referenced files could not be read.", result.output)
        self.assertEqual([old], self.stored())

    def test_a_file_that_cannot_be_removed_is_reported_with_a_failing_exit(self) -> None:
        stuck, gone = self.orphan("a" * 32, 2 * 3600), self.orphan("c" * 32, 2 * 3600)
        remove = document_files.remove

        def flaky(base: str, name: str) -> bool:
            if name == stuck:
                raise PermissionError("busy")
            return remove(base, name)

        with patch.object(document_files, "remove", side_effect=flaky):
            result = self.app.test_cli_runner().invoke(args=["cleanup-document-files"])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("Deleted 1 orphaned file(s).", result.output)
        self.assertIn(f"Could not delete: {stuck}", result.output)
        self.assertEqual([stuck], self.stored())
        self.assertNotIn(gone, self.stored())

    def test_a_missing_storage_directory_is_an_error(self) -> None:
        shutil.rmtree(self.storage)
        result = self.app.test_cli_runner().invoke(args=["cleanup-document-files"])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("Document storage directory not found", result.output)


if __name__ == "__main__":
    unittest.main()
