"""Check that Alembic migrations build the schema the models describe.

The migrations use PostgreSQL-only DDL, so these tests need a disposable
PostgreSQL database in ``TEST_POSTGRES_URI``. CI provides one; locally they
are skipped when the variable is absent. The database is wiped.
"""

from __future__ import annotations

import os
import unittest

import test_auth_bootstrap as bootstrap
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db


POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
MIGRATIONS_DIR = str(bootstrap.PROJECT_ROOT / "migrations")


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(
    POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set"
)
class MigrationsTestCase(unittest.TestCase):
    """Run the real migration chain against an empty PostgreSQL database."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self._reset_database()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()

    def _schema_differences(self) -> list:
        with db.engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True}
            )
            return compare_metadata(context, db.metadata)

    def test_migrations_at_head_match_the_models(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def test_password_column_fits_the_model_length(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            columns = {
                column["name"]: column
                for column in inspect(db.engine).get_columns("users")
            }
            self.assertEqual(256, columns["password"]["type"].length)

    def test_latest_migration_downgrades_and_upgrades_again(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def test_audit_logs_uses_jsonb_and_downgrade_drops_it(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            columns = {c["name"]: c for c in inspector.get_columns("audit_logs")}
            self.assertEqual("JSONB", type(columns["before"]["type"]).__name__)
            self.assertEqual("JSONB", type(columns["after"]["type"]).__name__)
            with self.assertRaises(IntegrityError) as raised:
                with db.engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO audit_logs "
                            "(entity_type, action, actor_label, channel) "
                            "VALUES ('x', 'create', 'a', 'bogus')"
                        )
                    )
            self.assertIn("ck_audit_logs_channel", str(raised.exception))
            downgrade(directory=MIGRATIONS_DIR, revision="b7e2c9d41f03")
            self.assertNotIn("audit_logs", inspect(db.engine).get_table_names())

    def _insert_legacy_user_and_nc(self) -> None:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (username, password, role) "
                    "VALUES ('legacy', 'x', 'OPERATIVO')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO no_conformidades "
                    "(descripcion, fecha_detectada, estado) "
                    "VALUES ('vieja', '2026-01-01', 'Abierta')"
                )
            )

    def test_metadata_migration_keeps_legacy_rows_null_and_downgrades(self) -> None:
        from app.services.audit import AUDITED_MODELS

        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="c4d8e1f2a9b7")
            self._insert_legacy_user_and_nc()
            upgrade(directory=MIGRATIONS_DIR)
            with db.engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT created_at, created_by_id, updated_at, "
                        "updated_by_id FROM no_conformidades"
                    )
                ).one()
            self.assertEqual((None, None, None, None), tuple(row))
            inspector = inspect(db.engine)
            for model in AUDITED_MODELS:
                names = {c["name"] for c in inspector.get_columns(model.__tablename__)}
                self.assertLessEqual(
                    {"created_at", "created_by_id", "updated_at", "updated_by_id"},
                    names,
                    model.__tablename__,
                )
            downgrade(directory=MIGRATIONS_DIR, revision="c4d8e1f2a9b7")
            inspector = inspect(db.engine)
            for model in AUDITED_MODELS:
                names = {c["name"] for c in inspector.get_columns(model.__tablename__)}
                self.assertFalse(
                    names & {"created_at", "created_by_id", "updated_at", "updated_by_id"},
                    model.__tablename__,
                )
                self.assertFalse(
                    [
                        fk
                        for fk in inspector.get_foreign_keys(model.__tablename__)
                        if fk["name"] and fk["name"].endswith("_users")
                    ],
                    model.__tablename__,
                )

    def test_deleting_a_user_nulls_the_attribution_ids(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            self._insert_legacy_user_and_nc()
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE no_conformidades SET created_by_id = "
                        "(SELECT id FROM users), updated_by_id = "
                        "(SELECT id FROM users)"
                    )
                )
                connection.execute(text("DELETE FROM users"))
                row = connection.execute(
                    text(
                        "SELECT created_by_id, updated_by_id FROM no_conformidades"
                    )
                ).one()
            self.assertEqual((None, None), tuple(row))


if __name__ == "__main__":
    unittest.main()
