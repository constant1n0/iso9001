"""Tests for the append-only audit recorder and the test-only flush guard."""

from __future__ import annotations

import enum
import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

import test_auth_bootstrap as bootstrap

from app.extensions import db
from app.models import EstadoNoConformidad, NoConformidad, RoleEnum, User

# The stored state is an enum; snapshots show its Spanish label.
ABIERTA, CERRADA = EstadoNoConformidad.abierta, EstadoNoConformidad.cerrada


def audit_module():
    from app.services import audit

    return audit


def make_actor(channel="web", user_id=7, label="alice", role=RoleEnum.OPERATIVO):
    from app.services.actor import Actor

    return Actor(user_id=user_id, label=label, role=role, channel=channel)


class SnapshotTestCase(unittest.TestCase):
    def test_serializes_values_to_json_safe_types(self) -> None:
        class Color(enum.Enum):
            RED = "rojo"

        audit = audit_module()
        self.assertEqual("2026-10-02", audit.json_safe(date(2026, 10, 2)))
        self.assertEqual(
            "2026-10-02T10:30:00+00:00",
            audit.json_safe(datetime(2026, 10, 2, 10, 30, tzinfo=timezone.utc)),
        )
        self.assertEqual("rojo", audit.json_safe(Color.RED))
        self.assertEqual("12.50", audit.json_safe(Decimal("12.50")))
        self.assertEqual(3, audit.json_safe(3))
        self.assertIsNone(audit.json_safe(None))

    def test_is_sensitive_covers_password_token_and_secret_names(self) -> None:
        audit = audit_module()
        for name in ("password", "password_hash", "reset_token", "API_SECRET"):
            self.assertTrue(audit.is_sensitive(name), name)
        for name in ("descripcion", "estado"):
            self.assertFalse(audit.is_sensitive(name), name)


