"""Document control screens (task DC-3 of ``document-control``).

The revision workflow through the web (decisions DC1-DC6): a document is
created with draft revision 1 and lands on its page; administrators and
auditors edit drafts, attach files, submit, reject and publish; only
administrators approve (never the author) and withdraw. Every role reads the
documents in force; drafts stay with administrators and auditors. Forms keep
what was typed when a service refuses it, and a withdrawn document is
read-only. Every test runs with the audit flush guard installed, so a web
write that skips its audit row fails.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from test_delete_forms import _PostForms
from test_document_files import PDF
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (Document, DocumentCategory, DocumentRevision, EstadoRevision, Person,
                        RoleEnum, User)
from app.services import audit, document_revisions
from app.services.actor import Actor
from app.services.errors import ValidationError

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
SYSTEM = Actor(None, "seed", ADMIN, "system")  # linked to no person: it approves anything
BASE = "/documents"
SEEDED = date(2026, 10, 5)
TODAY = date(2026, 10, 30)
TODAY_IN_ROUTES = "app.routes.document_routes.local_today"
DENIED = ("danger", "No tienes permiso para acceder a esta página.")
R = EstadoRevision


class ScreensBase(unittest.TestCase):
    """Users of every role and three people: Ana (the administrator's own), Eva and Luis."""

    overrides: dict = {}

    def setUp(self) -> None:
        self.storage = tempfile.mkdtemp(prefix="iso9001-screens-")
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.app = bootstrap.build_app(DOCUMENT_STORAGE_DIR=self.storage, **self.overrides)
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(),
                                    email=f"{role.name.lower()}@example.com",
                                    password=PASSWORD_HASH, role=role))
            db.session.flush()
            self.ids = {u.role: u.id for u in User.query.all()}
            people = [Person(nombre="Ana Pérez", user_id=self.ids[ADMIN]),
                      Person(nombre="Eva Ruiz"), Person(nombre="Luis Gil")]
            db.session.add_all(people)
            db.session.commit()
            self.ana, self.eva, self.luis = (p.id for p in people)
            self.remove_guard = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        self.remove_guard()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # -- helpers ------------------------------------------------------------

    def login(self, role: RoleEnum = ADMIN) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True

    def flashes(self) -> list:
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def html(self, url: str) -> str:
        return self.client.get(url).get_data(as_text=True)

    def post(self, url: str, data: dict | None = None, expect: str | None = None):
        """POST ``data`` and check it redirects to ``expect`` (a path)."""
        response = self.client.post(url, data=data or {})
        self.assertEqual(302, response.status_code, response.get_data(as_text=True)[:500])
        if expect is not None:
            self.assertEqual(expect, response.headers["Location"])
        return response

    def detail(self, doc_id: int) -> str:
        return f"{BASE}/{doc_id}"

    def rev_url(self, doc_id: int, rev_id: int, verb: str) -> str:
        return f"{BASE}/{doc_id}/revisions/{rev_id}/{verb}"

    def seed(self, code: str = "DOC-1", stage: EstadoRevision | None = None, **values) -> int:
        """A document authored and owned by Eva; revision 1 reaches ``stage`` (draft if None)."""
        from app.services import documents

        data = {"title": f"Título {code}", "code": code, "category": DocumentCategory.OTRO,
                "owner_id": self.eva, "author_id": self.eva, "content": f"Texto de {code}",
                "change_summary": "Alta"} | values
        with self.app.app_context():
            doc_id = documents.create(db.session, SYSTEM, data).id
            db.session.commit()
        if stage is not None:
            self.advance(doc_id, 1, stage)
        return doc_id

    def advance(self, doc_id: int, numero: int, stage: EstadoRevision) -> None:
        """Move revision ``numero`` from draft through the workflow up to ``stage``."""
        steps = (R.en_revision, R.aprobado, R.vigente)
        with self.app.app_context():
            rev_id = self.revision(doc_id, numero).id
            for step in steps[:steps.index(stage) + 1]:
                if step is R.en_revision:
                    document_revisions.submit(db.session, SYSTEM, rev_id)
                elif step is R.aprobado:
                    document_revisions.approve(db.session, SYSTEM, rev_id,
                                               approver_id=self.luis, today=SEEDED)
                else:
                    document_revisions.publish(db.session, SYSTEM, rev_id, today=SEEDED)
            db.session.commit()

    def later_draft(self, doc_id: int, **values) -> int:
        """Start the next revision (a draft by Eva) of a document in force; return its id."""
        with self.app.app_context():
            draft = document_revisions.start_draft(
                db.session, SYSTEM, doc_id, {"author_id": self.eva, "change_summary": "Cambio"})
            if values:
                document_revisions.edit_draft(db.session, SYSTEM, draft.id, values)
            db.session.commit()
            return draft.id

    def withdraw(self, doc_id: int) -> None:
        with self.app.app_context():
            document_revisions.withdraw(db.session, SYSTEM, doc_id, "Sustituido", today=SEEDED)
            db.session.commit()

    def revision(self, doc_id: int, numero: int) -> DocumentRevision:
        with self.app.app_context():
            found = DocumentRevision.query.filter_by(document_id=doc_id, numero=numero).one()
            db.session.expunge(found)
            return found

    def document(self, doc_id: int) -> Document:
        with self.app.app_context():
            found = db.session.get(Document, doc_id)
            db.session.expunge(found)
            return found


