"""Empty optional text is stored as NULL, so an unchanged form edit is a no-op.

Web forms post an empty string for every blank optional field while the rows
hold NULL; without normalisation each unchanged edit looked like a change and
wrote an audit row and new stamps (review suggestion R3-audit-form-empty-values).
"""

from __future__ import annotations

import unittest

from test_audit_service import VALID as AUDIT_VALID, WriteBase as AuditWriteBase
from test_document_service import VALID as DOCUMENT_VALID, WriteBase as DocumentWriteBase
from test_nonconformity_service import VALID as NC_VALID, WriteBase as NcWriteBase
from test_nonconformity_service import actor, errors

from app.extensions import db
from app.models import RoleEnum
from app.services import audits, documents, fields, nonconformities


class FieldsTextTestCase(unittest.TestCase):
    def test_optional_blank_text_becomes_none_with_or_without_stripping(self) -> None:
        for raw in ("", "   ", "\n\t"):
            for strip in (True, False):
                with self.subTest(raw=raw, strip=strip):
                    self.assertIsNone(fields.text({"k": raw}, "k", strip=strip))

    def test_optional_text_keeps_content_and_required_text_still_rejects_blank(self) -> None:
        self.assertEqual("a", fields.text({"k": " a "}, "k"))
        self.assertEqual(" a ", fields.text({"k": " a "}, "k", strip=False))
        with self.assertRaises(errors().ValidationError):
            fields.text({"k": "  "}, "k", required=True)


class AuditsTestCase(AuditWriteBase):
    def test_blank_corrective_action_is_stored_as_none(self) -> None:
        audit = self.create(accion_correctiva="  ")
        self.assertIsNone(audit.accion_correctiva)

    def test_editing_with_blank_optional_text_writes_nothing(self) -> None:
        audit = self.create(accion_correctiva="")
        stamps = (audit.updated_at, audit.updated_by_id)
        audits.update(db.session, actor(RoleEnum.AUDITOR, user_id=8), audit.id,
                      AUDIT_VALID | {"accion_correctiva": ""})
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
        self.assertEqual(stamps, (audit.updated_at, audit.updated_by_id))


class DocumentsTestCase(DocumentWriteBase):
    def test_blank_approver_is_stored_as_none_and_a_blank_edit_writes_nothing(self) -> None:
        doc = self.create(approved_by=" ")
        self.assertIsNone(doc.approved_by)
        documents.update(db.session, actor(RoleEnum.ADMINISTRADOR), doc.id,
                         DOCUMENT_VALID | {"approved_by": ""})
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))


class NonconformitiesTestCase(NcWriteBase):
    def test_blank_optional_text_is_stored_as_none_and_a_blank_edit_writes_nothing(self) -> None:
        nc = self.create(responsable="", accion_correctiva="  ")
        self.assertEqual((None, None), (nc.responsable, nc.accion_correctiva))
        nonconformities.update(
            db.session, actor(), nc.id,
            NC_VALID | {"responsable": "", "accion_correctiva": ""},
        )
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))


if __name__ == "__main__":
    unittest.main()