class AuditDbBase(unittest.TestCase):
    """In-memory app with helpers; defines no tests of its own."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def new_nc(self, **kwargs) -> NoConformidad:
        values = {
            "descripcion": "Fallo",
            "fecha_detectada": date(2026, 10, 1),
            "estado": ABIERTA,
        } | kwargs
        nc = NoConformidad(**values)
        db.session.add(nc)
        db.session.flush()
        return nc


class AuditDatabaseTestCase(AuditDbBase):
    def test_snapshot_returns_column_values_only(self) -> None:
        nc = self.new_nc()
        snap = audit_module().snapshot(nc)
        self.assertEqual("Fallo", snap["descripcion"])
        self.assertEqual("2026-10-01", snap["fecha_detectada"])
        self.assertIsNone(snap["fecha_cierre"])
        self.assertEqual(nc.id, snap["id"])
        json.dumps(snap)

    def test_snapshot_excludes_sensitive_columns_of_user(self) -> None:
        user = User(username="bob", email="b@example.com", password="HASH", role=RoleEnum.OPERATIVO)
        db.session.add(user)
        db.session.flush()
        snap = audit_module().snapshot(user)
        self.assertNotIn("password", snap)
        self.assertNotIn("HASH", json.dumps(snap))
        self.assertEqual("bob", snap["username"])
        self.assertEqual("Operativo", snap["role"])

    def test_record_create_stores_full_after_snapshot(self) -> None:
        audit = audit_module()
        nc = NoConformidad(descripcion="Nueva", fecha_detectada=date(2026, 10, 1))
        db.session.add(nc)
        row = audit.record(db.session, make_actor(), "create", nc)
        db.session.commit()
        self.assertEqual("no_conformidades", row.entity_type)
        self.assertEqual(nc.id, row.entity_id)
        self.assertEqual("create", row.action)
        self.assertIsNone(row.before)
        self.assertEqual("Nueva", row.after["descripcion"])
        self.assertEqual("Abierta", row.after["estado"])
        self.assertEqual(7, row.actor_user_id)
        self.assertEqual("alice", row.actor_label)
        self.assertEqual("web", row.channel)
        self.assertIsNotNone(row.occurred_at)

    def test_record_update_stores_changed_fields_only(self) -> None:
        audit = audit_module()
        nc = self.new_nc()
        before = audit.snapshot(nc)
        nc.estado = CERRADA
        nc.fecha_cierre = date(2026, 10, 2)
        row = audit.record(
            db.session, make_actor(), "update", nc, before=before, request_id="req-1"
        )
        db.session.commit()
        self.assertEqual({"estado": "Abierta", "fecha_cierre": None}, row.before)
        self.assertEqual({"estado": "Cerrada", "fecha_cierre": "2026-10-02"}, row.after)
        self.assertEqual("req-1", row.request_id)

    def test_record_delete_stores_full_before_snapshot(self) -> None:
        audit = audit_module()
        nc = self.new_nc(descripcion="Borrar")
        nc_id = nc.id
        row = audit.record(db.session, make_actor(), "delete", nc)
        db.session.delete(nc)
        db.session.commit()
        self.assertEqual(nc_id, row.entity_id)
        self.assertEqual("Borrar", row.before["descripcion"])
        self.assertEqual(nc_id, row.before["id"])
        self.assertIsNone(row.after)

    def test_record_scrubs_sensitive_keys_passed_by_the_caller(self) -> None:
        audit = audit_module()
        user = User(username="bob", password="HASH", role=RoleEnum.OPERATIVO)
        db.session.add(user)
        db.session.flush()
        row = audit.record(
            db.session,
            make_actor(),
            "update",
            user,
            before={"password": "OLD", "username": "bob"},
            after={"password": "NEW", "username": "rob", "api_token": "x"},
        )
        db.session.commit()
        self.assertEqual({"username": "bob"}, row.before)
        self.assertEqual({"username": "rob"}, row.after)

    def test_record_rejects_unknown_action(self) -> None:
        nc = self.new_nc()
        with self.assertRaises(ValueError):
            audit_module().record(db.session, make_actor(), "purge", nc)

    def test_channel_and_system_actor_without_user_are_stored(self) -> None:
        audit = audit_module()
        from app.services.actor import Actor

        nc = self.new_nc()
        system = Actor(None, "system", RoleEnum.ADMINISTRADOR, "system")
        row = audit.record(db.session, system, "update", nc, before={}, after={})
        mcp = audit.record(db.session, make_actor("mcp"), "update", nc, before={}, after={})
        db.session.commit()
        self.assertIsNone(row.actor_user_id)
        self.assertEqual("system", row.actor_label)
        self.assertEqual("system", row.channel)
        self.assertEqual("mcp", mcp.channel)

    def test_deleting_the_actor_user_keeps_the_row_with_null_fk(self) -> None:
        audit = audit_module()
        user = User(username="carol", password="x", role=RoleEnum.OPERATIVO)
        db.session.add(user)
        db.session.flush()
        nc = self.new_nc()
        row = audit.record(
            db.session, make_actor(user_id=user.id, label="carol"), "update", nc,
            before={}, after={},
        )
        db.session.commit()
        row_id = row.id
        # SQLite does not enforce FKs unless asked; emulate the DB-side SET NULL.
        db.session.execute(db.text("PRAGMA foreign_keys=ON"))
        db.session.execute(db.text("DELETE FROM users WHERE id = :i"), {"i": user.id})
        db.session.commit()
        db.session.expire_all()
        stored = db.session.get(audit.AuditLog, row_id)
        self.assertIsNone(stored.actor_user_id)
        self.assertEqual("carol", stored.actor_label)

    def test_audited_models_registry_excludes_user_and_audit_log(self) -> None:
        audit = audit_module()
        from app import models

        self.assertIsInstance(audit.AUDITED_MODELS, tuple)
        self.assertNotIn(User, audit.AUDITED_MODELS)
        self.assertNotIn(models.AuditLog, audit.AUDITED_MODELS)
        self.assertIn(NoConformidad, audit.AUDITED_MODELS)
        domain = {
            m.class_
            for m in db.Model.registry.mappers
            if m.class_ not in (User, models.AuditLog, models.ApiToken)
        }
        self.assertEqual(domain, set(audit.AUDITED_MODELS))

    def test_record_update_without_before_snapshot_is_rejected(self) -> None:
        audit = audit_module()
        nc = self.new_nc()
        with self.assertRaises(ValueError):
            audit.record(db.session, make_actor(), "update", nc)

    def test_audit_log_rows_cannot_be_updated(self) -> None:
        audit = audit_module()
        row = audit.record(db.session, make_actor(), "create", self.new_nc())
        db.session.commit()
        row.actor_label = "mallory"
        with self.assertRaises(audit.AuditLogImmutable):
            db.session.flush()
        db.session.rollback()

    def test_audit_log_rows_cannot_be_deleted(self) -> None:
        audit = audit_module()
        row = audit.record(db.session, make_actor(), "create", self.new_nc())
        db.session.commit()
        db.session.delete(row)
        with self.assertRaises(audit.AuditLogImmutable):
            db.session.flush()
        db.session.rollback()


class FlushGuardTestCase(AuditDbBase):
    def install(self):
        audit = audit_module()
        remove = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(remove)
        return audit

    def test_flush_with_audit_row_passes_for_create_update_delete(self) -> None:
        audit = self.install()
        nc = NoConformidad(descripcion="x", fecha_detectada=date(2026, 10, 1))
        db.session.add(nc)
        audit.record(db.session, make_actor(), "create", nc)
        db.session.commit()
        before = audit.snapshot(nc)
        nc.estado = CERRADA
        audit.record(db.session, make_actor(), "update", nc, before=before)
        db.session.commit()
        audit.record(db.session, make_actor(), "delete", nc)
        db.session.delete(nc)
        db.session.commit()
        self.assertEqual(3, db.session.query(audit.AuditLog).count())

    def test_flush_without_audit_row_fails_for_each_change_kind(self) -> None:
        audit = self.install()
        nc = NoConformidad(descripcion="x", fecha_detectada=date(2026, 10, 1))
        db.session.add(nc)
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()

        db.session.add(NoConformidad(descripcion="y", fecha_detectada=date(2026, 10, 1)))
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.commit()
        db.session.rollback()

    def test_update_and_delete_without_audit_row_fail(self) -> None:
        audit = audit_module()
        nc = self.new_nc()
        db.session.commit()
        remove = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(remove)
        nc.estado = CERRADA
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()
        db.session.delete(db.session.get(NoConformidad, nc.id))
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()

    def test_unaudited_models_and_untouched_instances_are_ignored(self) -> None:
        audit = self.install()
        db.session.add(User(username="dan", password="x", role=RoleEnum.OPERATIVO))
        db.session.flush()
        self.assertEqual(0, db.session.query(audit.AuditLog).count())

    def test_audit_row_for_another_entity_type_does_not_satisfy_the_guard(self) -> None:
        audit = self.install()
        from app.models import Auditoria

        other = Auditoria(area_auditada="a", auditor="b", resultado="c", fecha=date(2026, 10, 1))
        db.session.add(other)
        audit.record(db.session, make_actor(), "create", other)
        db.session.flush()
        db.session.add(NoConformidad(descripcion="x", fecha_detectada=date(2026, 10, 1)))
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()

    def test_two_changed_instances_with_one_audit_row_fail_the_guard(self) -> None:
        audit = audit_module()
        first, second = self.new_nc(), self.new_nc()
        db.session.commit()
        remove = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(remove)
        before = audit.snapshot(first)
        first.estado = CERRADA
        second.estado = CERRADA
        audit.record(db.session, make_actor(), "update", first, before=before)
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()

    def test_two_changed_instances_with_one_audit_row_each_pass(self) -> None:
        audit = audit_module()
        first, second = self.new_nc(), self.new_nc()
        db.session.commit()
        remove = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(remove)
        for nc in (first, second):
            before = audit.snapshot(nc)
            nc.estado = CERRADA
            audit.record(db.session, make_actor(), "update", nc, before=before)
        db.session.commit()
        self.assertEqual(2, db.session.query(audit.AuditLog).count())

    def test_two_new_instances_sharing_one_audit_row_without_pk_fail_the_guard(self) -> None:
        audit = self.install()
        first = NoConformidad(descripcion="a", fecha_detectada=date(2026, 10, 1))
        second = NoConformidad(descripcion="b", fecha_detectada=date(2026, 10, 1))
        db.session.add_all([first, second])
        # Both pending instances have no primary key yet, so they would share
        # the key (table, None); one audit row must not cover them both.
        db.session.add(
            audit.AuditLog(
                entity_type="no_conformidades",
                entity_id=None,
                action="create",
                actor_user_id=7,
                actor_label="alice",
                channel="web",
            )
        )
        with self.assertRaises(audit.AuditGuardViolation):
            db.session.flush()
        db.session.rollback()

    def test_removing_the_guard_stops_enforcement(self) -> None:
        audit = audit_module()
        remove = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        remove()
        db.session.add(NoConformidad(descripcion="x", fecha_detectada=date(2026, 10, 1)))
        db.session.flush()


class ProductionIsolationTestCase(unittest.TestCase):
    def test_guard_is_not_installed_by_default(self) -> None:
        app = bootstrap.build_app()
        with app.app_context():
            db.create_all()
            try:
                db.session.add(
                    NoConformidad(descripcion="x", fecha_detectada=date(2026, 10, 1))
                )
                db.session.flush()  # no audit row: raises only if a guard is installed
            finally:
                db.session.rollback()
                db.session.remove()
                db.drop_all()
            self.assertFalse(
                any(
                    getattr(fn, "__module__", "") == "app.services.audit"
                    and fn.__name__ == "_guard"
                    for fn in db.session().dispatch.before_flush
                )
            )


if __name__ == "__main__":
    unittest.main()