def selected(person_id: int, name: str) -> str:
    return f'<option selected value="{person_id}">{name}</option>'


class LifecycleTestCase(ScreensBase):
    def test_a_document_goes_through_its_whole_life_on_the_web(self) -> None:
        self.login(ADMIN)
        # The author defaults to the person linked to the administrator.
        self.assertIn(selected(self.ana, "Ana Pérez"), self.html(f"{BASE}/new"))
        response = self.client.post(f"{BASE}/new", data={
            "title": "Manual de calidad", "code": "MC-001", "category": "MANUAL_CALIDAD",
            "owner_id": str(self.ana), "next_review_date": "2027-10-01",
            "author_id": str(self.eva), "content": "Texto inicial"})
        with self.app.app_context():
            doc_id = Document.query.one().id
        self.assertEqual(self.detail(doc_id), response.headers["Location"])
        self.assertIn(("success", "Documento creado exitosamente"), self.flashes())
        page = self.html(self.detail(doc_id))
        for text in ("Revisión 1 en preparación", "Borrador", "Texto inicial", "Eva Ruiz",
                     "01/10/2027", "Este documento no tiene ninguna revisión vigente."):
            self.assertIn(text, page)
        rev1 = self.revision(doc_id, 1).id
        draft = {"content": "Texto con alcance", "change_summary": "Primera edición",
                 "author_id": str(self.eva)}
        back = self.detail(doc_id)

        # Draft, attachment and submission; the reviewer rejects it with a reason.
        self.assertIn("Texto inicial", self.html(self.rev_url(doc_id, rev1, "edit")))
        self.post(self.rev_url(doc_id, rev1, "edit"), draft, expect=back)
        self.assertIn(("success", "Borrador guardado."), self.flashes())
        upload = self.client.post(self.rev_url(doc_id, rev1, "attachment"),
                                  data={"file": (io.BytesIO(PDF), "Manual.pdf")},
                                  content_type="multipart/form-data")
        self.assertEqual(302, upload.status_code)
        self.post(self.rev_url(doc_id, rev1, "submit"), expect=back)
        self.assertIn(("success", "Revisión 1 enviada a revisión."), self.flashes())
        self.assertIs(R.en_revision, self.revision(doc_id, 1).estado)
        self.assertIn("Primera edición", self.html(self.rev_url(doc_id, rev1, "reject")))
        self.post(self.rev_url(doc_id, rev1, "reject"), {"review_comment": "Falta el alcance"},
                  expect=back)
        self.assertIn(("success", "Revisión 1 devuelta a borrador."), self.flashes())
        rejected = self.revision(doc_id, 1)
        self.assertEqual((R.borrador, "Falta el alcance"),
                         (rejected.estado, rejected.review_comment))
        self.assertIn("Falta el alcance", self.html(back))

        # Corrected, resubmitted, approved by Ana and published.
        self.post(self.rev_url(doc_id, rev1, "edit"), draft | {"content": "Texto final"},
                  expect=back)
        self.post(self.rev_url(doc_id, rev1, "submit"), expect=back)
        approve_page = self.html(self.rev_url(doc_id, rev1, "approve"))
        self.assertIn(selected(self.ana, "Ana Pérez"), approve_page)
        self.assertNotIn(f'value="{self.eva}"', approve_page)  # the author is never offered
        with patch(TODAY_IN_ROUTES, return_value=date(2026, 10, 20)):
            self.post(self.rev_url(doc_id, rev1, "approve"), {"approver_id": str(self.ana)},
                      expect=back)
            self.assertIn(("success", "Revisión 1 aprobada."), self.flashes())
            self.post(self.rev_url(doc_id, rev1, "publish"), expect=back)
        self.assertIn(("success", "Revisión 1 publicada: ya está vigente."), self.flashes())
        first = self.revision(doc_id, 1)
        self.assertEqual((R.vigente, self.ana, date(2026, 10, 20), date(2026, 10, 20)),
                         (first.estado, first.approver_id, first.approved_at,
                          first.effective_from))

        # A new revision starts from the text in force and replaces it.
        self.assertIn(selected(self.ana, "Ana Pérez"),
                      self.html(f"{BASE}/{doc_id}/revisions/new"))
        self.post(f"{BASE}/{doc_id}/revisions/new",
                  {"author_id": str(self.eva), "change_summary": "Amplía el alcance"},
                  expect=back)
        self.assertIn(("success", "Revisión 2 creada en borrador."), self.flashes())
        second = self.revision(doc_id, 2)
        self.assertEqual((R.borrador, "Texto final", "Amplía el alcance", None),
                         (second.estado, second.content, second.change_summary,
                          second.attachment_path))
        rev2 = second.id
        self.post(self.rev_url(doc_id, rev2, "edit"),
                  draft | {"content": "Texto revisado", "change_summary": "Amplía el alcance"},
                  expect=back)
        self.post(self.rev_url(doc_id, rev2, "submit"), expect=back)
        with patch(TODAY_IN_ROUTES, return_value=TODAY):
            self.post(self.rev_url(doc_id, rev2, "approve"), {"approver_id": str(self.ana)},
                      expect=back)
            self.post(self.rev_url(doc_id, rev2, "publish"), expect=back)
        first, second = self.revision(doc_id, 1), self.revision(doc_id, 2)
        self.assertEqual((R.obsoleto, TODAY), (first.estado, first.obsolete_from))
        self.assertEqual((R.vigente, TODAY), (second.estado, second.effective_from))
        page = self.html(back)
        self.assertIn("Historial de revisiones", page)
        self.assertIn("Texto revisado", page)
        self.assertIn(f'href="{self.rev_url(doc_id, rev1, "attachment")}">Manual.pdf</a>', page)
        for text in ("Obsoleto", "20/10/2026", "30/10/2026", "Último rechazo: Falta el alcance"):
            self.assertIn(text, page)

        # Withdrawal: the document becomes read-only and its revision obsolete.
        self.assertIn("Motivo de la baja", self.html(f"{BASE}/{doc_id}/withdraw"))
        with patch(TODAY_IN_ROUTES, return_value=date(2026, 11, 2)):
            self.post(f"{BASE}/{doc_id}/withdraw", {"withdrawn_reason": "Sustituido por MC-002"},
                      expect=back)
        self.assertIn(("success", "Documento dado de baja."), self.flashes())
        withdrawn = self.document(doc_id)
        self.assertEqual((date(2026, 11, 2), "Sustituido por MC-002", self.ana),
                         (withdrawn.withdrawn_at, withdrawn.withdrawn_reason,
                          withdrawn.withdrawn_by_id))
        self.assertIs(R.obsoleto, self.revision(doc_id, 2).estado)
        page = self.html(back)
        for text in ("Dado de baja el 02/11/2026", "Sustituido por MC-002", "Ana Pérez"):
            self.assertIn(text, page)
        for control in (f"{BASE}/edit/{doc_id}", "/revisions/new", "/withdraw"):
            self.assertNotIn(control, page)
        self.assertIn(">De baja</span>", self.html(f"{BASE}/"))
        self.assertEqual(1, len(os.listdir(self.storage)))  # the file stays with revision 1


