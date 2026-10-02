"""Document service: authorization (administrators only), validation, unique
code, attribution and audit rows.

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
from app.models import Document, DocumentCategory, RoleEnum

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
    "version": "1.0",
    "issued_date": date(2026, 10, 1),
    "approved_by": "Direccion",
    "content": "Contenido del manual",
}


class DocumentServiceBase(ServiceBase):
    def seed(self, **overrides) -> Document:
        values = {"category": MANUAL} | {k: v for k, v in VALID.items() if k != "category"} | overrides
        result = db.session.execute(Document.__table__.insert().values(**values))
        db.session.commit()
        return db.session.get(Document, result.inserted_primary_key[0])


class ReadTestCase(DocumentServiceBase):
    def test_only_admin_reads_and_other_roles_are_denied(self) -> None:
        doc = self.seed()
        self.assertIs(doc, service().get(db.session, actor(ADMIN), doc.id))
        self.assertEqual([doc], service().list_(db.session, actor(ADMIN)))
        for role in (AUDITOR, OPERATIVO):
            with self.subTest(role=role.name):
                with self.assertRaises(errors().PermissionDenied):
                    service().get(db.session, actor(role), doc.id)
                with self.assertRaises(errors().PermissionDenied):
                    service().list_(db.session, actor(role))

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
        row = self.audit_rows()[-1]
        self.assertEqual(
            ("documents", doc.id, "create", "web"),
            (row.entity_type, row.entity_id, row.action, row.channel),
        )
        self.assertIsNone(row.before)
        self.assertEqual("MC-001", row.after["code"])
        self.assertEqual("Manual de Calidad", row.after["category"])
        self.assertEqual("2026-10-01", row.after["issued_date"])

    def test_other_roles_are_denied_and_nothing_is_written(self) -> None:
        for role in (AUDITOR, OPERATIVO):
            with self.subTest(role=role.name):
                with self.assertRaises(errors().PermissionDenied):
                    service().create(db.session, actor(role), VALID)
        self.assertEqual(0, db.session.query(Document).count())
        self.assertEqual([], self.audit_rows())

    def test_create_stamps_attribution(self) -> None:
        doc = self.create(actor(ADMIN, user_id=7))
        self.assertEqual((7, 7), (doc.created_by_id, doc.updated_by_id))
        self.assertIsNotNone(doc.created_at)
        self.assertEqual(doc.created_at, doc.updated_at)

    def test_category_accepts_an_enum_or_its_name_and_text_is_trimmed(self) -> None:
        self.assertIs(MANUAL, self.create().category)
        doc = self.create(
            code="  PO-1 ", title="  Proc ", category=DocumentCategory.OTRO
        )
        self.assertEqual(("PO-1", "Proc", DocumentCategory.OTRO), (doc.code, doc.title, doc.category))

    def test_approved_by_is_optional_and_signature_is_not_writable(self) -> None:
        data = {k: v for k, v in VALID.items() if k != "approved_by"}
        doc = service().create(db.session, actor(ADMIN), data)
        db.session.commit()
        self.assertIsNone(doc.approved_by)
        with self.assertRaises(errors().ValidationError):
            service().create(db.session, actor(ADMIN), VALID | {"signature": "x"})

    def test_invalid_data_raises_validation_error_and_writes_nothing(self) -> None:
        cases = {
            "blank title": VALID | {"title": " "},
            "title too long": VALID | {"title": "x" * 151},
            "code too long": VALID | {"code": "x" * 51},
            "version too long": VALID | {"version": "x" * 11},
            "approved_by too long": VALID | {"approved_by": "x" * 101},
            "blank content": VALID | {"content": ""},
            "missing code": {k: v for k, v in VALID.items() if k != "code"},
            "missing version": {k: v for k, v in VALID.items() if k != "version"},
            "missing date": {k: v for k, v in VALID.items() if k != "issued_date"},
            "date as text": VALID | {"issued_date": "2026-10-01"},
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
        self.assertEqual(1, len(self.audit_rows()))

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
            {"version": "2.0", "category": "OTRO"},
        )
        db.session.commit()
        self.assertEqual(("2.0", DocumentCategory.OTRO), (doc.version, doc.category))
        self.assertEqual((7, 8, created_at), (doc.created_by_id, doc.updated_by_id, doc.created_at))
        row = self.audit_rows()[-1]
        self.assertEqual("update", row.action)
        self.assertEqual("1.0", row.before["version"])
        self.assertEqual("2.0", row.after["version"])
        self.assertEqual("Otro", row.after["category"])
        self.assertNotIn("content", row.after)
        self.assertEqual(8, row.after["updated_by_id"])

    def test_update_without_changes_writes_no_audit_row_and_keeps_stamps(self) -> None:
        doc = self.create(actor(ADMIN, user_id=7))
        before = (doc.updated_at, doc.updated_by_id)
        service().update(
            db.session, actor(ADMIN, user_id=8), doc.id, {"code": "MC-001", "version": "1.0"}
        )
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
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

    def test_other_roles_are_denied_and_the_record_is_untouched(self) -> None:
        doc = self.create()
        for role in (AUDITOR, OPERATIVO):
            with self.subTest(role=role.name):
                with self.assertRaises(errors().PermissionDenied):
                    service().update(db.session, actor(role), doc.id, {"title": "X"})
        db.session.refresh(doc)
        self.assertEqual("Manual de calidad", doc.title)

    def test_update_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().update(db.session, actor(ADMIN), 999, {"title": "X"})

    def test_update_validates_like_create_and_leaves_the_record_untouched(self) -> None:
        doc = self.create()
        for data in ({"category": "NADA"}, {"category": None}, {"title": " "},
                     {"issued_date": None}, {"code": "x" * 51}, {"signature": "s"},
                     {"updated_by_id": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(ADMIN), doc.id, data)
                db.session.rollback()
        db.session.refresh(doc)
        self.assertEqual("Manual de calidad", doc.title)
        self.assertEqual(1, len(self.audit_rows()))

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


class DeleteTestCase(WriteBase):
    def test_admin_deletes_with_a_before_snapshot(self) -> None:
        doc = self.create()
        doc_id = doc.id
        service().delete(db.session, actor(ADMIN), doc_id)
        db.session.commit()
        self.assertIsNone(db.session.get(Document, doc_id))
        row = self.audit_rows()[-1]
        self.assertEqual(("delete", doc_id), (row.action, row.entity_id))
        self.assertIsNone(row.after)
        self.assertEqual("MC-001", row.before["code"])

    def test_other_roles_are_denied_and_the_record_survives(self) -> None:
        doc = self.create()
        for role in (AUDITOR, OPERATIVO):
            with self.subTest(role=role.name):
                with self.assertRaises(errors().PermissionDenied):
                    service().delete(db.session, actor(role), doc.id)
        self.assertIsNotNone(db.session.get(Document, doc.id))
        self.assertEqual(1, len(self.audit_rows()))

    def test_mcp_channel_never_deletes_even_for_admin(self) -> None:
        doc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, channel="mcp"), doc.id)
        self.assertIsNotNone(db.session.get(Document, doc.id))

    def test_scoped_admin_without_write_is_denied(self) -> None:
        doc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, scopes={"read"}), doc.id)

    def test_delete_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().delete(db.session, actor(ADMIN), 999)

    def test_integrity_error_becomes_conflict(self) -> None:
        doc = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("fk"))
        ):
            with self.assertRaises(errors().Conflict):
                service().delete(db.session, actor(ADMIN), doc.id)


if __name__ == "__main__":
    unittest.main()
