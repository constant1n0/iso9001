"""Periodic review, list filters, code lock and workflow page guards (DC-4 of ``document-control``).

Decision DC5: each document has a next review date; ``documents.due_for_review``
lists the documents still in use whose review falls due by a date, the
dashboard shows those overdue or due within 30 days, and a weekly e-mail tells
each owner (through the user linked to the person) and the administrators
which reviews are overdue. The list filters run in the database
(``documents.list_`` and ``list_page``), the MCP ``documentos`` module offers
them, a document's code is locked once a revision has been published, and the
approval and rejection pages refuse a revision that is not in review.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from test_document_screens import (ADMIN, AUDITOR, BASE, OPERATIVO, SYSTEM, TODAY, R,
                                   ScreensBase)
from test_scheduled_notifications import _run_worker_probe

from app.extensions import db, mail
from app.models import DocumentCategory, Person, User
from app.services import document_revisions
from app.services.actor import Actor
from app.services.errors import PermissionDenied, ValidationError

SENDER = "qms@example.invalid"
CODE_LOCKED = "El código de un documento no se puede cambiar una vez publicada una revisión."
APPROVE_REFUSED = "Solo se puede aprobar una revisión enviada a revisión."
REJECT_REFUSED = "Solo se puede rechazar una revisión enviada a revisión."
BAD_OVERDUE_ON = "La fecha de las revisiones vencidas debe ser una fecha."
TODAY_IN_DASHBOARD = "app.routes.dashboard_routes.local_today"


def documents():
    from app.services import documents as service

    return service


def codes(rows) -> list[str]:
    return [row.code for row in rows]


def web(role) -> Actor:
    return Actor(None, f"web-{role.name.lower()}", role, "web")


class DueDocumentsBase(ScreensBase):
    """Documents around ``TODAY`` (2026-10-30), all owned by Eva unless said otherwise."""

    def seed_due(self) -> dict[str, int]:
        ids = {
            "VEN-1": self.seed("VEN-1", R.vigente, next_review_date=date(2026, 10, 20)),
            "HOY-1": self.seed("HOY-1", R.vigente, next_review_date=TODAY),
            "MES-1": self.seed("MES-1", next_review_date=TODAY + timedelta(days=10)),
            "LEJ-1": self.seed("LEJ-1", R.vigente, next_review_date=TODAY + timedelta(days=31)),
            "BAJ-1": self.seed("BAJ-1", R.vigente, next_review_date=date(2026, 9, 1)),
            "SIN-1": self.seed("SIN-1", R.vigente),
        }
        self.withdraw(ids["BAJ-1"])
        return ids


class DueForReviewTestCase(DueDocumentsBase):
    def setUp(self) -> None:
        super().setUp()
        self.seed_due()

    def due(self, role=ADMIN, **kwargs) -> list[str]:
        with self.app.app_context():
            return codes(documents().due_for_review(db.session, web(role), today=TODAY, **kwargs))

    def test_lists_documents_in_use_due_by_the_date_soonest_first(self) -> None:
        self.assertEqual(["VEN-1", "HOY-1"], self.due())
        self.assertEqual(["VEN-1", "HOY-1", "MES-1"], self.due(within_days=30))
        self.assertEqual(["VEN-1", "HOY-1", "MES-1", "LEJ-1"], self.due(within_days=31))

    def test_operativos_only_count_documents_in_force(self) -> None:
        self.assertEqual(["VEN-1", "HOY-1"], self.due(OPERATIVO, within_days=30))
        self.assertEqual(["VEN-1", "HOY-1", "MES-1"], self.due(AUDITOR, within_days=30))

    def test_the_date_comes_from_the_adapter_and_reading_needs_permission(self) -> None:
        with self.app.app_context():
            for today, within in ((datetime(2026, 10, 30, 9), 0), ("2026-10-30", 0),
                                  (TODAY, -1), (TODAY, "30"), (TODAY, True)):
                with self.subTest(today=today, within=within):
                    with self.assertRaises(ValueError):
                        documents().due_for_review(db.session, web(ADMIN), today=today,
                                                   within_days=within)
            no_read = Actor(None, "token", ADMIN, "mcp", scopes=frozenset({"write"}))
            with self.assertRaises(PermissionDenied):
                documents().due_for_review(db.session, no_read, today=TODAY)


class DashboardCardTestCase(DueDocumentsBase):
    def setUp(self) -> None:
        super().setUp()
        self.docs = self.seed_due()

    def dashboard(self, role) -> str:
        self.login(role)
        with patch(TODAY_IN_DASHBOARD, return_value=TODAY):
            return self.html("/dashboard/")

    def card(self, html: str) -> str:
        start = html.index('id="revisiones-documentales"')
        return html[start:html.index("</article>", start)]

    def test_administrators_see_overdue_and_upcoming_reviews_with_links(self) -> None:
        card = self.card(self.dashboard(ADMIN))
        for code in ("VEN-1", "HOY-1", "MES-1"):
            self.assertIn(f'href="{BASE}/{self.docs[code]}"', card)
            self.assertIn(code, card)
        for code in ("LEJ-1", "BAJ-1", "SIN-1"):
            self.assertNotIn(code, card)
        self.assertEqual(1, card.count(">Vencida</span>"))  # only VEN-1: HOY-1 is due today
        self.assertIn(f'href="{BASE}/?vencida=1"', card)
        self.assertLess(card.index("VEN-1"), card.index("HOY-1"))
        self.assertLess(card.index("HOY-1"), card.index("MES-1"))

    def test_operativos_count_only_documents_in_force(self) -> None:
        card = self.card(self.dashboard(OPERATIVO))
        self.assertIn("VEN-1", card)
        self.assertIn("HOY-1", card)
        self.assertNotIn("MES-1", card)

    def test_the_card_needs_the_right_to_read_documents(self) -> None:
        self.login(OPERATIVO)
        with (patch(TODAY_IN_DASHBOARD, return_value=TODAY),
              patch("app.routes.dashboard_routes.can", return_value=False)):
            html = self.html("/dashboard/")
        self.assertNotIn('id="revisiones-documentales"', html)

    def test_nothing_due_says_so(self) -> None:
        self.login(ADMIN)
        with patch(TODAY_IN_DASHBOARD, return_value=date(2026, 1, 1)):
            card = self.card(self.html("/dashboard/"))
        self.assertIn("Ninguna revisión pendiente", card)


class ReviewAlertTestCase(ScreensBase):
    """Olga is linked to the OPERATIVO user, Pablo to the AUDITOR user; Eva has no user."""

    overrides = {"MAIL_DEFAULT_SENDER": SENDER}

    def setUp(self) -> None:
        super().setUp()
        with self.app.app_context():
            insert = Person.__table__.insert()
            self.olga = db.session.execute(insert.values(
                nombre="Olga Sanz", user_id=self.ids[OPERATIVO])).inserted_primary_key[0]
            self.pablo = db.session.execute(insert.values(
                nombre="Pablo Mora", user_id=self.ids[AUDITOR])).inserted_primary_key[0]
            db.session.commit()
        overdue = date(2026, 10, 20)
        self.seed("OLG-1", R.vigente, owner_id=self.olga, next_review_date=overdue)
        self.seed("OLG-2", owner_id=self.olga, next_review_date=overdue)  # never in force
        self.seed("OLG-3", R.vigente, owner_id=self.olga, next_review_date=TODAY + timedelta(1))
        self.seed("PAB-1", R.vigente, owner_id=self.pablo, next_review_date=TODAY)
        self.withdraw(self.seed("PAB-2", R.vigente, owner_id=self.pablo,
                                next_review_date=overdue))
        self.seed("EVA-1", R.vigente, next_review_date=overdue)
        self.seed("ANA-1", R.vigente, owner_id=self.ana, next_review_date=overdue)

    def send(self, today=TODAY):
        from app import audit_notifications as notifications

        with self.app.app_context(), mail.record_messages() as outbox:
            sent = notifications.send_document_review_alert(today=today)
        return sent, {message.recipients[0]: message for message in outbox}, len(outbox)

    def set_user(self, role, **values) -> None:
        with self.app.app_context():
            db.session.execute(User.__table__.update()
                               .where(User.id == self.ids[role]).values(**values))
            db.session.commit()

    def test_owners_get_their_overdue_reviews_and_administrators_all_of_them(self) -> None:
        sent, mails, count = self.send()
        self.assertEqual(3, sent)
        self.assertEqual(3, count)  # the administrator owning ANA-1 gets one message
        self.assertEqual({"administrador@example.com", "operativo@example.com",
                          "auditor@example.com"}, set(mails))
        admin = mails["administrador@example.com"]
        self.assertEqual(SENDER, admin.sender)
        for code in ("OLG-1", "OLG-2", "PAB-1", "EVA-1", "ANA-1"):
            self.assertIn(code, admin.body)
        for code in ("OLG-3", "PAB-2"):
            self.assertNotIn(code, admin.body)
        self.assertIn("Olga Sanz", admin.body)  # the administrator sees each owner
        self.assertIn("20/10/2026", admin.body)
        # Owners get their own documents, and an operativo only those in force.
        self.assertIn("OLG-1", mails["operativo@example.com"].body)
        for code in ("OLG-2", "OLG-3", "PAB-1", "EVA-1", "ANA-1"):
            self.assertNotIn(code, mails["operativo@example.com"].body)
        self.assertIn("PAB-1", mails["auditor@example.com"].body)
        for code in ("OLG-1", "PAB-2", "EVA-1"):
            self.assertNotIn(code, mails["auditor@example.com"].body)

    def test_inactive_users_and_owners_without_e_mail_are_skipped_and_logged(self) -> None:
        from app import audit_notifications as notifications

        self.set_user(AUDITOR, active=False)
        self.set_user(OPERATIVO, email=None)
        with self.assertLogs(notifications.logger, "INFO") as logs:
            sent, mails, _ = self.send()
        self.assertEqual(1, sent)
        self.assertEqual({"administrador@example.com"}, set(mails))
        output = "\n".join(logs.output)
        self.assertRegex(output, r"auditor.*inactive")
        self.assertRegex(output, r"operativo.*no e-mail")
        self.assertRegex(output, rf"person {self.eva}\b.*no user")
        self.assertNotIn("auditor@example.com", output)

    def test_nothing_overdue_sends_nothing(self) -> None:
        self.assertEqual((0, {}, 0), self.send(today=date(2026, 9, 1)))

    def test_it_defaults_to_the_configured_local_date(self) -> None:
        from app import audit_notifications as notifications

        with (self.app.app_context(), mail.record_messages(),
              patch.object(notifications, "local_today", return_value=TODAY) as today):
            notifications.send_document_review_alert()
        today.assert_called_once_with()


class ReviewAlertScheduleTestCase(unittest.TestCase):
    def test_the_alert_runs_every_monday_at_eight(self) -> None:
        result = _run_worker_probe()
        self.assertEqual(0, result.returncode, result.stderr)
        probe = json.loads(result.stdout.strip().splitlines()[-1])
        entry = probe["schedule"]["document-review-alert-weekly"]
        self.assertEqual("iso9001.send_document_review_alert", entry["task"])
        self.assertEqual("<crontab: 0 8 * * monday (m/h/dM/MY/d)>", entry["crontab"])
        self.assertIn(entry["task"], probe["registered"])


class ListFiltersTestCase(ScreensBase):
    """A-1 in force (Ana, overdue), B-2 never published (procedure), C-3 withdrawn."""

    def setUp(self) -> None:
        super().setUp()
        self.a1 = self.seed("A-1", R.vigente, owner_id=self.ana, next_review_date=date(2026, 10, 1))
        self.b2 = self.seed("B-2", category=DocumentCategory.PROCEDIMIENTO_OPERATIVO,
                            next_review_date=date(2027, 1, 1))
        self.c3 = self.seed("C-3", R.vigente, next_review_date=date(2026, 9, 1))
        self.withdraw(self.c3)

    def listed(self, role=ADMIN, **filters) -> list[str]:
        with self.app.app_context():
            rows = codes(documents().list_(db.session, web(role), **filters))
            page, total = documents().list_page(db.session, web(role), **filters,
                                                page=1, per_page=10)
            self.assertEqual(rows, codes(page))
            self.assertEqual(len(rows), total)
            return rows

    def test_filters_run_in_the_service_and_combine(self) -> None:
        procedure = DocumentCategory.PROCEDIMIENTO_OPERATIVO
        cases = (
            ({}, ["A-1", "B-2", "C-3"]),
            ({"category": procedure}, ["B-2"]),
            ({"category": "PROCEDIMIENTO_OPERATIVO"}, ["B-2"]),
            ({"owner_id": self.ana}, ["A-1"]),
            ({"owner_id": 999}, []),
            ({"owner_id": 0}, []),
            ({"status": "vigente"}, ["A-1"]),
            ({"status": "sin_publicar"}, ["B-2"]),
            ({"status": "de_baja"}, ["C-3"]),
            ({"overdue_on": TODAY}, ["A-1"]),
            ({"overdue_on": date(2026, 10, 1)}, []),  # due that day is not overdue yet
            ({"owner_id": self.eva, "status": "de_baja"}, ["C-3"]),
            ({"category": procedure, "status": "vigente"}, []),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                self.assertEqual(expected, self.listed(**filters))

    def test_operativos_filter_only_documents_in_force(self) -> None:
        self.assertEqual(["A-1"], self.listed(OPERATIVO))
        self.assertEqual([], self.listed(OPERATIVO, status="sin_publicar"))
        self.assertEqual([], self.listed(OPERATIVO, status="de_baja"))

    def test_bad_filter_values_are_refused_in_spanish(self) -> None:
        cases = (
            ({"category": "NOPE"}, "La categoría no es válida."),
            ({"owner_id": "1"}, "El propietario debe ser un número entero."),
            ({"owner_id": True}, "El propietario debe ser un número entero."),
            ({"status": "Vigente"}, "El estado no es válido."),
            ({"overdue_on": "2026-10-30"}, BAD_OVERDUE_ON),
            ({"overdue_on": datetime(2026, 10, 30)}, BAD_OVERDUE_ON),
        )
        with self.app.app_context():
            for filters, message in cases:
                for function in (documents().list_, documents().list_page):
                    with self.subTest(filters=filters, function=function.__name__):
                        with self.assertRaises(ValidationError) as caught:
                            function(db.session, web(ADMIN), **filters)
                        self.assertEqual(message, caught.exception.message)

    def test_the_list_route_hands_its_filters_to_the_service(self) -> None:
        self.login(ADMIN)
        service = documents()
        with (patch("app.routes.document_routes.local_today", return_value=TODAY),
              patch.object(service, "list_", wraps=service.list_) as listed):
            html = self.html(f"{BASE}/?categoria=PROCEDIMIENTO_OPERATIVO&estado=sin_publicar"
                             f"&propietario={self.eva}&vencida=1")
        kwargs = listed.call_args.kwargs
        self.assertEqual(
            {"category": "PROCEDIMIENTO_OPERATIVO", "owner_id": self.eva,
             "status": "sin_publicar", "overdue_on": TODAY},
            {key: kwargs.get(key) for key in ("category", "owner_id", "status", "overdue_on")})
        self.assertIn("Ningún documento coincide con los filtros.", html)
        # The owner picker still offers every owner, not only those listed.
        for name in ("Ana Pérez", "Eva Ruiz"):
            self.assertIn(f">{name}</option>", html)

    def test_the_mcp_module_offers_category_owner_and_status_filters(self) -> None:
        from app.mcp_server import operations
        from app.mcp_server.registry import MODULES

        module = MODULES["documentos"]
        filters = {f.name: f for f in module.filters}
        self.assertEqual({"category", "owner_id", "status"}, set(filters))
        self.assertEqual(("vigente", "sin_publicar", "de_baja"), filters["status"].allowed)
        self.assertIn("PROCEDIMIENTO_OPERATIVO", filters["category"].allowed)
        self.assertEqual("integer", filters["owner_id"].type)
        mcp = Actor(None, "token", ADMIN, "mcp", scopes=frozenset({"read"}))
        with self.app.app_context():
            for given, expected in (({"status": "vigente"}, ["A-1"]),
                                    ({"category": "PROCEDIMIENTO_OPERATIVO"}, ["B-2"]),
                                    ({"owner_id": self.ana}, ["A-1"])):
                with self.subTest(filters=given):
                    result = operations.list_records(db.session, mcp, module, given, 1, 10)
                    self.assertEqual(expected, [item["code"] for item in result["items"]])


class CodeLockTestCase(ScreensBase):
    def update(self, doc_id: int, data: dict):
        with self.app.app_context():
            updated = documents().update(db.session, SYSTEM, doc_id, data)
            db.session.commit()
            return updated.code

    def test_the_code_stays_editable_until_a_revision_is_published(self) -> None:
        doc_id = self.seed("BOR-1", R.aprobado)
        self.assertEqual("BOR-2", self.update(doc_id, {"code": "BOR-2"}))
        with self.app.app_context():
            document_revisions.publish(db.session, SYSTEM, self.revision(doc_id, 1).id,
                                       today=TODAY)
            db.session.commit()
        with self.assertRaises(ValidationError) as caught:
            self.update(doc_id, {"code": "BOR-3"})
        self.assertEqual(CODE_LOCKED, caught.exception.message)
        # The same code and the other fields still save.
        self.assertEqual("BOR-2", self.update(doc_id, {"code": "BOR-2", "title": "Otro"}))
        self.assertEqual("Otro", self.document(doc_id).title)

    def test_an_obsolete_revision_keeps_the_code_locked(self) -> None:
        doc_id = self.seed("OBS-1", R.vigente)
        self.later_draft(doc_id)
        self.advance(doc_id, 2, R.vigente)  # revision 1 becomes obsolete
        with self.assertRaises(ValidationError):
            self.update(doc_id, {"code": "OBS-2"})

    def test_the_edit_form_shows_a_locked_code_read_only(self) -> None:
        draft_only = self.seed("BOR-1")
        published = self.seed("PUB-1", R.vigente)
        self.login(ADMIN)
        editable = self.html(f"{BASE}/edit/{draft_only}")
        self.assertNotRegex(editable, r'<input[^>]*name="code"[^>]*readonly')
        locked = self.html(f"{BASE}/edit/{published}")
        self.assertRegex(locked, r'<input[^>]*name="code"[^>]*readonly')
        self.assertIn("ya tiene una revisión publicada", locked)
        response = self.client.post(f"{BASE}/edit/{published}", data={
            "title": "Título PUB-1", "code": "PUB-9", "category": "OTRO",
            "owner_id": str(self.eva), "next_review_date": ""})
        self.assertEqual(200, response.status_code)
        self.assertIn(CODE_LOCKED, response.get_data(as_text=True))
        self.assertEqual("PUB-1", self.document(published).code)

    def test_a_successful_edit_lands_on_the_document_page(self) -> None:
        doc_id = self.seed("PUB-1", R.vigente)
        self.login(AUDITOR)
        self.post(f"{BASE}/edit/{doc_id}", {
            "title": "Título nuevo", "code": "PUB-1", "category": "OTRO",
            "owner_id": str(self.eva), "next_review_date": "2027-01-15"},
            expect=f"{BASE}/{doc_id}")
        self.assertIn(("success", "Documento actualizado exitosamente"), self.flashes())
        self.assertEqual(date(2027, 1, 15), self.document(doc_id).next_review_date)


class ReviewPagesGuardTestCase(ScreensBase):
    """The approve and reject pages only serve a revision in review."""

    def test_pages_refuse_a_revision_that_is_not_in_review(self) -> None:
        draft_doc = self.seed("BOR-1")
        approved_doc = self.seed("APR-1", R.aprobado)
        in_force_doc = self.seed("VIG-1", R.vigente)
        self.login(ADMIN)
        for doc_id, refusal in ((draft_doc, None), (approved_doc, None),
                                (in_force_doc, document_revisions.IMMUTABLE)):
            rev_id = self.revision(doc_id, 1).id
            state = self.revision(doc_id, 1).estado
            for verb, message, data in (
                ("approve", APPROVE_REFUSED, {"approver_id": str(self.luis)}),
                ("reject", REJECT_REFUSED, {"review_comment": "Motivo"}),
            ):
                for method in ("get", "post"):
                    with self.subTest(state=state.name, verb=verb, method=method):
                        url = self.rev_url(doc_id, rev_id, verb)
                        response = getattr(self.client, method)(url, data=data)
                        self.assertEqual(302, response.status_code)
                        self.assertEqual(self.detail(doc_id), response.headers["Location"])
                        self.assertIn(("danger", refusal or message), self.flashes())
            self.assertIs(state, self.revision(doc_id, 1).estado)

    def test_a_revision_in_review_still_gets_its_forms(self) -> None:
        doc_id = self.seed("REV-1", R.en_revision)
        rev_id = self.revision(doc_id, 1).id
        self.login(ADMIN)
        for verb in ("approve", "reject"):
            with self.subTest(verb=verb):
                self.assertEqual(200, self.client.get(self.rev_url(doc_id, rev_id, verb))
                                 .status_code)


if __name__ == "__main__":
    unittest.main()