class VisibilityTestCase(ScreensBase):
    def setUp(self) -> None:
        super().setUp()
        self.in_force = self.seed("A-1", R.vigente, content="Texto en vigor")
        self.draft = self.later_draft(self.in_force, content="Borrador reservado",
                                      change_summary="Resumen reservado")
        self.unpublished = self.seed("B-2")
        self.withdrawn = self.seed("C-3", R.vigente)
        self.withdraw(self.withdrawn)

    def test_operativos_see_documents_in_force_only_and_never_a_draft(self) -> None:
        self.login(OPERATIVO)
        listing = self.html(f"{BASE}/")
        self.assertIn("A-1", listing)
        self.assertIn(">Vigente</span>", listing)
        for hidden in ("B-2", "C-3", "Sin publicar", "De baja", 'name="estado"'):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, listing)
        page = self.html(self.detail(self.in_force))
        self.assertIn("Texto en vigor", page)
        self.assertIn("Historial de revisiones", page)
        for secret in ("Borrador reservado", "Resumen reservado", "en preparación", "Borrador",
                       f"/revisions/{self.draft}/", "Nueva revisión", "Dar de baja"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, page)
        for doc_id in (self.unpublished, self.withdrawn):
            self.assertEqual(404, self.client.get(self.detail(doc_id)).status_code)
        for url in (self.rev_url(self.in_force, self.draft, "edit"),
                    f"{BASE}/{self.in_force}/revisions/new", f"{BASE}/{self.in_force}/withdraw",
                    self.rev_url(self.in_force, self.draft, "reject"),
                    self.rev_url(self.in_force, self.draft, "approve")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                self.assertIn(DENIED, self.flashes())

    def test_administrators_and_auditors_see_every_document_and_its_drafts(self) -> None:
        for role in (ADMIN, AUDITOR):
            self.login(role)
            with self.subTest(role=role.name):
                listing = self.html(f"{BASE}/")
                for badge in (">Vigente</span>", ">Sin publicar</span>", ">De baja</span>"):
                    self.assertIn(badge, listing)
                page = self.html(self.detail(self.in_force))
                for text in ("Revisión 2 en preparación", "Borrador reservado",
                             "Resumen reservado", "Texto en vigor"):
                    self.assertIn(text, page)
                self.assertIn("Dado de baja el 05/10/2026",
                              self.html(self.detail(self.withdrawn)))


class WorkflowRulesTestCase(ScreensBase):
    def test_the_approver_rules_are_explained_in_spanish(self) -> None:
        doc_id = self.seed(stage=R.en_revision, author_id=self.ana)  # the admin's own person
        url = self.rev_url(doc_id, self.revision(doc_id, 1).id, "approve")
        self.login(ADMIN)
        page = self.html(url)
        self.assertNotIn(f'value="{self.ana}"', page)  # the author is never offered
        self.assertNotRegex(page, r'<option selected value="\d')  # nor is the admin's own
        self.assertIn("El autor de la revisión (Ana Pérez) no puede aprobarla", page)
        response = self.client.post(url, data={"approver_id": str(self.eva)})
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(document_revisions.AUTHOR_APPROVES, html)
        self.assertIn(selected(self.eva, "Eva Ruiz"), html)  # the choice is kept
        forged = self.client.post(url, data={"approver_id": str(self.ana)})
        self.assertIn("Opción inválida.", forged.get_data(as_text=True))
        empty = self.client.post(url, data={"approver_id": ""})
        self.assertIn("Este campo es obligatorio.", empty.get_data(as_text=True))
        self.assertIs(R.en_revision, self.revision(doc_id, 1).estado)

    def test_only_administrators_approve_and_withdraw(self) -> None:
        doc_id = self.seed(stage=R.en_revision)
        rev_id = self.revision(doc_id, 1).id
        for role in (AUDITOR, OPERATIVO):
            self.login(role)
            for method, url, data in (
                ("get", self.rev_url(doc_id, rev_id, "approve"), None),
                ("post", self.rev_url(doc_id, rev_id, "approve"), {"approver_id": str(self.luis)}),
                ("get", f"{BASE}/{doc_id}/withdraw", None),
                ("post", f"{BASE}/{doc_id}/withdraw", {"withdrawn_reason": "Motivo"}),
            ):
                with self.subTest(role=role.name, method=method, url=url):
                    response = getattr(self.client, method)(url, data=data)
                    self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                    self.assertIn(DENIED, self.flashes())
        self.assertIs(R.en_revision, self.revision(doc_id, 1).estado)
        self.assertIsNone(self.document(doc_id).withdrawn_at)
        self.login(AUDITOR)  # auditors reject, though
        self.post(self.rev_url(doc_id, rev_id, "reject"), {"review_comment": "Incompleta"},
                  expect=self.detail(doc_id))
        self.assertIs(R.borrador, self.revision(doc_id, 1).estado)

    def test_one_button_actions_flash_the_service_refusal(self) -> None:
        doc_id = self.seed(change_summary="")
        rev_id = self.revision(doc_id, 1).id
        self.login(AUDITOR)
        for verb, message in (("submit", document_revisions.SUMMARY_REQUIRED),
                              ("publish", "Solo se puede publicar una revisión aprobada.")):
            with self.subTest(verb=verb):
                self.post(self.rev_url(doc_id, rev_id, verb), expect=self.detail(doc_id))
                self.assertIn(("danger", message), self.flashes())
        self.assertIs(R.borrador, self.revision(doc_id, 1).estado)

    def test_forms_keep_what_was_typed_when_refused(self) -> None:
        self.seed("MC-001")
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/new", data={
            "title": "Manual tecleado", "code": "MC-001", "category": "OTRO",
            "owner_id": str(self.eva), "next_review_date": "", "author_id": str(self.eva),
            "content": "Texto que no se pierde"})
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        for kept in ("Ya existe un documento con ese código.", 'value="Manual tecleado"',
                     "Texto que no se pierde"):
            self.assertIn(kept, html)

        doc_id = self.seed("PR-001", R.vigente)
        draft = self.later_draft(doc_id)
        cases = (
            (self.rev_url(doc_id, draft, "edit"), "document_revisions.edit_draft",
             {"content": "Texto tecleado", "change_summary": "Resumen tecleado",
              "author_id": str(self.eva)}),
            (self.rev_url(doc_id, draft, "reject"), "document_revisions.reject",
             {"review_comment": "Motivo tecleado"}),
            (f"{BASE}/{doc_id}/withdraw", "document_revisions.withdraw",
             {"withdrawn_reason": "Baja tecleada"}),
            (f"{BASE}/edit/{doc_id}", "documents.update",
             {"title": "Título tecleado", "code": "PR-001", "category": "OTRO",
              "owner_id": str(self.eva), "next_review_date": ""}),
        )
        for url, target, data in cases:
            with self.subTest(url=url):
                with patch(f"app.services.{target}",
                           side_effect=ValidationError("Dato rechazado.")):
                    response = self.client.post(url, data=data)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn("Dato rechazado.", html)
                for value in data.values():
                    self.assertIn(value, html)
        # A real refusal: the document already has a revision in preparation.
        response = self.client.post(f"{BASE}/{doc_id}/revisions/new", data={
            "author_id": str(self.luis), "change_summary": "Otro cambio tecleado"})
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(document_revisions.ONE_DRAFT, html)
        self.assertIn("Otro cambio tecleado", html)
        self.assertIn(selected(self.luis, "Luis Gil"), html)

    def test_a_withdrawn_document_is_read_only_on_the_web(self) -> None:
        doc_id = self.seed(stage=R.vigente)
        draft = self.later_draft(doc_id, content="Borrador congelado")
        self.withdraw(doc_id)
        self.login(ADMIN)
        for method, url, data in (
            ("get", f"{BASE}/edit/{doc_id}", None),
            ("post", f"{BASE}/edit/{doc_id}", {"title": "Otro", "code": "DOC-1",
                                               "category": "OTRO", "owner_id": str(self.eva)}),
            ("get", f"{BASE}/{doc_id}/revisions/new", None),
            ("get", self.rev_url(doc_id, draft, "edit"), None),
            ("get", self.rev_url(doc_id, draft, "reject"), None),
            ("get", self.rev_url(doc_id, draft, "approve"), None),
            ("get", f"{BASE}/{doc_id}/withdraw", None),
            ("post", self.rev_url(doc_id, draft, "submit"), None),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data=data)
                self.assertEqual(302, response.status_code)
                self.assertEqual(self.detail(doc_id), response.headers["Location"])
                self.assertIn(("danger", document_revisions.WITHDRAWN), self.flashes())
        self.assertEqual("Título DOC-1", self.document(doc_id).title)
        page = self.html(self.detail(doc_id))
        for text in ("Dado de baja el 05/10/2026", "Sustituido", "Borrador congelado",
                     "Este documento no tiene ninguna revisión vigente."):
            self.assertIn(text, page)
        self.assertNotIn(f"/revisions/{draft}/", page)  # no draft controls left


