"""Training service, and through it the generic CRUD helper's contract.

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
from app.models import Capacitacion, RoleEnum

ADMIN, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.OPERATIVO


def service():
    from app.services import training

    return training


def crud():
    from app.services import crud as helper

    return helper


VALID = {
    "tema": "Seguridad",
    "fecha": date(2026, 10, 1),
    "personal": "Ana",
    "duracion_horas": 4,
    "evaluacion_final": "Apto",
}


class TrainingBase(ServiceBase):
    def seed(self, **overrides) -> Capacitacion:
        result = db.session.execute(Capacitacion.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(Capacitacion, result.inserted_primary_key[0])

    def create(self, who=None, **overrides) -> Capacitacion:
        record = service().create(db.session, who or actor(), VALID | overrides)
        db.session.commit()
        return record


class ReadTestCase(TrainingBase):
    def test_every_role_reads_but_a_token_without_read_scope_does_not(self) -> None:
        record = self.seed()
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.assertIs(record, service().get(db.session, actor(role), record.id))
        for call in (lambda: service().get(db.session, actor(scopes={"write"}), record.id),
                     lambda: service().list_(db.session, actor(scopes={"write"}))):
            with self.assertRaises(errors().PermissionDenied):
                call()

    def test_get_missing_raises_not_found_with_the_spec_message(self) -> None:
        with self.assertRaises(errors().NotFound) as caught:
            service().get(db.session, actor(), 999)
        self.assertEqual("Capacitación no encontrada.", caught.exception.message)

    def test_list_orders_newest_first_and_filters_combine_with_and(self) -> None:
        a = self.seed(tema="Calidad", fecha=date(2026, 9, 1), personal="Ana")
        b = self.seed(tema="Seguridad", fecha=date(2026, 10, 1), personal="Eva")
        c = self.seed(tema="Seguridad", fecha=date(2026, 10, 1), personal="Ana")

        def ids(**filters):
            return [x.id for x in service().list_(db.session, actor(), **filters)]

        self.assertEqual([c.id, b.id, a.id], ids())
        self.assertEqual([c.id, b.id], ids(tema="SEGUR"))
        self.assertEqual([b.id], ids(personal="eva"))
        self.assertEqual([a.id], ids(fecha=date(2026, 9, 1)))
        self.assertEqual([c.id], ids(tema="seguridad", personal="ana"))
        self.assertEqual([], ids(tema="calidad", personal="eva"))

    def test_list_page_clamps_page_and_size_and_reports_the_total(self) -> None:
        for n in range(5):
            self.seed(tema=f"T{n}")
        spec, who = service().SPEC, actor()
        items, total = crud().list_page(spec, db.session, who, page=2, per_page=2)
        self.assertEqual((5, 2), (total, len(items)))
        items, total = crud().list_page(spec, db.session, who, page=9, per_page=2)
        self.assertEqual((5, []), (total, items))
        self.assertEqual(2, len(crud().list_page(spec, db.session, who, page=0, per_page=2)[0]))
        self.assertEqual(5, len(crud().list_page(spec, db.session, who, per_page=0)[0]))


class CreateTestCase(TrainingBase):
    def test_create_stamps_and_audits_the_full_snapshot(self) -> None:
        record = self.create(actor(user_id=7))
        self.assertEqual((7, 7), (record.created_by_id, record.updated_by_id))
        row = self.audit_rows()[-1]
        self.assertEqual(("capacitaciones", record.id, "create"), (row.entity_type, row.entity_id, row.action))
        self.assertIsNone(row.before)
        self.assertEqual("Seguridad", row.after["tema"])

    def test_text_is_trimmed_and_blank_optional_fields_become_none(self) -> None:
        record = self.create(tema="  Calidad ", duracion_horas=None, evaluacion_final=" ")
        self.assertEqual(("Calidad", None, None), (record.tema, record.duracion_horas, record.evaluacion_final))

    def test_optional_fields_may_be_absent(self) -> None:
        data = {k: VALID[k] for k in ("tema", "fecha", "personal")}
        record = service().create(db.session, actor(), data)
        db.session.commit()
        self.assertIsNone(record.duracion_horas)

    def test_invalid_data_raises_validation_error_and_writes_nothing(self) -> None:
        cases = {
            "blank topic": VALID | {"tema": " "},
            "topic too long": VALID | {"tema": "x" * 101},
            "grade too long": VALID | {"evaluacion_final": "x" * 21},
            "missing person": {k: v for k, v in VALID.items() if k != "personal"},
            "missing date": {k: v for k, v in VALID.items() if k != "fecha"},
            "date as text": VALID | {"fecha": "2026-10-01"},
            "negative hours": VALID | {"duracion_horas": -1},
            "hours as text": VALID | {"duracion_horas": "4"},
            "hours as bool": VALID | {"duracion_horas": True},
            "unknown field": VALID | {"created_by_id": 1},
            "id is not writable": VALID | {"id": 99},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        self.assertEqual(0, db.session.query(Capacitacion).count())
        self.assertEqual([], self.audit_rows())

    def test_a_scoped_actor_without_write_is_denied(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(scopes={"read"}), VALID)

    def test_integrity_error_becomes_conflict(self) -> None:
        with patch.object(db.session, "flush", side_effect=IntegrityError("s", {}, Exception())):
            with self.assertRaises(errors().Conflict):
                service().create(db.session, actor(), VALID)


class UpdateTestCase(TrainingBase):
    def test_update_changes_fields_stamps_and_audits_changed_fields_only(self) -> None:
        record = self.create(actor(user_id=7))
        service().update(db.session, actor(user_id=8), record.id, {"tema": "Calidad"})
        db.session.commit()
        self.assertEqual(("Calidad", 7, 8), (record.tema, record.created_by_id, record.updated_by_id))
        row = self.audit_rows()[-1]
        self.assertEqual("Seguridad", row.before["tema"])
        self.assertEqual("Calidad", row.after["tema"])
        self.assertNotIn("personal", row.after)

    def test_update_without_changes_writes_no_audit_row_and_keeps_stamps(self) -> None:
        record = self.create(actor(user_id=7))
        stamps = (record.updated_at, record.updated_by_id)
        service().update(db.session, actor(user_id=8), record.id, VALID | {"evaluacion_final": "Apto"})
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
        self.assertEqual(stamps, (record.updated_at, record.updated_by_id))

    def test_update_validates_and_leaves_the_record_untouched(self) -> None:
        record = self.create()
        for data in ({"tema": " "}, {"fecha": None}, {"duracion_horas": -2}, {"updated_by_id": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(), record.id, data)
                db.session.rollback()
        db.session.refresh(record)
        self.assertEqual(("Seguridad", 1), (record.tema, len(self.audit_rows())))

    def test_missing_scope_and_integrity_error(self) -> None:
        record = self.create()
        with self.assertRaises(errors().NotFound):
            service().update(db.session, actor(), 999, {"tema": "X"})
        with self.assertRaises(errors().PermissionDenied):
            service().update(db.session, actor(scopes={"read"}), record.id, {"tema": "X"})
        with patch.object(db.session, "flush", side_effect=IntegrityError("s", {}, Exception())):
            with self.assertRaises(errors().Conflict):
                service().update(db.session, actor(), record.id, {"tema": "X"})


class DeleteTestCase(TrainingBase):
    def test_delete_keeps_a_before_snapshot(self) -> None:
        record = self.create()
        record_id = record.id
        service().delete(db.session, actor(ADMIN), record_id)
        db.session.commit()
        self.assertIsNone(db.session.get(Capacitacion, record_id))
        row = self.audit_rows()[-1]
        self.assertEqual(("delete", record_id, None), (row.action, row.entity_id, row.after))
        self.assertEqual("Seguridad", row.before["tema"])

    def test_mcp_channel_and_read_only_tokens_never_delete(self) -> None:
        record = self.create()
        for who in (actor(ADMIN, channel="mcp"), actor(ADMIN, scopes={"read"})):
            with self.assertRaises(errors().PermissionDenied):
                service().delete(db.session, who, record.id)
        self.assertIsNotNone(db.session.get(Capacitacion, record.id))

    def test_delete_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().delete(db.session, actor(ADMIN), 999)


if __name__ == "__main__":
    unittest.main()
