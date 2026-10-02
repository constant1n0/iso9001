"""Document service: authorization (administrators only), validation, unique
code, attribution and audit rows.

Read tests seed with a core insert; write tests go through the service with the
audit flush guard installed, so a write that forgets its audit row fails.
"""

from __future__ import annotations

from datetime import date


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