class ListTestCase(ScreensBase):
    def setUp(self) -> None:
        super().setUp()
        self.seed("A-1", R.vigente, owner_id=self.ana, next_review_date=date(2026, 10, 1))
        self.seed("B-2", category=DocumentCategory.PROCEDIMIENTO_OPERATIVO,
                  next_review_date=date(2027, 1, 1))
        withdrawn = self.seed("C-3", R.vigente, next_review_date=date(2026, 9, 1))
        self.withdraw(withdrawn)
        self.login(ADMIN)

    def listing(self, query: str = "") -> str:
        with patch(TODAY_IN_ROUTES, return_value=TODAY):
            return self.html(f"{BASE}/{query}")

    def test_rows_show_the_revision_in_force_owner_review_and_status(self) -> None:
        html = self.listing()
        for text in ("Rev. 1 · 05/10/2026", "Ana Pérez", "Eva Ruiz", "01/10/2026", "01/01/2027",
                     ">Vigente</span>", ">Sin publicar</span>", ">De baja</span>",
                     "Procedimiento Operativo"):
            self.assertIn(text, html)
        # Only the document still in use is flagged: C-3 is withdrawn.
        self.assertEqual(1, html.count('class="badge badge--danger">Vencida</span>'))
        self.assertLess(html.index("A-1"), html.index("Vencida"))
        self.assertLess(html.index("Vencida"), html.index("B-2"))

    def test_filters_narrow_the_list_and_ignore_unknown_values(self) -> None:
        cases = {
            "?categoria=PROCEDIMIENTO_OPERATIVO": {"B-2"},
            f"?propietario={self.ana}": {"A-1"},
            "?estado=vigente": {"A-1"},
            "?estado=sin_publicar": {"B-2"},
            "?estado=de_baja": {"C-3"},
            "?vencida=1": {"A-1"},
            "?categoria=NOPE&estado=nope&propietario=abc": {"A-1", "B-2", "C-3"},
        }
        for query, codes in cases.items():
            with self.subTest(query=query):
                html = self.listing(query)
                self.assertEqual(codes, {code for code in ("A-1", "B-2", "C-3")
                                         if f'<td class="mono">{code}</td>' in html})


