"""Nonconformity service: the pilot every other module's service copies.

Runs against an in-memory database with the audit flush guard installed, so a
write that forgets its audit row fails the test instead of passing silently.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from test_audit import AuditDbBase

from app.extensions import db
from app.models import AuditLog, NoConformidad, RoleEnum
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)


def actor(role=OPERATIVO, channel="web", user_id=7, scopes=None) -> Actor:
    return Actor(
        user_id=user_id,
        label=f"user{user_id}",
        role=role,
        channel=channel,
        scopes=None if scopes is None else frozenset(scopes),
    )


def service():
    from app.services import nonconformities

    return nonconformities


def errors():
    from app.services import errors as domain_errors

    return domain_errors


VALID = {
    "descripcion": "Pieza fuera de tolerancia",
    "fecha_detectada": date(2026, 10, 1),
    "responsable": "Ana",
    "accion_correctiva": "Reajustar la maquina",
}


class ServiceBase(AuditDbBase):
    def setUp(self) -> None:
        super().setUp()
        from app.services import audit

        self.audit = audit
        self.addCleanup(audit.install_audit_guard(db.session, audit.AUDITED_MODELS))

    def seed(self, **overrides) -> NoConformidad:
        # Core insert: read tests must not depend on the write API, and core
        # statements bypass the ORM flush guard installed above.
        result = db.session.execute(
            NoConformidad.__table__.insert().values(**(VALID | overrides))
        )
        db.session.commit()
        return db.session.get(NoConformidad, result.inserted_primary_key[0])

    def audit_rows(self):
        return db.session.query(AuditLog).order_by(AuditLog.id).all()


class StateConstantTestCase(unittest.TestCase):
    def test_states_are_the_three_spanish_values(self) -> None:
        self.assertEqual(
            ("Abierta", "En proceso", "Cerrada"), service().ESTADOS_NO_CONFORMIDAD
        )
        self.assertEqual("Abierta", service().ESTADO_ABIERTA)
        self.assertEqual("Cerrada", service().ESTADO_CERRADA)

    def test_forms_reuse_the_service_constant(self) -> None:
        from app import forms

        self.assertIs(service().ESTADOS_NO_CONFORMIDAD, forms.ESTADOS_NO_CONFORMIDAD)


class ReadTestCase(ServiceBase):
    def test_get_returns_the_record_for_every_role(self) -> None:
        nc = self.seed()
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.assertIs(nc, service().get(db.session, actor(role), nc.id))

    def test_get_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(), 999)

    def test_read_requires_the_read_scope(self) -> None:
        nc = self.seed()
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(scopes={"write"}), nc.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_(db.session, actor(scopes={"write"}))

    def test_list_orders_newest_first_and_filters_like_the_web_list(self) -> None:
        old = self.seed(descripcion="Ruido en Linea", fecha_detectada=date(2026, 9, 1))
        new = self.seed(
            descripcion="Fuga de aceite", fecha_detectada=date(2026, 10, 1), estado="Cerrada"
        )
        who = actor()
        self.assertEqual([new, old], service().list_(db.session, who))
        self.assertEqual([old], service().list_(db.session, who, descripcion="linea"))
        self.assertEqual([new], service().list_(db.session, who, estado="Cerrada"))
        self.assertEqual([old], service().list_(db.session, who, fecha_detectada=date(2026, 9, 1)))
        self.assertEqual([], service().list_(db.session, who, descripcion="x", estado="Abierta"))

    def test_available_states_append_legacy_values_after_the_fixed_ones(self) -> None:
        self.seed(estado="Abierta")
        self.seed_legacy_states()
        self.assertEqual(
            ["Abierta", "En proceso", "Cerrada", "Pendiente", "Zeta"],
            service().available_states(db.session, actor()),
        )

    def seed_legacy_states(self) -> None:
        # Legacy rows predate the service; a core insert bypasses ORM flush hooks.
        db.session.execute(
            NoConformidad.__table__.insert(),
            [
                {"descripcion": "old", "fecha_detectada": date(2026, 1, 1), "estado": "Pendiente"},
                {"descripcion": "old", "fecha_detectada": date(2026, 1, 1), "estado": "Zeta"},
            ],
        )
        db.session.commit()


class WriteBase(ServiceBase):
    def create(self, who=None, **overrides) -> NoConformidad:
        nc = service().create(db.session, who or actor(), VALID | overrides)
        db.session.commit()
        return nc


class CreateTestCase(WriteBase):
    def test_every_role_can_create_and_the_audit_row_holds_the_after_snapshot(self) -> None:
        for role in RoleEnum:
            with self.subTest(role=role.name):
                nc = self.create(actor(role))
                row = self.audit_rows()[-1]
                self.assertEqual(
                    ("no_conformidades", nc.id, "create", "web"),
                    (row.entity_type, row.entity_id, row.action, row.channel),
                )
                self.assertIsNone(row.before)
                self.assertEqual("Pieza fuera de tolerancia", row.after["descripcion"])
                self.assertEqual("Abierta", row.after["estado"])
                self.assertEqual("2026-10-01", row.after["fecha_detectada"])

    def test_create_stamps_creation_and_update_metadata(self) -> None:
        nc = self.create(actor(user_id=7))
        self.assertEqual((7, 7), (nc.created_by_id, nc.updated_by_id))
        self.assertIsNotNone(nc.created_at)
        self.assertEqual(nc.created_at, nc.updated_at)

    def test_state_defaults_to_abierta_and_text_is_trimmed(self) -> None:
        nc = self.create(descripcion="  espacios  ", responsable="  Ana ")
        self.assertEqual(("espacios", "Ana", "Abierta"), (nc.descripcion, nc.responsable, nc.estado))

    def test_invalid_data_raises_validation_error_and_writes_nothing(self) -> None:
        cases = {
            "blank description": VALID | {"descripcion": "   "},
            "missing description": {k: v for k, v in VALID.items() if k != "descripcion"},
            "missing date": {k: v for k, v in VALID.items() if k != "fecha_detectada"},
            "date as text": VALID | {"fecha_detectada": "2026-10-01"},
            "unknown state": VALID | {"estado": "Archivada"},
            "null state": VALID | {"estado": None},
            "responsable too long": VALID | {"responsable": "x" * 51},
            "unknown field": VALID | {"created_by_id": 1},
            "id is not writable": VALID | {"id": 99},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        self.assertEqual(0, db.session.query(NoConformidad).count())
        self.assertEqual([], self.audit_rows())

    def test_scoped_actor_without_write_is_denied(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            service().create(db.session, actor(scopes={"read"}), VALID)
        self.assertEqual(0, db.session.query(NoConformidad).count())

    def test_integrity_error_becomes_conflict(self) -> None:
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().create(db.session, actor(), VALID)


class UpdateTestCase(WriteBase):
    def test_update_changes_fields_stamps_and_audits_changed_fields_only(self) -> None:
        nc = self.create(actor(user_id=7))
        created_at = nc.created_at
        service().update(
            db.session, actor(AUDITOR, user_id=8), nc.id,
            {"estado": "En proceso", "responsable": "Luis"},
        )
        db.session.commit()
        self.assertEqual(("En proceso", "Luis"), (nc.estado, nc.responsable))
        self.assertEqual((7, 8, created_at), (nc.created_by_id, nc.updated_by_id, nc.created_at))
        row = self.audit_rows()[-1]
        self.assertEqual("update", row.action)
        self.assertEqual("Abierta", row.before["estado"])
        self.assertEqual("En proceso", row.after["estado"])
        self.assertEqual("Luis", row.after["responsable"])
        self.assertNotIn("descripcion", row.after)
        self.assertEqual(8, row.after["updated_by_id"])

    def test_update_without_changes_writes_no_audit_row_and_keeps_stamps(self) -> None:
        nc = self.create(actor(user_id=7))
        before = (nc.updated_at, nc.updated_by_id)
        service().update(db.session, actor(user_id=8), nc.id, {"estado": "Abierta"})
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
        self.assertEqual(before, (nc.updated_at, nc.updated_by_id))

    def test_update_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().update(db.session, actor(), 999, {"estado": "Cerrada"})

    def test_update_validates_like_create_and_leaves_the_record_untouched(self) -> None:
        nc = self.create()
        for data in ({"estado": "Archivada"}, {"estado": None}, {"descripcion": " "},
                     {"fecha_detectada": None},
                     {"responsable": "x" * 51}, {"updated_by_id": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(), nc.id, data)
                db.session.rollback()
        db.session.refresh(nc)
        self.assertEqual("Pieza fuera de tolerancia", nc.descripcion)
        self.assertEqual(1, len(self.audit_rows()))

    def test_legacy_state_is_tolerated_only_while_unchanged(self) -> None:
        nc = self.create()
        db.session.execute(
            NoConformidad.__table__.update().values(estado="Pendiente")
        )
        db.session.commit()
        db.session.refresh(nc)
        service().update(db.session, actor(), nc.id, {"estado": "Pendiente", "responsable": "Eva"})
        db.session.commit()
        self.assertEqual("Pendiente", nc.estado)
        with self.assertRaises(errors().ValidationError):
            service().update(db.session, actor(), nc.id, {"estado": "Otro heredado"})
        db.session.rollback()

    def test_scoped_actor_without_write_cannot_update(self) -> None:
        nc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().update(db.session, actor(scopes={"read"}), nc.id, {"estado": "Cerrada"})

    def test_integrity_error_becomes_conflict(self) -> None:
        nc = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("dup"))
        ):
            with self.assertRaises(errors().Conflict):
                service().update(db.session, actor(), nc.id, {"estado": "Cerrada"})


class DeleteTestCase(WriteBase):
    def test_admin_deletes_and_the_audit_row_holds_the_before_snapshot(self) -> None:
        nc = self.create()
        nc_id = nc.id
        service().delete(db.session, actor(ADMIN), nc_id)
        db.session.commit()
        self.assertIsNone(db.session.get(NoConformidad, nc_id))
        row = self.audit_rows()[-1]
        self.assertEqual(("delete", nc_id), (row.action, row.entity_id))
        self.assertIsNone(row.after)
        self.assertEqual("Pieza fuera de tolerancia", row.before["descripcion"])
        self.assertEqual("Ana", row.before["responsable"])

    def test_non_admin_roles_are_denied_and_the_record_survives(self) -> None:
        nc = self.create()
        for role in (OPERATIVO, AUDITOR):
            with self.subTest(role=role.name):
                with self.assertRaises(errors().PermissionDenied):
                    service().delete(db.session, actor(role), nc.id)
        self.assertIsNotNone(db.session.get(NoConformidad, nc.id))
        self.assertEqual(1, len(self.audit_rows()))

    def test_mcp_channel_never_deletes_even_for_admin(self) -> None:
        nc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, channel="mcp"), nc.id)
        self.assertIsNotNone(db.session.get(NoConformidad, nc.id))

    def test_scoped_admin_without_write_is_denied(self) -> None:
        nc = self.create()
        with self.assertRaises(errors().PermissionDenied):
            service().delete(db.session, actor(ADMIN, scopes={"read"}), nc.id)

    def test_delete_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().delete(db.session, actor(ADMIN), 999)

    def test_integrity_error_becomes_conflict(self) -> None:
        nc = self.create()
        with patch.object(
            db.session, "flush", side_effect=IntegrityError("stmt", {}, Exception("fk"))
        ):
            with self.assertRaises(errors().Conflict):
                service().delete(db.session, actor(ADMIN), nc.id)


if __name__ == "__main__":
    unittest.main()
