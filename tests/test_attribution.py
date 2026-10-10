"""Record metadata: the mixin columns and the explicit stamping helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import test_auth_bootstrap as bootstrap
from sqlalchemy import DateTime
from sqlalchemy import inspect as sa_inspect

from app.extensions import db
from app.models import AuditLog, NoConformidad, RoleEnum, User

METADATA_COLUMNS = ("created_at", "created_by_id", "updated_at", "updated_by_id")


def audited_models() -> tuple[type, ...]:
    from app.services.audit import AUDITED_MODELS

    return AUDITED_MODELS


def attribution_module():
    from app.services import attribution

    return attribution


def make_actor(user_id=7, channel="web"):
    from app.services.actor import Actor

    return Actor(
        user_id=user_id, label="alice", role=RoleEnum.OPERATIVO, channel=channel
    )


class MixinColumnsTestCase(unittest.TestCase):
    def test_every_audited_model_has_nullable_metadata_columns(self) -> None:
        self.assertEqual(17, len(audited_models()))
        for model in audited_models():
            table = model.__table__
            for name in METADATA_COLUMNS:
                with self.subTest(model=model.__name__, column=name):
                    self.assertIn(name, table.c)
                    self.assertTrue(table.c[name].nullable)
                    self.assertIsNone(table.c[name].server_default)
                    self.assertIsNone(table.c[name].default)
            for name in ("created_at", "updated_at"):
                column_type = table.c[name].type
                self.assertIsInstance(column_type, DateTime)
                self.assertTrue(column_type.timezone, f"{model.__name__}.{name}")

    def test_user_columns_are_foreign_keys_set_null_with_explicit_names(self) -> None:
        for model in audited_models():
            table = model.__table__
            for name in ("created_by_id", "updated_by_id"):
                with self.subTest(model=model.__name__, column=name):
                    (fk,) = table.c[name].foreign_keys
                    self.assertEqual("users.id", fk.target_fullname)
                    self.assertEqual("SET NULL", fk.ondelete)
                    self.assertEqual(
                        f"fk_{table.name}_{name}_users", fk.constraint.name
                    )

    def test_user_and_audit_log_do_not_carry_metadata(self) -> None:
        for model in (User, AuditLog):
            for name in METADATA_COLUMNS:
                with self.subTest(model=model.__name__, column=name):
                    self.assertNotIn(name, model.__table__.c)


class StampingTestCase(unittest.TestCase):
    def test_stamp_created_sets_created_and_updated_to_the_same_instant(self) -> None:
        nc = NoConformidad(descripcion="x")
        at = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
        attribution_module().stamp_created(nc, make_actor(user_id=7), at=at)
        self.assertEqual(at, nc.created_at)
        self.assertEqual(at, nc.updated_at)
        self.assertEqual(7, nc.created_by_id)
        self.assertEqual(7, nc.updated_by_id)

    def test_stamp_updated_leaves_created_untouched(self) -> None:
        nc = NoConformidad(descripcion="x")
        first = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
        later = first + timedelta(hours=1)
        module = attribution_module()
        module.stamp_created(nc, make_actor(user_id=7), at=first)
        module.stamp_updated(nc, make_actor(user_id=8), at=later)
        self.assertEqual(first, nc.created_at)
        self.assertEqual(7, nc.created_by_id)
        self.assertEqual(later, nc.updated_at)
        self.assertEqual(8, nc.updated_by_id)

    def test_default_instant_is_timezone_aware_utc(self) -> None:
        nc = NoConformidad(descripcion="x")
        before = datetime.now(timezone.utc)
        attribution_module().stamp_created(nc, make_actor())
        after = datetime.now(timezone.utc)
        self.assertEqual(timezone.utc.utcoffset(None), nc.created_at.utcoffset())
        self.assertTrue(before <= nc.created_at <= after)
        self.assertEqual(nc.created_at, nc.updated_at)

    def test_system_actor_without_user_id_stores_none_ids(self) -> None:
        nc = NoConformidad(descripcion="x")
        actor = make_actor(user_id=None, channel="system")
        attribution_module().stamp_created(nc, actor)
        self.assertIsNone(nc.created_by_id)
        self.assertIsNone(nc.updated_by_id)
        self.assertIsNotNone(nc.created_at)
        attribution_module().stamp_updated(nc, actor)
        self.assertIsNone(nc.updated_by_id)


class UnstampedInsertTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_insert_without_stamping_leaves_metadata_null(self) -> None:
        nc = NoConformidad(descripcion="x")
        db.session.add(nc)
        db.session.commit()
        db.session.expire_all()
        row = db.session.get(NoConformidad, nc.id)
        for name in METADATA_COLUMNS:
            self.assertIsNone(getattr(row, name), name)

    def test_models_expose_columns_in_the_mapper(self) -> None:
        mapper = sa_inspect(NoConformidad)
        for name in METADATA_COLUMNS:
            self.assertIn(name, mapper.columns)


if __name__ == "__main__":
    unittest.main()
