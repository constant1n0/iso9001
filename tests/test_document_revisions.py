"""Document revisions and their workflow (decisions DC1-DC6 of ``document-control``).

Writes go through the services with the audit flush guard installed, so a
transition that forgets its audit row fails. The row lock is checked twice: a
spy on ``lock_document`` for every write, and a real race on PostgreSQL.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import date, datetime
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from mcp_support import McpDbCase, mcp_actor
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ADMIN, AUDITOR, OPERATIVO, ServiceBase, actor, errors

from app.extensions import db
from app.models import ATTACHMENT_COLUMNS, AuditLog, Person, RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
TODAY, LATER = date(2026, 10, 10), date(2026, 11, 2)
SYSTEM = Actor(user_id=None, label="seed", role=ADMIN, channel="cli")


def models():
    import app.models as module

    return module


def documents():
    from app.services import documents as module

    return module


def revisions():
    from app.services import document_revisions as module

    return module


def new_person(nombre: str, **values) -> int:
    result = db.session.execute(Person.__table__.insert().values(nombre=nombre, **values))
    db.session.commit()
    return result.inserted_primary_key[0]


def create_document(who: Actor, owner: int, **overrides):
    data = {"title": "Manual de calidad", "code": "MC-001", "category": "MANUAL_CALIDAD",
            "owner_id": owner, "author_id": owner, "content": "Texto inicial",
            "change_summary": "Primera edición"} | overrides
    created = documents().create(db.session, who, data)
    db.session.commit()
    return created


def revision(document_id: int, numero: int):
    model = models().DocumentRevision
    return db.session.scalars(select(model).where(
        model.document_id == document_id, model.numero == numero)).one()


def put_in_force(who: Actor, document_id: int, approver: int, today: date = TODAY):
    """Submit, approve and publish the document's revision in preparation."""
    pending = max(revisions().list_(db.session, who, document_id), key=lambda r: r.numero)
    for write, kwargs in ((revisions().submit, {}),
                          (revisions().approve, {"approver_id": approver, "today": today}),
                          (revisions().publish, {"today": today})):
        write(db.session, who, pending.id, **kwargs)
        db.session.commit()
    return pending


