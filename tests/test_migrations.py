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


if __name__ == "__main__":
    unittest.main()
