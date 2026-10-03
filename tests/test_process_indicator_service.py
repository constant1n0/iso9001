"""Process and audit indicator services on the CRUD helper."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db


def processes():
    from app.services import process_operations

    return process_operations


def indicators():
    from app.services import audit_indicators

    return audit_indicators


class ProcessServiceTestCase(ServiceBase):
    def test_the_process_name_is_required_and_at_most_100_characters(self) -> None:
        for data in ({}, {"proceso": " "}, {"proceso": "x" * 101}, {"proceso": "P", "control_proveedor": 1}):
            with self.assertRaises(errors().ValidationError):
                processes().create(db.session, actor(), data)
            db.session.rollback()
        record = processes().create(db.session, actor(), {"proceso": "P", "no_conformidad": ""})
        self.assertEqual((False, None), (record.control_proveedor, record.no_conformidad))

    def test_a_duplicate_name_raises_conflict_on_create_and_update(self) -> None:
        processes().create(db.session, actor(), {"proceso": "Compras"})
        other = processes().create(db.session, actor(), {"proceso": "Ventas"})
        db.session.commit()
        with self.assertRaises(errors().Conflict) as caught:
            processes().create(db.session, actor(), {"proceso": "Compras"})
        self.assertEqual("Ya existe un proceso con ese nombre.", caught.exception.message)
        db.session.rollback()
        with self.assertRaises(errors().Conflict):
            processes().update(db.session, actor(), other.id_proceso, {"proceso": "Compras"})

    def test_updates_are_stamped_and_audited_and_missing_records_raise_not_found(self) -> None:
        record = processes().create(db.session, actor(), {"proceso": "Compras"})
        processes().update(db.session, actor(), record.id_proceso, {"criterio_calidad": "c"})
        self.assertEqual((7, ["create", "update"]), (record.updated_by_id, [r.action for r in self.audit_rows()]))
        with self.assertRaises(errors().NotFound) as caught:
            processes().delete(db.session, actor(), 999)
        self.assertEqual("Proceso de operación no encontrado.", caught.exception.message)


class IndicatorServiceTestCase(ServiceBase):
    def test_the_area_is_required_and_at_most_50_characters(self) -> None:
        for data in ({}, {"area_auditoria": " "}, {"area_auditoria": "x" * 51}):
            with self.assertRaises(errors().ValidationError):
                indicators().create(db.session, actor(), data)
            db.session.rollback()

    def test_the_date_accepts_datetimes_and_iso_text_and_stores_utc_naive(self) -> None:
        text = indicators().create(db.session, actor(), {"area_auditoria": "A", "fecha_auditoria": "2026-10-05T09:30:00"})
        aware = indicators().create(db.session, actor(), {
            "area_auditoria": "B", "fecha_auditoria": datetime(2026, 10, 5, 11, 30, tzinfo=timezone(offset=timedelta(hours=2)))})
        self.assertEqual(datetime(2026, 10, 5, 9, 30), text.fecha_auditoria)
        self.assertEqual(datetime(2026, 10, 5, 9, 30), aware.fecha_auditoria)
        for bad in ("mañana", 5, "2026-13-01"):
            with self.assertRaises(errors().ValidationError):
                indicators().create(db.session, actor(), {"area_auditoria": "C", "fecha_auditoria": bad})
            db.session.rollback()
        self.assertIsNotNone(indicators().create(db.session, actor(), {"area_auditoria": "D"}).fecha_auditoria)

    def test_an_unchanged_date_is_a_noop_and_changes_are_audited(self) -> None:
        record = indicators().create(db.session, actor(), {"area_auditoria": "A", "fecha_auditoria": "2026-10-05T09:30:00"})
        indicators().update(db.session, actor(), record.id_auditoria, {"fecha_auditoria": "2026-10-05T09:30:00"})
        indicators().update(db.session, actor(), record.id_auditoria, {"resultado": "ok"})
        self.assertEqual(["create", "update"], [r.action for r in self.audit_rows()])
        items, total = indicators().list_page(db.session, actor(), page=1, per_page=5)
        self.assertEqual((1, [record.id_auditoria]), (total, [r.id_auditoria for r in items]))
        with self.assertRaises(errors().NotFound) as caught:
            indicators().get(db.session, actor(), 999)
        self.assertEqual("Auditoría e indicador no encontrado.", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
