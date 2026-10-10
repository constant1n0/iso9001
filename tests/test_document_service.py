"""Document service: authorization (every role reads, administrators and
auditors write, nobody deletes), validation, unique code, attribution and audit
rows. The revision workflow is tested in ``test_document_revisions``.

Read tests seed with a core insert; write tests go through the service with the
audit flush guard installed, so a write that forgets its audit row fails.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import Document, DocumentCategory, Person, RoleEnum

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
MANUAL = DocumentCategory.MANUAL_CALIDAD


def service():
    from app.services import documents

    return documents


VALID = {
    "title": "Manual de calidad",
    "code": "MC-001",
    "category": "MANUAL_CALIDAD",
    "owner_id": 1,  # the person every test seeds first
    "author_id": 1,
    "content": "Contenido del manual",
}


class DocumentServiceBase(ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        db.session.execute(Person.__table__.insert().values(nombre="Ana"))
        db.session.commit()

    def seed(self, **overrides) -> Document:
        values = {"title": VALID["title"], "code": VALID["code"], "category": MANUAL} | overrides
        result = db.session.execute(Document.__table__.insert().values(**values))
        db.session.commit()
        return db.session.get(Document, result.inserted_primary_key[0])


class ReadTestCase(DocumentServiceBase):
    def test_every_role_reads_but_operativos_only_documents_in_force(self) -> None:
        doc = self.seed()  # no revision in force
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.assertIs(doc, service().get(db.session, actor(role), doc.id))
                self.assertEqual([doc], service().list_(db.session, actor(role)))
        self.assertEqual([], service().list_(db.session, actor(OPERATIVO)))
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(OPERATIVO), doc.id)

    def test_get_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(ADMIN), 999)

    def test_read_requires_the_read_scope(self) -> None:
        doc = self.seed()
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(ADMIN, scopes={"write"}), doc.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_(db.session, actor(ADMIN, scopes={"write"}))

    def test_list_is_ordered_by_code(self) -> None:
        b = self.seed(code="B-2")
        a = self.seed(code="A-1")
        self.assertEqual([a, b], service().list_(db.session, actor(ADMIN)))


class WriteBase(DocumentServiceBase):
    def create(self, who=None, **overrides) -> Document:
        doc = service().create(db.session, who or actor(ADMIN), VALID | overrides)
        db.session.commit()
        return doc


class CreateTestCase(WriteBase):
    def test_admin_creates_with_an_after_snapshot_audit_row(self) -> None:
        doc = self.create()
        row = self.audit_rows()[0]  # then revision 1's row
        self.assertEqual(
            ("documents", doc.id, "create", "web"),
            (row.entity_type, row.entity_id, row.action, row.channel),
        )
        self.assertIsNone(row.before)
        self.assertEqual("MC-001", row.after["code"])
        self.assertEqual("Manual de Calidad", row.after["category"])
        self.assertEqual(1, row.after["owner_id"])
        self.assertNotIn("content", row.after)

    def test_operativos_are_denied_and_nothing_is_written(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(OPERATIVO), VALID)
        self.assertEqual(0, db.session.query(Document).count())
        self.assertEqual([], self.audit_rows())

    def test_create_stamps_attribution(self) -> None:
        doc = self.create(actor(AUDITOR, user_id=7))
        self.assertEqual((7, 7), (doc.created_by_id, doc.updated_by_id))
        self.assertIsNotNone(doc.created_at)
        self.assertEqual(doc.created_at, doc.updated_at)

    def test_category_accepts_an_enum_or_its_name_and_text_is_trimmed(self) -> None:
        self.assertIs(MANUAL, self.create().category)
        doc = self.create(
            code="  PO-1 ", title="  Proc ", category=DocumentCategory.OTRO
        )
        self.assertEqual(("PO-1", "Proc", DocumentCategory.OTRO), (doc.code, doc.title, doc.category))

    def test_next_review_date_is_optional_and_the_moved_columns_are_not_writable(self) -> None:
        doc = self.create(next_review_date=date(2027, 10, 1))
        self.assertEqual(date(2027, 10, 1), doc.next_review_date)
        self.assertIsNone(self.create(code="MC-002", next_review_date=None).next_review_date)
        for key in ("version", "approved_by", "signature", "issued_date", "withdrawn_at"):
            with self.subTest(key=key):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(ADMIN), VALID | {key: "x"})

    def test_invalid_data_raises_validation_error_and_writes_nothing(self) -> None:
        cases = {
            "blank title": VALID | {"title": " "},
            "title too long": VALID | {"title": "x" * 151},
            "code too long": VALID | {"code": "x" * 51},
            "blank content": VALID | {"content": ""},
            "missing code": {k: v for k, v in VALID.items() if k != "code"},
            "missing owner": {k: v for k, v in VALID.items() if k != "owner_id"},
            "no owner": VALID | {"owner_id": None},
            "unknown owner": VALID | {"owner_id": 999},
            "review date as text": VALID | {"next_review_date": "2027-10-01"},
            "unknown category": VALID | {"category": "NADA"},
            "category by label": VALID | {"category": "Manual de Calidad"},
            "null category": VALID | {"category": None},
            "unknown field": VALID | {"created_by_id": 1},
            "id is not writable": VALID | {"id": 99},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(ADMIN), data)
                db.session.rollback()
        self.assertEqual(0, db.session.query(Document).count())
        self.assertEqual([], self.audit_rows())

    def test_duplicate_code_raises_conflict_and_writes_no_audit_row(self) -> None:
        self.create()
        with self.assertRaises(errors().Conflict) as caught:
            service().create(db.session, actor(ADMIN), VALID | {"title": "Otro"})
        self.assertIn("código", str(caught.exception))
        db.session.rollback()
        self.assertEqual(1, db.session.query(Document).count())
        self.assertEqual(2, len(self.audit_rows()))

    def test_duplicate_code_is_also_caught_at_the_database(self) -> None:
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().create(db.session, actor(ADMIN), VALID)

    def test_scoped_actor_without_write_is_denied(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(ADMIN, scopes={"read"}), VALID)


class UpdateTestCase(WriteBase):
    def test_update_changes_fields_stamps_and_audits_changed_fields_only(self) -> None:
        doc = self.create(actor(ADMIN, user_id=7))
        created_at = doc.created_at
        service().update(
            db.session, actor(ADMIN, user_id=8), doc.id,
            {"title": "Manual 2", "category": "OTRO"},
        )
        db.session.commit()
        self.assertEqual(("Manual 2", DocumentCategory.OTRO), (doc.title, doc.category))
        self.assertEqual((7, 8, created_at), (doc.created_by_id, doc.updated_by_id, doc.created_at))
        row = self.audit_rows()[-1]
        self.assertEqual(("documents", "update"), (row.entity_type, row.action))
        self.assertEqual("Manual de calidad", row.before["title"])
        self.assertEqual("Manual 2", row.after["title"])
        self.assertEqual("Otro", row.after["category"])
        self.assertNotIn("code", row.after)
        self.assertEqual(8, row.after["updated_by_id"])

    def test_update_without_changes_writes_no_audit_row_and_keeps_stamps(self) -> None:
        doc = self.create(actor(ADMIN, user_id=7))
        before = (doc.updated_at, doc.updated_by_id)
        service().update(
            db.session, actor(ADMIN, user_id=8), doc.id, {"code": "MC-001", "owner_id": 1}
        )
        db.session.commit()
        self.assertEqual(2, len(self.audit_rows()))
        self.assertEqual(before, (doc.updated_at, doc.updated_by_id))

    def test_keeping_the_own_code_is_not_a_conflict_but_taking_another_is(self) -> None:
        doc = self.create()
        other = self.create(code="MC-002")
        service().update(db.session, actor(ADMIN), doc.id, {"code": "MC-001", "title": "Nuevo"})
        db.session.commit()
        with self.assertRaises(errors().Conflict):
            service().update(db.session, actor(ADMIN), other.id, {"code": "MC-001"})
        db.session.rollback()
        db.session.refresh(other)
        self.assertEqual("MC-002", other.code)

    def test_operativos_are_denied_and_the_record_is_untouched(self) -> None:
        doc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().update(db.session, actor(OPERATIVO), doc.id, {"title": "X"})
        db.session.refresh(doc)
        self.assertEqual("Manual de calidad", doc.title)

    def test_update_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().update(db.session, actor(ADMIN), 999, {"title": "X"})

    def test_update_validates_like_create_and_leaves_the_record_untouched(self) -> None:
        doc = self.create()
        for data in ({"category": "NADA"}, {"category": None}, {"title": " "},
                     {"owner_id": None}, {"code": "x" * 51}, {"content": "Otro"},
                     {"version": "2.0"}, {"updated_by_id": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(ADMIN), doc.id, data)
                db.session.rollback()
        db.session.refresh(doc)
        self.assertEqual("Manual de calidad", doc.title)
        self.assertEqual(2, len(self.audit_rows()))

    def test_scoped_actor_without_write_cannot_update(self) -> None:
        doc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().update(db.session, actor(ADMIN, scopes={"read"}), doc.id, {"title": "X"})

    def test_integrity_error_becomes_conflict(self) -> None:
        doc = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().update(db.session, actor(ADMIN), doc.id, {"title": "X"})


if __name__ == "__main__":
    unittest.main()