class RevisionBase(ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.ana, self.eva = new_person("Ana"), new_person("Eva")

    def new_document(self, who=None, **overrides):
        return create_document(who or actor(ADMIN), self.ana, **overrides)

    def step(self, write, *args, who=None, **kwargs):
        """Run a service write (as an administrator unless ``who``) and commit it."""
        result = write(db.session, who or actor(ADMIN), *args, **kwargs)
        db.session.commit()
        return result

    def refused(self, error, write, *args, who=None, **kwargs) -> str:
        """Assert ``write`` raises ``error``, roll back and return its message."""
        with self.assertRaises(getattr(errors(), error)) as caught:
            write(db.session, who or actor(ADMIN), *args, **kwargs)
        db.session.rollback()
        return caught.exception.message

    def in_force(self, **overrides):
        """A document whose revision 1 (by Ana, approved by Eva) is in force since TODAY."""
        doc = self.new_document(**overrides)
        return doc, put_in_force(actor(ADMIN), doc.id, self.eva)

    def drafted(self, doc, **data):
        data = {"author_id": self.eva, "change_summary": "Revisión"} | data
        return self.step(revisions().start_draft, doc.id, data)

    def rows(self):
        return [(r.entity_type, r.entity_id, r.action) for r in self.audit_rows()]


class ModelTestCase(unittest.TestCase):
    def test_states_are_stored_by_name_and_shown_in_spanish(self) -> None:
        self.assertEqual(
            [("borrador", "Borrador"), ("en_revision", "En revisión"), ("aprobado", "Aprobado"),
             ("vigente", "Vigente"), ("obsoleto", "Obsoleto")],
            [(state.name, state.value) for state in models().EstadoRevision])


class CreateTestCase(RevisionBase):
    def test_creating_a_document_creates_its_draft_revision_1(self) -> None:
        doc = self.new_document(who=actor(AUDITOR, user_id=8))
        first = revision(doc.id, 1)
        self.assertEqual(
            (models().EstadoRevision.borrador, "Texto inicial", self.ana, "Primera edición"),
            (first.estado, first.content, first.author_id, first.change_summary))
        self.assertEqual((None, None, 8), (first.effective_from, first.approver_id,
                                           first.created_by_id))
        self.assertEqual([("documents", doc.id, "create"),
                          ("document_revisions", first.id, "create")], self.rows())
        self.assertEqual("Borrador", self.audit_rows()[-1].after["estado"])

    def test_the_first_revision_needs_text_and_an_active_author(self) -> None:
        inactive = new_person("Baja", activo=False)
        base = {"title": "T", "code": "C-1", "category": "OTRO", "owner_id": self.ana,
                "author_id": self.ana, "content": "Texto"}
        cases = {
            "blank content": base | {"content": "  "},
            "no author": base | {"author_id": None},
            "unknown author": base | {"author_id": 999},
            "inactive author": base | {"author_id": inactive},
            "missing author": {k: v for k, v in base.items() if k != "author_id"},
            "missing content": {k: v for k, v in base.items() if k != "content"},
            "revision state": base | {"estado": "vigente"},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.refused("ValidationError", documents().create, data)
        self.assertEqual([], self.audit_rows())


class DraftTestCase(RevisionBase):
    def test_start_draft_copies_the_effective_text_into_the_next_revision(self) -> None:
        doc, first = self.in_force()
        draft = self.drafted(doc, change_summary="Ajustes")
        self.assertEqual((2, models().EstadoRevision.borrador, "Texto inicial", self.eva),
                         (draft.numero, draft.estado, draft.content, draft.author_id))
        self.assertIs(models().EstadoRevision.vigente, first.estado)

    def test_only_one_revision_is_in_preparation_at_a_time(self) -> None:
        doc, _first = self.in_force()
        self.drafted(doc)
        message = self.refused("ValidationError", revisions().start_draft, doc.id,
                               {"author_id": self.eva})
        self.assertEqual(revisions().ONE_DRAFT, message)
        fresh = self.new_document(code="MC-002")  # revision 1 is still in preparation
        self.refused("ValidationError", revisions().start_draft, fresh.id, {"author_id": self.eva})
        self.assertEqual(3, db.session.query(models().DocumentRevision).count())

    def test_the_database_refuses_a_second_pending_or_effective_revision(self) -> None:
        doc, _first = self.in_force()
        self.drafted(doc)
        for numero, estado in ((7, "borrador"), (8, "en_revision"), (9, "vigente")):
            with self.subTest(estado=estado):
                with self.assertRaises(IntegrityError):
                    db.session.execute(models().DocumentRevision.__table__.insert().values(
                        document_id=doc.id, numero=numero, estado=estado, content="x"))
                db.session.rollback()

    def test_edit_draft_changes_a_draft_only(self) -> None:
        doc, _first = self.in_force()
        draft = self.drafted(doc)
        self.step(revisions().edit_draft, draft.id, {"content": "Texto nuevo",
                                                     "change_summary": "Cambio"})
        self.assertEqual(("Texto nuevo", "Cambio"), (draft.content, draft.change_summary))
        written = len(self.audit_rows())
        self.step(revisions().edit_draft, draft.id, {"content": "Texto nuevo"})
        self.assertEqual(written, len(self.audit_rows()))
        for data in ({"estado": "vigente"}, {"numero": 5}, {"content": " "},
                     {"author_id": None}):
            with self.subTest(data=data):
                self.refused("ValidationError", revisions().edit_draft, draft.id, data)
        self.step(revisions().submit, draft.id)
        message = self.refused("ValidationError", revisions().edit_draft, draft.id,
                               {"content": "Otro"})
        self.assertEqual(revisions().NOT_A_DRAFT, message)
        self.assertEqual("Texto nuevo", draft.content)

    def test_effective_and_obsolete_revisions_are_immutable(self) -> None:
        doc, first = self.in_force()
        second = put_in_force(actor(ADMIN), self.drafted(doc).document_id, self.ana, LATER)
        writes = {
            "edit": lambda rev: (revisions().edit_draft, rev.id, {"content": "x"}),
            "submit": lambda rev: (revisions().submit, rev.id),
            "approve": lambda rev: (revisions().approve, rev.id),
            "reject": lambda rev: (revisions().reject, rev.id, "No"),
            "publish": lambda rev: (revisions().publish, rev.id),
        }
        extra = {"approve": {"approver_id": self.ana, "today": LATER}, "publish": {"today": LATER}}
        for rev in (first, second):
            for name, call in writes.items():
                with self.subTest(numero=rev.numero, write=name):
                    message = self.refused("ValidationError", *call(rev), **extra.get(name, {}))
                    self.assertEqual(revisions().IMMUTABLE, message)
        self.assertEqual(("Texto inicial", "Texto inicial"), (first.content, second.content))


class ReviewTestCase(RevisionBase):
    def test_submit_needs_a_change_summary(self) -> None:
        first = revision(self.new_document(change_summary=None).id, 1)
        self.assertIn("resumen de cambios",
                      self.refused("ValidationError", revisions().submit, first.id))
        self.step(revisions().edit_draft, first.id, {"change_summary": "Primera edición"})
        self.step(revisions().submit, first.id, who=actor(AUDITOR))
        self.assertIs(models().EstadoRevision.en_revision, first.estado)

    def test_approve_is_for_administrators_and_records_the_approver_and_date(self) -> None:
        first = revision(self.new_document().id, 1)
        self.refused("ValidationError", revisions().approve, first.id, approver_id=self.eva,
                     today=TODAY)  # still a draft
        self.step(revisions().submit, first.id)
        for role in (AUDITOR, OPERATIVO):
            self.refused("PermissionDenied", revisions().approve, first.id, who=actor(role),
                         approver_id=self.eva, today=TODAY)
        for approver in (None, 999, new_person("Baja", activo=False)):
            with self.subTest(approver=approver):
                self.refused("ValidationError", revisions().approve, first.id,
                             approver_id=approver, today=TODAY)
        self.step(revisions().approve, first.id, approver_id=self.eva, today=TODAY)
        self.assertEqual((models().EstadoRevision.aprobado, self.eva, TODAY, None),
                         (first.estado, first.approver_id, first.approved_at,
                          first.effective_from))

    def test_the_author_never_approves_their_own_revision(self) -> None:
        first = revision(self.new_document().id, 1)
        self.step(revisions().submit, first.id)
        message = self.refused("ValidationError", revisions().approve, first.id,
                               approver_id=self.ana, today=TODAY)
        self.assertEqual(revisions().AUTHOR_APPROVES, message)
        # Nor does an administrator who is the author name somebody else.
        db.session.execute(Person.__table__.update().where(Person.id == self.ana)
                           .values(user_id=7))
        db.session.commit()
        message = self.refused("ValidationError", revisions().approve, first.id,
                               who=actor(ADMIN, user_id=7), approver_id=self.eva, today=TODAY)
        self.assertEqual(revisions().AUTHOR_APPROVES, message)
        self.assertIs(models().EstadoRevision.en_revision, first.estado)

    def test_reject_needs_a_comment_and_returns_the_revision_to_draft(self) -> None:
        first = revision(self.new_document().id, 1)
        self.step(revisions().submit, first.id)
        for comment in (None, "", "  "):
            self.refused("ValidationError", revisions().reject, first.id, comment)
        self.refused("PermissionDenied", revisions().reject, first.id, "No", who=actor(OPERATIVO))
        self.step(revisions().reject, first.id, " Falta el alcance ", who=actor(AUDITOR))
        self.assertEqual((models().EstadoRevision.borrador, "Falta el alcance"),
                         (first.estado, first.review_comment))
        self.step(revisions().edit_draft, first.id, {"content": "Con alcance"})
        self.step(revisions().submit, first.id)
        self.assertIs(models().EstadoRevision.en_revision, first.estado)


class PublishTestCase(RevisionBase):
    def test_publish_makes_an_approved_revision_effective_from_the_given_date(self) -> None:
        first = revision(self.new_document().id, 1)
        self.step(revisions().submit, first.id)
        self.refused("ValidationError", revisions().publish, first.id, today=TODAY)
        self.step(revisions().approve, first.id, approver_id=self.eva, today=TODAY)
        self.refused("PermissionDenied", revisions().publish, first.id, who=actor(OPERATIVO),
                     today=TODAY)
        self.step(revisions().publish, first.id, today=LATER, who=actor(AUDITOR))
        self.assertEqual((models().EstadoRevision.vigente, LATER, None),
                         (first.estado, first.effective_from, first.obsolete_from))

    def test_publishing_obsoletes_the_previous_effective_revision(self) -> None:
        doc, first = self.in_force()
        self.drafted(doc)
        second = put_in_force(actor(ADMIN), doc.id, self.ana, LATER)
        R = models().EstadoRevision
        self.assertEqual((R.obsoleto, TODAY, LATER),
                         (first.estado, first.effective_from, first.obsolete_from))
        self.assertEqual((R.vigente, LATER), (second.estado, second.effective_from))
        self.assertEqual([second, first], revisions().list_(db.session, actor(ADMIN), doc.id))


class WithdrawTestCase(RevisionBase):
    def test_withdraw_is_for_administrators_and_needs_a_reason(self) -> None:
        doc, first = self.in_force()
        for role in (AUDITOR, OPERATIVO):
            self.refused("PermissionDenied", revisions().withdraw, doc.id, "Motivo",
                         who=actor(role), today=TODAY)
        for reason in (None, "", "  "):
            self.refused("ValidationError", revisions().withdraw, doc.id, reason, today=TODAY)
        self.assertEqual((None, models().EstadoRevision.vigente), (doc.withdrawn_at, first.estado))

    def test_withdraw_obsoletes_the_effective_revision_and_records_who_and_when(self) -> None:
        doc, first = self.in_force()
        luis = new_person("Luis", user_id=7)
        self.step(revisions().withdraw, doc.id, " Sustituido por MC-010 ", today=LATER)
        self.assertEqual((LATER, "Sustituido por MC-010", luis),
                         (doc.withdrawn_at, doc.withdrawn_reason, doc.withdrawn_by_id))
        self.assertEqual((models().EstadoRevision.obsoleto, LATER),
                         (first.estado, first.obsolete_from))
        self.assertEqual([("document_revisions", first.id, "update"),
                          ("documents", doc.id, "update")], self.rows()[-2:])

    def test_a_withdrawn_document_is_read_only(self) -> None:
        doc, _first = self.in_force()
        draft = self.drafted(doc)
        self.step(revisions().withdraw, doc.id, "Obsoleto", today=LATER)
        writes = (
            (documents().update, doc.id, {"title": "Otro"}),
            (revisions().start_draft, doc.id, {"author_id": self.eva}),
            (revisions().edit_draft, draft.id, {"content": "Otro"}),
            (revisions().submit, draft.id),
            (revisions().withdraw, doc.id, "Otra vez"),
        )
        for write, *args in writes:
            with self.subTest(write=write.__name__):
                kwargs = {"today": LATER} if write is revisions().withdraw else {}
                message = self.refused("ValidationError", write, *args, **kwargs)
                self.assertEqual(revisions().WITHDRAWN, message)
        self.assertEqual(("Manual de calidad", models().EstadoRevision.borrador),
                         (doc.title, draft.estado))

    def test_documents_are_never_hard_deleted(self) -> None:
        from app.services.policy import Action, Resource, can

        self.assertFalse(hasattr(documents(), "delete"))
        for role in RoleEnum:
            for channel in ("web", "cli"):
                self.assertFalse(can(actor(role, channel), Action.DELETE, Resource.DOCUMENTS))


class VisibilityTestCase(RevisionBase):
    def setUp(self) -> None:
        super().setUp()
        self.doc, self.first = self.in_force()
        self.draft = self.drafted(self.doc)
        self.fresh = self.new_document(code="MC-002")  # only a draft

    def test_operativos_see_only_effective_revisions_of_documents_in_force(self) -> None:
        who = actor(OPERATIVO)
        self.assertEqual([self.doc], documents().list_(db.session, who))
        self.assertEqual(([self.doc], 1), documents().list_page(db.session, who))
        self.assertIs(self.doc, documents().get(db.session, who, self.doc.id))
        self.refused("NotFound", documents().get, self.fresh.id, who=who)
        self.assertEqual([self.first], revisions().list_(db.session, who, self.doc.id))
        self.assertIs(self.first, revisions().get(db.session, who, self.first.id))
        self.refused("NotFound", revisions().get, self.draft.id, who=who)
        self.assertEqual({self.doc.id: self.first},
                         revisions().effective(db.session, who, [self.doc.id, self.fresh.id]))

    def test_administrators_and_auditors_see_every_revision(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                who = actor(role)
                self.assertEqual([self.doc, self.fresh], documents().list_(db.session, who))
                self.assertEqual([self.draft, self.first],
                                 revisions().list_(db.session, who, self.doc.id))
                self.assertIs(self.draft, revisions().get(db.session, who, self.draft.id))

    def test_a_withdrawn_document_leaves_operativos_with_nothing_to_read(self) -> None:
        self.step(revisions().withdraw, self.doc.id, "Obsoleto", today=LATER)
        self.assertEqual([], documents().list_(db.session, actor(OPERATIVO)))
        self.assertEqual([], revisions().list_(db.session, actor(OPERATIVO), self.doc.id))


class AuditAndLockTestCase(RevisionBase):
    def test_each_transition_is_audited_and_stamped(self) -> None:
        first = revision(self.new_document().id, 1)
        steps = (
            (revisions().submit, (), {}, "En revisión"),
            (revisions().reject, ("Revisar",), {}, "Borrador"),
            (revisions().submit, (), {}, "En revisión"),
            (revisions().approve, (), {"approver_id": self.eva, "today": TODAY}, "Aprobado"),
            (revisions().publish, (), {"today": TODAY}, "Vigente"),
        )
        for user_id, (write, args, kwargs, label) in enumerate(steps, start=20):
            with self.subTest(write=write.__name__, to=label):
                self.step(write, first.id, *args, who=actor(ADMIN, user_id=user_id), **kwargs)
                row = self.audit_rows()[-1]
                self.assertEqual(("document_revisions", first.id, "update", label),
                                 (row.entity_type, row.entity_id, row.action, row.after["estado"]))
                self.assertEqual(user_id, first.updated_by_id)

    def test_every_write_locks_the_document_row_first(self) -> None:
        doc, first = self.in_force()
        writes = (
            lambda: revisions().start_draft(db.session, actor(ADMIN), doc.id, {"author_id": self.eva}),
            lambda: revisions().edit_draft(db.session, actor(ADMIN), revision(doc.id, 2).id,
                                           {"content": "Nuevo", "change_summary": "Cambio"}),
            lambda: revisions().submit(db.session, actor(ADMIN), revision(doc.id, 2).id),
            lambda: revisions().reject(db.session, actor(ADMIN), revision(doc.id, 2).id, "No"),
            lambda: revisions().submit(db.session, actor(ADMIN), revision(doc.id, 2).id),
            lambda: revisions().approve(db.session, actor(ADMIN), revision(doc.id, 2).id,
                                        approver_id=self.ana, today=TODAY),
            lambda: revisions().publish(db.session, actor(ADMIN), revision(doc.id, 2).id,
                                        today=TODAY),
            lambda: documents().update(db.session, actor(ADMIN), doc.id, {"title": "Nuevo"}),
            lambda: revisions().withdraw(db.session, actor(ADMIN), doc.id, "Fin", today=TODAY),
        )
        for number, write in enumerate(writes):
            with self.subTest(write=number), patch.object(
                revisions(), "lock_document", wraps=revisions().lock_document
            ) as spy:
                write()
                db.session.commit()
                spy.assert_called_once()
                self.assertEqual(doc.id, spy.call_args.args[1])
        self.assertIsNotNone(first.obsolete_from)


def stored_file(name: str = "a" * 32, **overrides):
    """A file record as ``document_files.store`` returns it (no file on disk needed)."""
    from app.services.document_files import StoredFile

    values = {"stored_name": name, "display_name": "Plan.pdf", "size": 1234,
              "sha256": "f" * 64, "mime": "application/pdf"} | overrides
    return StoredFile(**values)


def attachment(rev):
    return tuple(getattr(rev, name) for name in ATTACHMENT_COLUMNS)


class AttachmentTestCase(RevisionBase):
    def test_attach_records_the_file_and_returns_the_one_it_replaces(self) -> None:
        doc, _first = self.in_force()
        draft = self.drafted(doc)
        self.assertIsNone(self.step(revisions().attach, draft.id, stored_file()))
        self.assertEqual(("Plan.pdf", "a" * 32, 1234, "f" * 64, "application/pdf"),
                         attachment(draft))
        audited = self.audit_rows()[-1]
        self.assertEqual(("document_revisions", draft.id, "update"),
                         (audited.entity_type, audited.entity_id, audited.action))
        self.assertEqual({"attachment_name": "Plan.pdf", "attachment_path": "a" * 32,
                          "attachment_size": 1234, "attachment_sha256": "f" * 64,
                          "attachment_mime": "application/pdf"},
                         {k: v for k, v in audited.after.items() if k in ATTACHMENT_COLUMNS})
        replaced = self.step(revisions().attach, draft.id,
                             stored_file("b" * 32, display_name="Plan v2.docx", size=9))
        self.assertEqual("a" * 32, replaced)
        self.assertEqual(("Plan v2.docx", "b" * 32, 9), attachment(draft)[:3])

    def test_revision_1_of_a_new_document_takes_an_attachment_too(self) -> None:
        first = revision(self.new_document().id, 1)
        self.step(revisions().attach, first.id, stored_file(), who=actor(AUDITOR))
        self.assertEqual("a" * 32, first.attachment_path)

    def test_detach_clears_the_attachment_and_returns_its_stored_name(self) -> None:
        first = revision(self.new_document().id, 1)
        self.step(revisions().attach, first.id, stored_file())
        self.assertEqual("a" * 32, self.step(revisions().detach, first.id))
        self.assertEqual((None,) * 5, attachment(first))
        written = len(self.audit_rows())
        self.assertIsNone(self.step(revisions().detach, first.id))
        self.assertEqual(written, len(self.audit_rows()), "detaching nothing writes nothing")

    def test_only_drafts_of_active_documents_change_their_attachment(self) -> None:
        doc, first = self.in_force()
        self.step(revisions().attach, self.drafted(doc).id, stored_file("c" * 32))
        for write in (revisions().attach, revisions().detach):
            args = (stored_file(),) if write is revisions().attach else ()
            with self.subTest(write=write.__name__, state="vigente"):
                self.assertEqual(revisions().IMMUTABLE,
                                 self.refused("ValidationError", write, first.id, *args))
        submitted = revision(doc.id, 2)
        self.step(revisions().submit, submitted.id)
        self.assertEqual(revisions().NOT_A_DRAFT,
                         self.refused("ValidationError", revisions().attach, submitted.id,
                                      stored_file()))
        self.step(revisions().withdraw, doc.id, "Sustituido", today=LATER)
        self.assertEqual(revisions().WITHDRAWN,
                         self.refused("ValidationError", revisions().detach, submitted.id))
        self.assertEqual("c" * 32, submitted.attachment_path)
        self.assertIsNone(first.attachment_path)

    def test_operativos_never_attach_or_detach(self) -> None:
        first = revision(self.new_document().id, 1)
        for write, args in ((revisions().attach, (stored_file(),)), (revisions().detach, ())):
            with self.subTest(write=write.__name__):
                self.refused("PermissionDenied", write, first.id, *args, who=actor(OPERATIVO))
        self.assertIsNone(first.attachment_path)

    def test_a_malformed_file_record_is_a_programming_error(self) -> None:
        first = revision(self.new_document().id, 1)
        for bad in (stored_file("../etc/passwd"), stored_file(size=0), "a" * 32, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    revisions().attach(db.session, actor(ADMIN), first.id, bad)
                db.session.rollback()

    def test_a_new_draft_starts_without_the_effective_attachment(self) -> None:
        doc = self.new_document()
        self.step(revisions().attach, revision(doc.id, 1).id, stored_file())
        effective = put_in_force(actor(ADMIN), doc.id, self.eva)
        draft = self.drafted(doc)
        self.assertEqual("a" * 32, effective.attachment_path)
        self.assertEqual((None,) * 5, attachment(draft))

    def test_two_revisions_never_share_a_stored_file(self) -> None:
        doc, _first = self.in_force()
        self.step(revisions().attach, self.drafted(doc).id, stored_file())
        other = self.new_document(code="MC-002")
        with self.assertRaises(errors().Conflict):
            revisions().attach(db.session, actor(ADMIN), revision(other.id, 1).id, stored_file())
        db.session.rollback()


class DiscardTestCase(RevisionBase):
    def test_discard_deletes_a_later_draft_and_returns_its_stored_name(self) -> None:
        doc, first = self.in_force()
        draft = self.drafted(doc)
        draft_id = draft.id
        self.step(revisions().attach, draft_id, stored_file())
        self.assertEqual("a" * 32, self.step(revisions().discard_draft, draft_id,
                                             who=actor(AUDITOR)))
        self.assertEqual([first], revisions().list_(db.session, actor(ADMIN), doc.id))
        deleted = self.audit_rows()[-1]
        self.assertEqual(("document_revisions", draft_id, "delete", None),
                         (deleted.entity_type, deleted.entity_id, deleted.action, deleted.after))
        self.assertEqual(("Plan.pdf", 1234, "f" * 64, 2),
                         tuple(deleted.before[k] for k in ("attachment_name", "attachment_size",
                                                           "attachment_sha256", "numero")))
        self.assertEqual(2, self.drafted(doc).numero, "the number is free again")

    def test_a_rejected_draft_without_attachment_is_discarded(self) -> None:
        doc, _first = self.in_force()
        draft = self.drafted(doc)
        self.step(revisions().submit, draft.id)
        self.step(revisions().reject, draft.id, "Rehacer")
        self.assertIsNone(self.step(revisions().discard_draft, draft.id))

    def test_the_only_revision_of_a_document_never_in_force_is_kept(self) -> None:
        first = revision(self.new_document().id, 1)
        self.assertEqual(revisions().FIRST_DRAFT,
                         self.refused("ValidationError", revisions().discard_draft, first.id))
        self.assertIsNotNone(db.session.get(models().DocumentRevision, first.id))

    def test_discard_refuses_other_states_withdrawn_documents_and_operativos(self) -> None:
        doc, first = self.in_force()
        draft = self.drafted(doc)
        self.refused("PermissionDenied", revisions().discard_draft, draft.id,
                     who=actor(OPERATIVO))
        self.assertEqual(revisions().IMMUTABLE,
                         self.refused("ValidationError", revisions().discard_draft, first.id))
        self.step(revisions().submit, draft.id)
        self.assertEqual(revisions().NOT_A_DRAFT,
                         self.refused("ValidationError", revisions().discard_draft, draft.id))
        self.step(revisions().withdraw, doc.id, "Baja", today=LATER)
        self.assertEqual(revisions().WITHDRAWN,
                         self.refused("ValidationError", revisions().discard_draft, draft.id))
        self.refused("NotFound", revisions().discard_draft, 999)
        self.assertEqual(2, len(revisions().list_(db.session, actor(ADMIN), doc.id)))


class WorkflowGuardsTestCase(RevisionBase):
    def test_workflow_dates_refuse_a_datetime(self) -> None:
        doc = self.new_document()
        first = revision(doc.id, 1)
        moment = datetime(2026, 10, 10, 9, 30)
        self.step(revisions().submit, first.id)
        with self.assertRaises(ValueError):
            revisions().approve(db.session, actor(ADMIN), first.id, approver_id=self.eva,
                                today=moment)
        db.session.rollback()
        self.step(revisions().approve, first.id, approver_id=self.eva, today=TODAY)
        for write, args in ((revisions().publish, (first.id,)),
                            (revisions().withdraw, (doc.id, "Baja"))):
            with self.subTest(write=write.__name__):
                with self.assertRaises(ValueError):
                    write(db.session, actor(ADMIN), *args, today=moment)
                db.session.rollback()
        self.assertEqual((models().EstadoRevision.aprobado, TODAY, None),
                         (first.estado, first.approved_at, doc.withdrawn_at))

    def test_an_integrity_error_on_a_revision_write_is_a_conflict(self) -> None:
        doc, _first = self.in_force()
        draft = self.drafted(doc)
        clash = IntegrityError("INSERT", {}, Exception("duplicate key"))
        writes = (
            ("start_draft", revisions().start_draft, self.new_in_force_id(),
             {"author_id": self.eva}),
            ("submit", revisions().submit, draft.id),
            ("attach", revisions().attach, draft.id, stored_file()),
            ("discard_draft", revisions().discard_draft, draft.id),
        )
        for name, write, *args in writes:
            with self.subTest(write=name):
                with patch.object(db.session, "flush", side_effect=clash):
                    message = self.refused("Conflict", write, *args)
                self.assertEqual(revisions().CONFLICT, message)
                self.assertEqual(
                    "Otra revisión del documento ha cambiado a la vez; vuelve a intentarlo.",
                    message)

    def new_in_force_id(self) -> int:
        doc = self.new_document(code="MC-077")
        put_in_force(actor(ADMIN), doc.id, self.eva)
        return doc.id


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class ConcurrentDraftTestCase(unittest.TestCase):
    """Two drafts of one document race on PostgreSQL; the document's row lock decides."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        with self.app.app_context():
            db.create_all()
            self.ana, eva = new_person("Ana"), new_person("Eva")
            self.doc_id = create_document(SYSTEM, self.ana).id
            put_in_force(SYSTEM, self.doc_id, eva)

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()

    def test_a_draft_started_meanwhile_waits_and_is_refused(self) -> None:
        flushed, release, outcomes = threading.Event(), threading.Event(), {}

        def run(name: str, hold: bool) -> None:
            with self.app.app_context():
                try:
                    revisions().start_draft(db.session, SYSTEM, self.doc_id,
                                            {"author_id": self.ana})
                    if hold:
                        flushed.set()
                        release.wait(10)
                    db.session.commit()
                    outcomes[name] = "done"
                except errors().DomainError as error:
                    outcomes[name] = error.message
                except Exception as error:  # surfaced by the assertion below
                    outcomes[name] = repr(error)
                finally:
                    flushed.set()
                    db.session.rollback()
                    db.session.remove()

        first = threading.Thread(target=run, args=("first", True))
        second = threading.Thread(target=run, args=("second", False))
        try:
            first.start()
            self.assertTrue(flushed.wait(10))
            second.start()
            second.join(0.5)
            waited = second.is_alive()
        finally:
            release.set()
            first.join(10)
            if second.ident is not None:
                second.join(10)
        self.assertEqual({"first": "done", "second": revisions().ONE_DRAFT}, outcomes)
        self.assertTrue(waited, "the second draft must wait for the first one's row lock")


class McpTestCase(McpDbCase):
    """Every role reads documents with their effective revision; nobody writes them."""

    def setUp(self) -> None:
        super().setUp()
        ana, eva = new_person("Ana"), new_person("Eva")
        self.doc_id = create_document(SYSTEM, ana).id
        put_in_force(SYSTEM, self.doc_id, eva)
        draft = revisions().start_draft(db.session, SYSTEM, self.doc_id, {"author_id": eva})
        revisions().edit_draft(db.session, SYSTEM, draft.id, {"content": "Texto borrador"})
        db.session.commit()
        self.fresh_id = create_document(SYSTEM, ana, code="MC-002").id

    async def test_every_role_reads_the_effective_revision_and_never_a_draft(self) -> None:
        effective = {"numero": 1, "content": "Texto inicial", "effective_from": "2026-10-10",
                     "legacy_version": None}
        for role, total in ((ADMIN, 2), (AUDITOR, 2), (OPERATIVO, 1)):
            with self.subTest(role=role.name):
                listed = await self.call(mcp_actor(role), "qms_list", {"module": "documentos"})
                self.assertFalse(listed.is_error, listed.content)
                page = listed.structured_content
                self.assertEqual(total, page["total"])
                items = {item["id"]: item for item in page["items"]}
                self.assertEqual(effective, items[self.doc_id]["effective_revision"])
                got = await self.call(mcp_actor(role), "qms_get",
                                      {"module": "documentos", "id": self.doc_id})
                self.assertEqual(items[self.doc_id], got.structured_content)
                self.assertNotIn("Texto borrador", json.dumps(page))
        admin = await self.call(mcp_actor(ADMIN), "qms_get",
                                {"module": "documentos", "id": self.fresh_id})
        self.assertIsNone(admin.structured_content["effective_revision"])
        hidden = await self.call(mcp_actor(OPERATIVO), "qms_get",
                                 {"module": "documentos", "id": self.fresh_id})
        self.assertTrue(hidden.is_error)

    async def test_document_writes_are_refused_for_every_role(self) -> None:
        written = db.session.query(AuditLog).count()
        data = {"title": "Nuevo", "code": "MC-009", "category": "OTRO", "owner_id": 1,
                "author_id": 1, "content": "Texto"}
        for role in RoleEnum:
            with self.subTest(role=role.name):
                for tool, arguments in (
                    ("qms_create", {"module": "documentos", "data": data}),
                    ("qms_update", {"module": "documentos", "id": self.doc_id,
                                    "data": {"title": "Cambiado"}}),
                ):
                    result = await self.call(mcp_actor(role), tool, arguments)
                    self.assertTrue(result.is_error, tool)
                    self.assertIn("No tienes permiso", result.content[0].text)
                described = await self.call(mcp_actor(role), "qms_modules")
                modules = {m["slug"]: m for m in described.structured_content["modules"]}
                self.assertEqual({"read": True, "create": False, "update": False},
                                 modules["documentos"]["permissions"])
                self.assertEqual([], modules["documentos"]["fields"])
        db.session.expire_all()
        self.assertEqual(written, db.session.query(AuditLog).count())


if __name__ == "__main__":
    unittest.main()