class CsrfTestCase(ScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def forms(self, url: str) -> list[dict]:
        page = _PostForms()
        page.feed(self.html(url))
        return page.forms

    def test_every_post_form_on_the_detail_page_carries_a_token(self) -> None:
        doc_id = self.seed(stage=R.vigente)
        draft = self.later_draft(doc_id)
        self.login(ADMIN)
        forms = self.forms(self.detail(doc_id))
        self.assertEqual(sorted(self.rev_url(doc_id, draft, verb)
                                for verb in ("attachment", "discard", "submit")),
                         sorted(form["action"] for form in forms))
        for form in forms:
            with self.subTest(action=form["action"]):
                self.assertTrue(form["fields"].get("csrf_token"))
        submit_url = self.rev_url(doc_id, draft, "submit")
        self.assertEqual(400, self.client.post(submit_url).status_code)
        (submit,) = (form for form in forms if form["action"] == submit_url)
        self.post(submit_url, submit["fields"], expect=self.detail(doc_id))
        self.assertIs(R.en_revision, self.revision(doc_id, 2).estado)

    def test_the_workflow_forms_need_their_token(self) -> None:
        doc_id = self.seed(stage=R.en_revision)
        rev_id = self.revision(doc_id, 1).id
        self.login(ADMIN)
        url = self.rev_url(doc_id, rev_id, "reject")
        self.assertEqual(400, self.client.post(url, data={"review_comment": "No"}).status_code)
        (form,) = self.forms(url)
        self.post(url, form["fields"] | {"review_comment": "No"}, expect=self.detail(doc_id))
        self.assertIs(R.borrador, self.revision(doc_id, 1).estado)


if __name__ == "__main__":
    unittest.main()
