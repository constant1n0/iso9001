"""Audit (Auditoria) service: authorization, validation, attribution and audit rows.

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
from app.models import Auditoria, EstadoAuditoriaEnum, RoleEnum

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
PENDIENTE = EstadoAuditoriaEnum.PENDIENTE
COMPLETADA = EstadoAuditoriaEnum.COMPLETADA


def service():
    from app.services import audits

    return audits


VALID = {
    "area_auditada": "Compras",
    "fecha": date(2026, 10, 1),
    "auditor": "Luis",
    "resultado": "Sin hallazgos",
    "accion_correctiva": "Ninguna",
}


class AuditServiceBase(ServiceBase):
    def seed(self, **overrides) -> Auditoria:
        values = {"estado": PENDIENTE} | VALID | overrides
        result = db.session.execute(Auditoria.__table__.insert().values(**values))
        db.session.commit()
        return db.session.get(Auditoria, result.inserted_primary_key[0])


class ReadTestCase(AuditServiceBase):
    def test_admin_and_auditor_read_and_operativo_is_denied(self) -> None:
        audit = self.seed()
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.assertIs(audit, service().get(db.session, actor(role), audit.id))
                service().list_page(db.session, actor(role))
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(OPERATIVO), audit.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_page(db.session, actor(OPERATIVO))

    def test_get_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(ADMIN), 999)

    def test_read_requires_the_read_scope(self) -> None:
        audit = self.seed()
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(ADMIN, scopes={"write"}), audit.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_page(db.session, actor(ADMIN, scopes={"write"}))

    def test_filters_combine_with_and(self) -> None:
        a = self.seed(area_auditada="Compras", auditor="Luis", fecha=date(2026, 9, 1))
        b = self.seed(
            area_auditada="Ventas", auditor="Eva", fecha=date(2026, 10, 1),
            estado=COMPLETADA,
        )
        who = actor(AUDITOR)

        def ids(**filters):
            return [x.id for x in service().list_page(db.session, who, **filters)[0]]

        self.assertEqual([a.id, b.id], ids())
        self.assertEqual([a.id], ids(area="compr"))
        self.assertEqual([b.id], ids(auditor="EVA"))
        self.assertEqual([b.id], ids(estado=COMPLETADA))
        self.assertEqual([b.id], ids(fecha_inicio=date(2026, 9, 15)))
        self.assertEqual([a.id], ids(fecha_fin=date(2026, 9, 15)))
        self.assertEqual([a.id], ids(fecha_inicio=date(2026, 8, 1), fecha_fin=date(2026, 9, 30)))
        self.assertEqual([], ids(area="compr", auditor="eva"))

    def test_pagination_returns_the_page_and_the_filtered_total(self) -> None:
        for n in range(5):
            self.seed(area_auditada=f"Area {n}")
        items, total = service().list_page(
            db.session, actor(ADMIN), page=2, per_page=2
        )
        self.assertEqual(5, total)
        self.assertEqual(["Area 2", "Area 3"], [a.area_auditada for a in items])
        items, total = service().list_page(
            db.session, actor(ADMIN), page=9, per_page=2
        )
        self.assertEqual((5, []), (total, items))
        items, _ = service().list_page(db.session, actor(ADMIN), page=0, per_page=2)
        self.assertEqual(["Area 0", "Area 1"], [a.area_auditada for a in items])


class WriteBase(AuditServiceBase):
    def create(self, who=None, **overrides) -> Auditoria:
        audit = service().create(db.session, who or actor(AUDITOR), VALID | overrides)
        db.session.commit()
        return audit


class CreateTestCase(WriteBase):
    def test_admin_and_auditor_create_with_an_after_snapshot_audit_row(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                audit = self.create(actor(role))
                row = self.audit_rows()[-1]
                self.assertEqual(
                    ("auditorias", audit.id, "create", "web"),
                    (row.entity_type, row.entity_id, row.action, row.channel),
                )
                self.assertIsNone(row.before)
                self.assertEqual("Compras", row.after["area_auditada"])
                self.assertEqual("Pendiente", row.after["estado"])
                self.assertEqual("2026-10-01", row.after["fecha"])

    def test_operativo_is_denied_and_nothing_is_written(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(OPERATIVO), VALID)
        self.assertEqual(0, db.session.query(Auditoria).count())
        self.assertEqual([], self.audit_rows())

    def test_create_stamps_attribution(self) -> None:
        audit = self.create(actor(AUDITOR, user_id=7))
        self.assertEqual((7, 7), (audit.created_by_id, audit.updated_by_id))
        self.assertIsNotNone(audit.created_at)
        self.assertEqual(audit.created_at, audit.updated_at)

    def test_state_defaults_to_pendiente_and_accepts_an_enum_or_its_name(self) -> None:
        self.assertIs(PENDIENTE, self.create().estado)
        self.assertIs(COMPLETADA, self.create(estado="COMPLETADA").estado)
        self.assertIs(
            EstadoAuditoriaEnum.CANCELADA,
            self.create(estado=EstadoAuditoriaEnum.CANCELADA).estado,
        )

    def test_text_is_trimmed_and_the_corrective_action_is_optional(self) -> None:
        data = {k: v for k, v in VALID.items() if k != "accion_correctiva"}
        audit = service().create(
            db.session, actor(AUDITOR), data | {"area_auditada": "  Compras "}
        )
        db.session.commit()
        self.assertEqual(("Compras", None), (audit.area_auditada, audit.accion_correctiva))

    def test_invalid_data_raises_validation_error_and_writes_nothing(self) -> None:
        cases = {
            "blank area": VALID | {"area_auditada": "  "},
            "area too long": VALID | {"area_auditada": "x" * 51},
            "auditor too long": VALID | {"auditor": "x" * 51},
            "missing auditor": {k: v for k, v in VALID.items() if k != "auditor"},
            "missing result": {k: v for k, v in VALID.items() if k != "resultado"},
            "missing date": {k: v for k, v in VALID.items() if k != "fecha"},
            "date as text": VALID | {"fecha": "2026-10-01"},
            "unknown state": VALID | {"estado": "ARCHIVADA"},
            "state by label": VALID | {"estado": "Pendiente"},
            "null state": VALID | {"estado": None},
            "text as number": VALID | {"resultado": 5},
            "unknown field": VALID | {"created_by_id": 1},
            "id is not writable": VALID | {"id": 99},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(AUDITOR), data)
                db.session.rollback()
        self.assertEqual(0, db.session.query(Auditoria).count())
        self.assertEqual([], self.audit_rows())

    def test_scoped_actor_without_write_is_denied(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(AUDITOR, scopes={"read"}), VALID)

    def test_integrity_error_becomes_conflict(self) -> None:
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().create(db.session, actor(AUDITOR), VALID)


class UpdateTestCase(WriteBase):
    def test_update_changes_fields_stamps_and_audits_changed_fields_only(self) -> None:
        audit = self.create(actor(AUDITOR, user_id=7))
        created_at = audit.created_at
        service().update(
            db.session, actor(ADMIN, user_id=8), audit.id,
            {"estado": "COMPLETADA", "auditor": "Eva"},
        )
        db.session.commit()
        self.assertEqual((COMPLETADA, "Eva"), (audit.estado, audit.auditor))
        self.assertEqual((7, 8, created_at), (audit.created_by_id, audit.updated_by_id, audit.created_at))
        row = self.audit_rows()[-1]
        self.assertEqual("update", row.action)
        self.assertEqual("Pendiente", row.before["estado"])
        self.assertEqual("Completada", row.after["estado"])
        self.assertEqual("Eva", row.after["auditor"])
        self.assertNotIn("resultado", row.after)
        self.assertEqual(8, row.after["updated_by_id"])

    def test_update_without_changes_writes_no_audit_row_and_keeps_stamps(self) -> None:
        audit = self.create(actor(AUDITOR, user_id=7))
        before = (audit.updated_at, audit.updated_by_id)
        service().update(db.session, actor(AUDITOR, user_id=8), audit.id, {"estado": "PENDIENTE"})
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
        self.assertEqual(before, (audit.updated_at, audit.updated_by_id))

    def test_operativo_is_denied_and_the_record_is_untouched(self) -> None:
        audit = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().update(db.session, actor(OPERATIVO), audit.id, {"auditor": "Eva"})
        db.session.refresh(audit)
        self.assertEqual("Luis", audit.auditor)

    def test_update_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().update(db.session, actor(ADMIN), 999, {"auditor": "Eva"})

    def test_update_validates_like_create_and_leaves_the_record_untouched(self) -> None:
        audit = self.create()
        for data in ({"estado": "ARCHIVADA"}, {"estado": None}, {"area_auditada": " "},
                     {"fecha": None}, {"auditor": "x" * 51}, {"updated_by_id": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(AUDITOR), audit.id, data)
                db.session.rollback()
        db.session.refresh(audit)
        self.assertEqual("Compras", audit.area_auditada)
        self.assertEqual(1, len(self.audit_rows()))

    def test_scoped_actor_without_write_cannot_update(self) -> None:
        audit = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().update(
                db.session, actor(AUDITOR, scopes={"read"}), audit.id, {"auditor": "Eva"}
            )

    def test_integrity_error_becomes_conflict(self) -> None:
        audit = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().update(db.session, actor(AUDITOR), audit.id, {"auditor": "Eva"})


class DeleteTestCase(WriteBase):
    def test_admin_and_auditor_delete_with_a_before_snapshot(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                audit = self.create()
                audit_id = audit.id
                service().delete(db.session, actor(role), audit_id)
                db.session.commit()
                self.assertIsNone(db.session.get(Auditoria, audit_id))
                row = self.audit_rows()[-1]
                self.assertEqual(("delete", audit_id), (row.action, row.entity_id))
                self.assertIsNone(row.after)
                self.assertEqual("Compras", row.before["area_auditada"])

    def test_operativo_is_denied_and_the_record_survives(self) -> None:
        audit = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(OPERATIVO), audit.id)
        self.assertIsNotNone(db.session.get(Auditoria, audit.id))
        self.assertEqual(1, len(self.audit_rows()))

    def test_mcp_channel_never_deletes_even_for_admin(self) -> None:
        audit = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, channel="mcp"), audit.id)
        self.assertIsNotNone(db.session.get(Auditoria, audit.id))

    def test_scoped_admin_without_write_is_denied(self) -> None:
        audit = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, scopes={"read"}), audit.id)

    def test_delete_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().delete(db.session, actor(ADMIN), 999)

    def test_integrity_error_becomes_conflict(self) -> None:
        audit = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("fk"))
        ):
            with self.assertRaises(errors().Conflict):
                service().delete(db.session, actor(ADMIN), audit.id)


if __name__ == "__main__":
    unittest.main()
