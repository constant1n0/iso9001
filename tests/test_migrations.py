"""Check that Alembic migrations build the schema the models describe.

The migrations use PostgreSQL-only DDL, so these tests need a disposable
PostgreSQL database in ``TEST_POSTGRES_URI``. CI provides one; locally they
are skipped when the variable is absent. The database is wiped.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db


POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
MIGRATIONS_DIR = str(bootstrap.PROJECT_ROOT / "migrations")
# Audited tables created after the record-metadata migration (c4d8e1f2a9b7).
LATER_AUDITED_TABLES = frozenset(
    {"personas", "competencias_requeridas", "competencias_acreditadas"}
)


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

    def _insert_legacy_user_and_nc(self, estado: str = "Abierta") -> None:
        """A user and a nonconformity; ``estado`` is free text before e7a9c1d3f5b8."""
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
                    "VALUES ('vieja', '2026-01-01', :estado)"
                ),
                {"estado": estado},
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
            self.assertFalse(LATER_AUDITED_TABLES & set(inspector.get_table_names()))
            for model in AUDITED_MODELS:
                if model.__tablename__ in LATER_AUDITED_TABLES:
                    continue
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
            self._insert_legacy_user_and_nc(estado="abierta")
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


    def test_api_tokens_table_cascades_with_its_user_and_downgrades(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            self.assertIn("api_tokens", inspector.get_table_names())
            (fk,) = inspector.get_foreign_keys("api_tokens")
            self.assertEqual("fk_api_tokens_user_id_users", fk["name"])
            self.assertEqual("CASCADE", fk["options"]["ondelete"])
            self.assertEqual(
                ["uq_api_tokens_prefix"],
                [u["name"] for u in inspector.get_unique_constraints("api_tokens")],
            )
            self.assertIn(
                "ix_api_tokens_user_id",
                {i["name"] for i in inspector.get_indexes("api_tokens")},
            )
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (username, password, role) "
                        "VALUES ('owner', 'x', 'OPERATIVO')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO api_tokens (user_id, name, prefix, "
                        "token_hash, scopes, created_at, expires_at) "
                        "SELECT id, 'n', 'a1b2c3d4', repeat('0', 64), 'read', "
                        "now(), now() + interval '1 day' FROM users"
                    )
                )
                connection.execute(text("DELETE FROM users"))
                remaining = connection.execute(
                    text("SELECT count(*) FROM api_tokens")
                ).scalar_one()
            self.assertEqual(0, remaining)
            downgrade(directory=MIGRATIONS_DIR, revision="d5a9f3b7c1e2")
            self.assertNotIn("api_tokens", inspect(db.engine).get_table_names())

    def test_users_active_keeps_existing_users_active_and_downgrades(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="e6b1a4c8d3f7")
            with db.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (username, password, role) "
                        "VALUES ('legacy', 'x', 'OPERATIVO')"
                    )
                )
            upgrade(directory=MIGRATIONS_DIR)
            columns = {c["name"]: c for c in inspect(db.engine).get_columns("users")}
            self.assertFalse(columns["active"]["nullable"])
            with db.engine.begin() as connection:
                # Writers outside the ORM rely on the server default.
                connection.execute(
                    text(
                        "INSERT INTO users (username, password, role) "
                        "VALUES ('raw', 'x', 'OPERATIVO')"
                    )
                )
            with db.engine.connect() as connection:
                active = dict(
                    connection.execute(
                        text("SELECT username, active FROM users")
                    ).all()
                )
            self.assertEqual({"legacy": True, "raw": True}, active)
            downgrade(directory=MIGRATIONS_DIR, revision="e6b1a4c8d3f7")
            self.assertNotIn(
                "active", {c["name"] for c in inspect(db.engine).get_columns("users")}
            )

    def test_people_tables_link_users_and_roles_and_downgrade(self) -> None:
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {
                fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                for table in ("personas", "persona_roles")
                for fk in inspector.get_foreign_keys(table)
            }
            self.assertEqual(("users", "SET NULL"), foreign_keys["fk_personas_user_id_users"])
            self.assertEqual(("personas", "CASCADE"),
                             foreign_keys["fk_persona_roles_persona_id_personas"])
            self.assertEqual(("roles_responsabilidades", "CASCADE"),
                             foreign_keys["fk_persona_roles_rol_id_roles_responsabilidades"])
            self.assertEqual(
                ["persona_id", "rol_id"],
                inspector.get_pk_constraint("persona_roles")["constrained_columns"],
            )
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO users (username, password, role) VALUES ('ana', 'x', 'OPERATIVO')"
                ))
                connection.execute(text(
                    "INSERT INTO roles_responsabilidades (rol) VALUES ('Calidad')"
                ))
                # Writers outside the ORM rely on the server default for ``activo``.
                connection.execute(text(
                    "INSERT INTO personas (nombre, user_id) SELECT 'Ana', id FROM users"
                ))
                connection.execute(text(
                    "INSERT INTO persona_roles (persona_id, rol_id) "
                    "SELECT p.id, r.id_rol FROM personas p, roles_responsabilidades r"
                ))
                self.assertIs(True, connection.execute(
                    text("SELECT activo FROM personas")).scalar_one())
            with self.assertRaises(IntegrityError):
                with db.engine.begin() as connection:
                    connection.execute(text(
                        "INSERT INTO personas (nombre, user_id) SELECT 'Otra', id FROM users"
                    ))
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM users"))
                connection.execute(text("DELETE FROM roles_responsabilidades"))
                row = connection.execute(text(
                    "SELECT user_id, (SELECT count(*) FROM persona_roles) FROM personas"
                )).one()
            self.assertEqual((None, 0), tuple(row))
            downgrade(directory=MIGRATIONS_DIR, revision="f2c7a9e4b1d6")
            remaining = set(inspect(db.engine).get_table_names())
            self.assertFalse({"personas", "persona_roles"} & remaining)

    def test_person_links_restrict_deleting_a_cited_person_and_downgrade(self) -> None:
        links = {"capacitaciones": "persona_id", "no_conformidades": "responsable_id",
                 "auditorias": "auditor_id"}
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            for table, column in links.items():
                with self.subTest(table=table):
                    columns = {c["name"]: c for c in inspector.get_columns(table)}
                    self.assertTrue(columns[column]["nullable"])
                    fk = {fk["name"]: fk for fk in inspector.get_foreign_keys(table)}[
                        f"fk_{table}_{column}_personas"]
                    self.assertEqual(
                        ("personas", [column], ["id"], "RESTRICT"),
                        (fk["referred_table"], fk["constrained_columns"],
                         fk["referred_columns"], fk["options"].get("ondelete")),
                    )
                    self.assertIn(f"ix_{table}_{column}",
                                  {i["name"] for i in inspector.get_indexes(table)})
            with db.engine.begin() as connection:
                connection.execute(text("INSERT INTO personas (nombre) VALUES ('Ana')"))
                connection.execute(text(
                    "INSERT INTO capacitaciones (tema, fecha, personal, persona_id) "
                    "SELECT 'Seguridad', '2026-10-01', 'Ana', id FROM personas"
                ))
                connection.execute(text(
                    "INSERT INTO no_conformidades "
                    "(descripcion, fecha_detectada, estado, responsable_id) "
                    "SELECT 'Fallo', '2026-10-01', 'abierta', id FROM personas"
                ))
                connection.execute(text(
                    "INSERT INTO auditorias "
                    "(area_auditada, fecha, auditor, resultado, estado, auditor_id) "
                    "SELECT 'Compras', '2026-10-01', 'Ana', 'OK', 'PENDIENTE', id FROM personas"
                ))
            # Each table on its own keeps the person: release them one at a time.
            for release in ("DELETE FROM capacitaciones", "DELETE FROM no_conformidades",
                            "DELETE FROM auditorias"):
                with self.subTest(still_cited_before=release):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text("DELETE FROM personas"))
                with db.engine.begin() as connection:
                    connection.execute(text(release))
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM personas"))
            downgrade(directory=MIGRATIONS_DIR, revision="a3c5e7f9b2d4")
            inspector = inspect(db.engine)
            for table, column in links.items():
                self.assertNotIn(column, {c["name"] for c in inspector.get_columns(table)})
            self.assertIn("personas", inspector.get_table_names())
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def test_competence_tables_guard_their_links_and_downgrade(self) -> None:
        expected = {
            "fk_competencias_requeridas_rol_id_roles_responsabilidades":
                ("roles_responsabilidades", "RESTRICT"),
            "fk_competencias_acreditadas_persona_id_personas": ("personas", "RESTRICT"),
            "fk_competencias_acreditadas_requisito_id": ("competencias_requeridas", "RESTRICT"),
            "fk_competencias_acreditadas_capacitacion_id_capacitaciones":
                ("capacitaciones", "SET NULL"),
            "fk_competencias_acreditadas_evaluador_id_personas": ("personas", "RESTRICT"),
        }
        indexed = {"competencias_requeridas": {"rol_id"},
                   "competencias_acreditadas": {"persona_id", "requisito_id",
                                                "capacitacion_id", "evaluador_id"}}
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {
                fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                for table in indexed for fk in inspector.get_foreign_keys(table)
            }
            self.assertLessEqual(expected.items(), foreign_keys.items())
            for table, columns in indexed.items():
                self.assertEqual({f"ix_{table}_{column}" for column in columns},
                                 {i["name"] for i in inspector.get_indexes(table)})
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO roles_responsabilidades (rol) VALUES ('Calidad')",
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    "INSERT INTO capacitaciones (tema, fecha, personal) "
                    "VALUES ('Seguridad', '2026-03-01', 'Ana')",
                    "INSERT INTO competencias_requeridas (rol_id, tipo, descripcion) "
                    "SELECT id_rol, 'formacion', 'Curso' FROM roles_responsabilidades",
                    # Writers outside the ORM rely on the server default for the evaluation.
                    "INSERT INTO competencias_acreditadas (persona_id, requisito_id, evidencia, "
                    "capacitacion_id, fecha_obtencion, evaluador_id) "
                    "SELECT p.id, r.id, 'Certificado', c.id, '2026-03-10', p.id "
                    "FROM personas p, competencias_requeridas r, capacitaciones c",
                ):
                    connection.execute(text(statement))
                self.assertEqual("pendiente", connection.execute(text(
                    "SELECT evaluacion_eficacia FROM competencias_acreditadas")).scalar_one())
            refused = (
                "UPDATE competencias_requeridas SET tipo = 'Formación'",
                "UPDATE competencias_acreditadas SET evaluacion_eficacia = 'otra'",
                "DELETE FROM roles_responsabilidades",
                "DELETE FROM competencias_requeridas",
                "DELETE FROM personas",
            )
            for statement in refused:
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM capacitaciones"))
                self.assertIsNone(connection.execute(text(
                    "SELECT capacitacion_id FROM competencias_acreditadas")).scalar_one())
            downgrade(directory=MIGRATIONS_DIR, revision="b8d2f4a6c1e3")
            self.assertFalse(set(indexed) & set(inspect(db.engine).get_table_names()))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def _nc_states(self) -> dict:
        with db.engine.connect() as connection:
            return dict(connection.execute(
                text("SELECT descripcion, estado FROM no_conformidades")).all())

    def test_nonconformity_states_convert_both_ways(self) -> None:
        legacy = {"a": "Abierta", "b": "En proceso", "c": "Cerrada", "d": "Pendiente revisión"}
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="c9e3a5b7d1f4")
            with db.engine.begin() as connection:
                for descripcion, estado in legacy.items():
                    connection.execute(text(
                        "INSERT INTO no_conformidades (descripcion, fecha_detectada, estado) "
                        "VALUES (:d, '2026-01-01', :e)"), {"d": descripcion, "e": estado})
            # env.py reconfigures logging on every run; keep the capture handler.
            with patch("logging.config.fileConfig"), \
                    self.assertLogs("alembic.runtime.migration", "INFO") as logs:
                upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual({"a": "abierta", "b": "accion_planificada", "c": "cerrada",
                              "d": "abierta"}, self._nc_states())
            self.assertTrue(any("4 nonconformity states" in line and "1 unrecognised" in line
                                for line in logs.output), logs.output)
            columns = {c["name"]: c for c in inspect(db.engine).get_columns("no_conformidades")}
            for name in ("origen", "gravedad", "contencion", "causa_raiz",
                         "motivo_cancelacion"):
                self.assertTrue(columns[name]["nullable"], name)
            with db.engine.begin() as connection:
                self.assertEqual((None, None), tuple(connection.execute(text(
                    "SELECT origen, gravedad FROM no_conformidades WHERE descripcion = 'a'"
                )).one()))
                # Writers outside the ORM get the opening state by default.
                connection.execute(text(
                    "INSERT INTO no_conformidades (descripcion, fecha_detectada, origen, "
                    "gravedad) VALUES ('e', '2026-02-01', 'cliente', 'observacion')"))
            self.assertEqual("abierta", self._nc_states()["e"])
            for statement in (
                "UPDATE no_conformidades SET estado = 'Abierta'",
                "UPDATE no_conformidades SET origen = 'Cliente'",
                "UPDATE no_conformidades SET gravedad = 'critica'",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                for descripcion, estado in (("a", "en_verificacion"), ("d", "cancelada")):
                    connection.execute(text(
                        "UPDATE no_conformidades SET estado = :e WHERE descripcion = :d"),
                        {"d": descripcion, "e": estado})
            downgrade(directory=MIGRATIONS_DIR, revision="c9e3a5b7d1f4")
            self.assertEqual({"a": "En proceso", "b": "En proceso", "c": "Cerrada",
                              "d": "Cerrada", "e": "Abierta"}, self._nc_states())
            names = {c["name"] for c in inspect(db.engine).get_columns("no_conformidades")}
            self.assertFalse(names & {"origen", "gravedad", "contencion", "causa_raiz",
                                      "motivo_cancelacion"})
            with db.engine.begin() as connection:  # free text again
                connection.execute(text("UPDATE no_conformidades SET estado = 'Pendiente'"))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual({"abierta"}, set(self._nc_states().values()))
            self.assertEqual([], self._schema_differences())


if __name__ == "__main__":
    unittest.main()
