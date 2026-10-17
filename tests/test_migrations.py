"""Check that Alembic migrations build the schema the models describe.

The migrations use PostgreSQL-only DDL, so these tests need a disposable
PostgreSQL database in ``TEST_POSTGRES_URI``. CI provides one; locally they
are skipped when the variable is absent. The database is wiped.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
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
    {"personas", "competencias_requeridas", "competencias_acreditadas",
     "acciones_correctivas", "document_revisions", "programas_auditoria",
     "hallazgos_auditoria", "objetivos_calidad", "mediciones_objetivo",
     "revisiones_direccion", "acciones_revision", "riesgos", "oportunidades",
     "acciones_riesgo"}
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


    def test_corrective_actions_table_guards_its_links_and_downgrade(self) -> None:
        table = "acciones_correctivas"
        expected = {
            "fk_acciones_correctivas_no_conformidad_id_no_conformidades":
                ("no_conformidades", "CASCADE"),
            "fk_acciones_correctivas_responsable_id_personas": ("personas", "RESTRICT"),
            "fk_acciones_correctivas_verificador_id_personas": ("personas", "RESTRICT"),
        }
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                            for fk in inspector.get_foreign_keys(table)}
            self.assertLessEqual(expected.items(), foreign_keys.items())
            self.assertEqual(
                {f"ix_{table}_{column}"
                 for column in ("no_conformidad_id", "responsable_id", "verificador_id")},
                {index["name"] for index in inspector.get_indexes(table)})
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    "INSERT INTO no_conformidades (descripcion, fecha_detectada) "
                    "VALUES ('Fallo', '2026-10-01')",
                    "INSERT INTO acciones_correctivas (no_conformidad_id, descripcion, "
                    "responsable_id, fecha_prevista, verificador_id) "
                    "SELECT n.id, 'Reajustar', p.id, '2026-10-20', p.id "
                    "FROM no_conformidades n, personas p",
                ):
                    connection.execute(text(statement))
            for statement in (
                "UPDATE acciones_correctivas SET resultado_verificacion = 'Eficaz'",
                "UPDATE acciones_correctivas SET descripcion = NULL",
                "DELETE FROM personas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE acciones_correctivas SET resultado_verificacion = 'no_eficaz'"))
                connection.execute(text("DELETE FROM no_conformidades"))  # cascades
                self.assertEqual(0, connection.execute(text(
                    "SELECT count(*) FROM acciones_correctivas")).scalar_one())
            downgrade(directory=MIGRATIONS_DIR, revision="e7a9c1d3f5b8")
            self.assertNotIn(table, inspect(db.engine).get_table_names())
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def _rows(self, sql: str) -> list[tuple]:
        with db.engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql))]

    def test_documents_become_effective_revisions_and_convert_back(self) -> None:
        moved = {"content", "version", "approved_by", "signature", "issued_date"}
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="f8b2d4a6c9e1")
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO documents (title, code, category, version, issued_date, "
                    "approved_by, signature, content, created_at) VALUES "
                    "('Manual', 'MC-1', 'MANUAL_CALIDAD', '2.1', '2026-03-01', 'Dirección', "
                    "'firma', 'Texto A', '2026-01-05 12:00+00'), "
                    "('Proc', 'PO-1', 'OTRO', '1.0', NULL, NULL, NULL, 'Texto B', "
                    "'2026-02-07 12:00+00'), "
                    "('Antiguo', 'AN-1', 'OTRO', '1', NULL, NULL, NULL, 'Texto C', NULL)"))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([
                ("MC-1", 1, "vigente", "Texto A", "2.1", "Dirección", "firma", date(2026, 3, 1)),
                ("PO-1", 1, "vigente", "Texto B", "1.0", None, None, date(2026, 2, 7)),
                ("AN-1", 1, "vigente", "Texto C", "1", None, None, None),
            ], self._rows(
                "SELECT d.code, r.numero, r.estado, r.content, r.legacy_version, "
                "r.legacy_approved_by, r.legacy_signature, r.effective_from "
                "FROM document_revisions r JOIN documents d ON d.id = r.document_id "
                "ORDER BY d.id"))
            columns = {c["name"]: c for c in inspect(db.engine).get_columns("documents")}
            self.assertFalse(moved & set(columns))
            for name in ("owner_id", "next_review_date", "withdrawn_at", "withdrawn_reason",
                         "withdrawn_by_id"):
                self.assertTrue(columns[name]["nullable"], name)
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    # MC-1 gets a draft; PO-1 a second revision in force, approved by Ana.
                    "INSERT INTO document_revisions (document_id, numero, estado, content) "
                    "SELECT id, 2, 'borrador', 'Borrador A' FROM documents WHERE code = 'MC-1'",
                    "UPDATE document_revisions SET estado = 'obsoleto' WHERE document_id = "
                    "(SELECT id FROM documents WHERE code = 'PO-1')",
                    "INSERT INTO document_revisions (document_id, numero, estado, content, "
                    "approver_id, effective_from) SELECT d.id, 2, 'vigente', 'Texto B2', p.id, "
                    "'2026-10-01' FROM documents d, personas p WHERE d.code = 'PO-1'",
                    "INSERT INTO documents (title, code, category) VALUES ('Nuevo', 'NU-1', 'OTRO')",
                    "INSERT INTO document_revisions (document_id, numero, estado, content) "
                    "SELECT id, 1, 'borrador', 'Borrador D' FROM documents WHERE code = 'NU-1'",
                ):
                    connection.execute(text(statement))
            for statement in (
                "INSERT INTO document_revisions (document_id, numero, estado, content) "
                "SELECT id, 3, 'vigente', 'x' FROM documents WHERE code = 'PO-1'",
                "INSERT INTO document_revisions (document_id, numero, estado, content) "
                "SELECT id, 3, 'en_revision', 'x' FROM documents WHERE code = 'MC-1'",
                "INSERT INTO document_revisions (document_id, numero, estado, content) "
                "SELECT id, 2, 'obsoleto', 'x' FROM documents WHERE code = 'MC-1'",
                "UPDATE document_revisions SET estado = 'Vigente'",
                "DELETE FROM documents WHERE code = 'AN-1'",
                "DELETE FROM personas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            downgrade(directory=MIGRATIONS_DIR, revision="f8b2d4a6c9e1")
            self.assertNotIn("document_revisions", inspect(db.engine).get_table_names())
            self.assertEqual([
                ("MC-1", "Texto A", "2.1", "Dirección", "firma", date(2026, 3, 1)),
                ("PO-1", "Texto B2", "2", "Ana", None, date(2026, 10, 1)),
                ("AN-1", "Texto C", "1", None, None, None),
                ("NU-1", "Borrador D", "1", None, None, None),
            ], self._rows("SELECT code, content, version, approved_by, signature, issued_date "
                          "FROM documents ORDER BY id"))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual(4, self._rows("SELECT count(*) FROM document_revisions")[0][0])
            self.assertEqual([], self._schema_differences())

    def test_revision_attachments_are_complete_unique_and_downgrade(self) -> None:
        attachment = ("attachment_name", "attachment_path", "attachment_size",
                      "attachment_sha256", "attachment_mime")
        insert = ("INSERT INTO document_revisions (document_id, numero, estado, content, "
                  + ", ".join(attachment) + ") SELECT id, 1, 'borrador', 'x', 'a.pdf', '"
                  + "a" * 32 + "', 10, '" + "f" * 64 + "', 'application/pdf' FROM documents "
                  "WHERE code = '{code}'")
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR)
            with db.engine.begin() as connection:
                connection.execute(text("INSERT INTO documents (title, code, category) "
                                        "VALUES ('M', 'M-1', 'OTRO'), ('N', 'N-1', 'OTRO')"))
                connection.execute(text(insert.format(code="M-1")))
            for statement in (
                insert.format(code="N-1"),  # two revisions sharing one stored file
                "UPDATE document_revisions SET attachment_sha256 = NULL",
                "UPDATE document_revisions SET attachment_name = NULL",
                "UPDATE document_revisions SET attachment_size = 0",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text("UPDATE document_revisions SET "
                                        + ", ".join(f"{c} = NULL" for c in attachment)))
            downgrade(directory=MIGRATIONS_DIR, revision="a7d3f5b9c2e4")
            columns = {c["name"] for c in inspect(db.engine).get_columns("document_revisions")}
            self.assertFalse(set(attachment) & columns)
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())


    def test_audit_programmes_and_planning_fields_guard_their_links_and_downgrade(self) -> None:
        planning = {"programa_id", "alcance", "criterios", "responsable_area_id"}
        expected = {
            "fk_auditorias_programa_id_programas_auditoria": ("programas_auditoria", "RESTRICT"),
            "fk_auditorias_responsable_area_id_personas": ("personas", "RESTRICT"),
            "fk_programas_auditoria_aprobado_por_id_personas": ("personas", "RESTRICT"),
            "fk_auditoria_equipo_auditoria_id_auditorias": ("auditorias", "CASCADE"),
            "fk_auditoria_equipo_persona_id_personas": ("personas", "RESTRICT"),
        }
        programme = ("INSERT INTO programas_auditoria (anio, objetivos, alcance) "
                     "VALUES ({anio}, 'Objetivos', 'Alcance')")
        team = ("INSERT INTO auditoria_equipo (auditoria_id, persona_id) "
                "SELECT a.id, p.id FROM auditorias a, personas p")
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="c3e8a1f6d4b2")
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO auditorias (area_auditada, fecha, auditor, resultado, estado) "
                    "VALUES ('Compras', '2026-03-01', 'Luis', 'OK', 'PENDIENTE')"))
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            columns = {c["name"]: c for c in inspector.get_columns("auditorias")}
            for name in planning:
                self.assertTrue(columns[name]["nullable"], name)
            self.assertEqual([(None, None, None, None)], self._rows(
                "SELECT programa_id, alcance, criterios, responsable_area_id FROM auditorias"))
            foreign_keys = {
                fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                for table in ("auditorias", "programas_auditoria", "auditoria_equipo")
                for fk in inspector.get_foreign_keys(table)
            }
            self.assertLessEqual(expected.items(), foreign_keys.items())
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    programme.format(anio=2026),
                    "UPDATE auditorias SET programa_id = (SELECT id FROM programas_auditoria), "
                    "responsable_area_id = (SELECT id FROM personas)",
                    team,
                ):
                    connection.execute(text(statement))
            self.assertEqual([("borrador",)], self._rows("SELECT estado FROM programas_auditoria"))
            for statement in (
                programme.format(anio=2026),  # one programme per year
                programme.format(anio=1999),
                programme.format(anio=2101),
                "UPDATE programas_auditoria SET estado = 'Aprobado'",
                "UPDATE programas_auditoria SET estado = 'aprobado'",  # no date nor approver
                "UPDATE programas_auditoria SET objetivos = NULL",
                team,  # the same person twice in one team
                "DELETE FROM programas_auditoria",
                "DELETE FROM personas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE programas_auditoria SET estado = 'aprobado', "
                    "aprobado_en = '2026-01-15', aprobado_por_id = (SELECT id FROM personas)"))
                connection.execute(text("DELETE FROM auditorias"))  # cascades to the team
                self.assertEqual(0, connection.execute(text(
                    "SELECT count(*) FROM auditoria_equipo")).scalar_one())
            downgrade(directory=MIGRATIONS_DIR, revision="c3e8a1f6d4b2")
            inspector = inspect(db.engine)
            self.assertFalse({"programas_auditoria", "auditoria_equipo"}
                             & set(inspector.get_table_names()))
            self.assertFalse(planning & {c["name"] for c in inspector.get_columns("auditorias")})
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())


    def test_audit_findings_guard_their_links_and_downgrade(self) -> None:
        table = "hallazgos_auditoria"
        expected = {
            "fk_hallazgos_auditoria_auditoria_id_auditorias": ("auditorias", "RESTRICT"),
            "fk_hallazgos_auditoria_no_conformidad_id_no_conformidades":
                ("no_conformidades", "SET NULL"),
        }
        finding = ("INSERT INTO hallazgos_auditoria (auditoria_id, tipo, descripcion, "
                   "no_conformidad_id) SELECT a.id, '{tipo}', 'Firma ausente', {link} "
                   "FROM auditorias a")
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="d7f1b3a5c9e2")
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO auditorias (area_auditada, fecha, auditor, resultado, estado) "
                    "VALUES ('Compras', '2026-03-01', 'Luis', 'OK', 'PENDIENTE')"))
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                            for fk in inspector.get_foreign_keys(table)}
            self.assertLessEqual(expected.items(), foreign_keys.items())
            self.assertIn(f"ix_{table}_auditoria_id",
                          {index["name"] for index in inspector.get_indexes(table)})
            self.assertEqual([["no_conformidad_id"]],
                             [u["column_names"] for u in inspector.get_unique_constraints(table)])
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO no_conformidades (descripcion, fecha_detectada) "
                    "VALUES ('Firma ausente', '2026-03-01')"))
                connection.execute(text(finding.format(
                    tipo="nc_mayor", link="(SELECT id FROM no_conformidades)")))
                connection.execute(text(finding.format(tipo="observacion", link="NULL")))
            for statement in (
                finding.format(tipo="nc_mayor", link="(SELECT id FROM no_conformidades)"),
                finding.format(tipo="No conformidad mayor", link="NULL"),
                "UPDATE hallazgos_auditoria SET descripcion = NULL",
                "UPDATE hallazgos_auditoria SET auditoria_id = NULL",
                "DELETE FROM auditorias",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM no_conformidades"))  # clears the link
            self.assertEqual([(None,), (None,)], self._rows(
                "SELECT no_conformidad_id FROM hallazgos_auditoria ORDER BY id"))
            downgrade(directory=MIGRATIONS_DIR, revision="d7f1b3a5c9e2")
            self.assertNotIn(table, inspect(db.engine).get_table_names())
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())


    def test_quality_objectives_guard_their_links_and_downgrade(self) -> None:
        objectives, measurements = "objetivos_calidad", "mediciones_objetivo"
        expected = {
            "fk_objetivos_calidad_proceso_id_procesos_operacion":
                ("procesos_operacion", "RESTRICT"),
            "fk_objetivos_calidad_responsable_id_personas": ("personas", "RESTRICT"),
            "fk_mediciones_objetivo_objetivo_id_objetivos_calidad":
                ("objetivos_calidad", "RESTRICT"),
            "fk_mediciones_objetivo_registrado_por_id_personas": ("personas", "RESTRICT"),
        }
        objective = ("INSERT INTO objetivos_calidad (titulo, indicador, meta, sentido, "
                     "responsable_id, anio, fecha_inicio, fecha_limite, frecuencia) "
                     "SELECT 'Entregas', '% a tiempo', 95, '{sentido}', p.id, {anio}, "
                     "'2026-01-01', '{limite}', 'mensual' FROM personas p")
        measurement = ("INSERT INTO mediciones_objetivo (objetivo_id, fecha, valor) "
                       "SELECT id, '2026-01-31', 96.5 FROM objetivos_calidad")
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="e9c2a4f6b8d1")
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                            for table in (objectives, measurements)
                            for fk in inspector.get_foreign_keys(table)}
            self.assertLessEqual(expected.items(), foreign_keys.items())
            self.assertIn(f"ix_{measurements}_objetivo_id",
                          {index["name"] for index in inspector.get_indexes(measurements)})
            self.assertEqual([["objetivo_id", "fecha"]], [
                u["column_names"] for u in inspector.get_unique_constraints(measurements)])
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    "INSERT INTO procesos_operacion (proceso) VALUES ('Compras')",
                    objective.format(sentido="mayor_igual", anio=2026, limite="2026-12-31"),
                    "UPDATE objetivos_calidad SET proceso_id = "
                    "(SELECT id_proceso FROM procesos_operacion)",
                    measurement,
                ):
                    connection.execute(text(statement))
            self.assertEqual([("activo", Decimal("95.0000"), Decimal("96.5000"))], self._rows(
                "SELECT o.estado, o.meta, m.valor FROM objetivos_calidad o "
                "JOIN mediciones_objetivo m ON m.objetivo_id = o.id"))
            for statement in (
                measurement,  # one measurement per objective and date
                objective.format(sentido="≥", anio=2026, limite="2026-12-31"),
                objective.format(sentido="mayor_igual", anio=1999, limite="2026-12-31"),
                objective.format(sentido="mayor_igual", anio=2026, limite="2025-12-31"),
                "UPDATE objetivos_calidad SET estado = 'Activo'",
                "UPDATE objetivos_calidad SET frecuencia = 'semanal'",
                "UPDATE objetivos_calidad SET meta = NULL",
                "DELETE FROM objetivos_calidad",
                "DELETE FROM procesos_operacion",
                "DELETE FROM personas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            downgrade(directory=MIGRATIONS_DIR, revision="e9c2a4f6b8d1")
            self.assertFalse({objectives, measurements}
                             & set(inspect(db.engine).get_table_names()))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())

    def test_management_reviews_guard_their_links_and_downgrade(self) -> None:
        reviews, actions, attendees = (
            "revisiones_direccion", "acciones_revision", "revision_asistentes")
        expected = {
            "fk_revisiones_direccion_celebrada_por_id_personas": ("personas", "RESTRICT"),
            "fk_revision_asistentes_revision_id_revisiones_direccion": (reviews, "CASCADE"),
            "fk_revision_asistentes_persona_id_personas": ("personas", "RESTRICT"),
            "fk_acciones_revision_revision_id_revisiones_direccion": (reviews, "RESTRICT"),
            "fk_acciones_revision_responsable_id_personas": ("personas", "RESTRICT"),
        }
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="b4d6f8a1c3e5")
            upgrade(directory=MIGRATIONS_DIR)
            inspector = inspect(db.engine)
            foreign_keys = {fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                            for table in (reviews, actions, attendees)
                            for fk in inspector.get_foreign_keys(table)}
            self.assertLessEqual(expected.items(), foreign_keys.items())
            self.assertLessEqual(
                {f"ix_{actions}_revision_id", f"ix_{actions}_responsable_id"},
                {index["name"] for index in inspector.get_indexes(actions)})
            self.assertIn(f"ix_{attendees}_persona_id",
                          {index["name"] for index in inspector.get_indexes(attendees)})
            with db.engine.begin() as connection:
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    "INSERT INTO revisiones_direccion (fecha, periodo_desde, periodo_hasta) "
                    "VALUES ('2026-07-10', '2026-04-01', '2026-06-30')",
                    "INSERT INTO revision_asistentes (revision_id, persona_id) "
                    "SELECT r.id, p.id FROM revisiones_direccion r, personas p",
                    "INSERT INTO acciones_revision (revision_id, tipo, descripcion, "
                    "responsable_id, fecha_limite) SELECT r.id, 'mejora', 'Automatizar', p.id, "
                    "'2026-12-31' FROM revisiones_direccion r, personas p",
                ):
                    connection.execute(text(statement))
            self.assertEqual([("borrador", None, "pendiente")], self._rows(
                "SELECT r.estado, r.datos_entrada, a.estado FROM revisiones_direccion r "
                "JOIN acciones_revision a ON a.revision_id = r.id"))
            for statement in (
                "UPDATE revisiones_direccion SET estado = 'celebrada'",  # without its inputs
                "UPDATE revisiones_direccion SET datos_entrada = '{}'",  # a draft has none
                "UPDATE revisiones_direccion SET estado = 'Borrador'",
                "UPDATE revisiones_direccion SET periodo_hasta = '2026-07-11'",
                "UPDATE revisiones_direccion SET fecha_cierre = '2026-10-12'",
                "UPDATE acciones_revision SET estado = 'completada'",  # without its date
                "UPDATE acciones_revision SET tipo = 'otra'",
                "DELETE FROM revisiones_direccion",
                "DELETE FROM personas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM acciones_revision"))
                connection.execute(text("DELETE FROM revisiones_direccion"))  # and its attendees
            self.assertEqual([(0,)], self._rows(f"SELECT count(*) FROM {attendees}"))
            downgrade(directory=MIGRATIONS_DIR, revision="b4d6f8a1c3e5")
            self.assertFalse({reviews, actions, attendees}
                             & set(inspect(db.engine).get_table_names()))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([], self._schema_differences())


    def test_risks_and_opportunities_move_to_their_registers_and_downgrade(self) -> None:
        old, risks, opportunities, actions = (
            "riesgos_oportunidades", "riesgos", "oportunidades", "acciones_riesgo")
        expected = {
            **{f"fk_{table}_{column}_{target}": (target, "RESTRICT")
               for table in (risks, opportunities)
               for column, target in (("parte_interesada_id", "partes_interesadas"),
                                      ("proceso_id", "procesos_operacion"),
                                      ("responsable_id", "personas"))},
            f"fk_{actions}_riesgo_id_{risks}": (risks, "RESTRICT"),
            f"fk_{actions}_oportunidad_id_{opportunities}": (opportunities, "RESTRICT"),
            f"fk_{actions}_responsable_id_personas": ("personas", "RESTRICT"),
        }
        legacy = ("SELECT id_riesgo, tipo, descripcion, objetivo_calidad, plan_accion, "
                  f"created_at, created_by_id FROM {old} ORDER BY id_riesgo")
        moved = ("SELECT id, titulo, descripcion, origen, estado, fecha_identificacion, notas, "
                 "fecha_revision, responsable_id, created_at, updated_at, created_by_id "
                 "FROM {} ORDER BY id")
        long_line = "Dependencia de un único proveedor de acero " * 6  # over 200 characters
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="a9c1e3f5d7b2")
            with db.engine.begin() as connection:
                user_id = connection.execute(text(
                    "INSERT INTO users (username, password, role) "
                    "VALUES ('legacy', 'x', 'OPERATIVO') RETURNING id")).scalar_one()
                connection.execute(text(
                    f"INSERT INTO {old} (tipo, descripcion, objetivo_calidad, plan_accion, "
                    "created_at, updated_at, created_by_id, updated_by_id) VALUES "
                    "('Riesgo', :one, 'Retener talento', 'Plan de carrera', "
                    " '2026-03-31 12:00+00', '2026-04-02 12:00+00', :user, :user), "
                    "('Oportunidad', 'Nuevo mercado', NULL, 'Estudio de mercado', "
                    " NULL, NULL, NULL, NULL), "
                    "('Riesgo', :three, NULL, NULL, '2026-02-01 12:00+00', NULL, NULL, NULL), "
                    "('Oportunidad', :four, 'Mejorar la imagen', '  ', "
                    " '2026-04-15 12:00+00', NULL, NULL, NULL), "
                    "('Riesgo', '  ', '', NULL, NULL, NULL, NULL, NULL)"
                ), {"one": "Rotación de personal\nSalida de técnicos clave", "user": user_id,
                    "three": f"{long_line}\nSegunda línea",
                    "four": "\n  Certificar ISO 14001  \r\nAmpliar el alcance"})
            before = self._rows(legacy)
            upgrade(directory=MIGRATIONS_DIR, revision="c6e8a2f4b1d9")  # RO-2 drops the old table
            (today,) = self._rows("SELECT CURRENT_DATE")[0]
            created = {row[0]: row[5:] for row in self._rows(
                f"SELECT id_riesgo, tipo, descripcion, objetivo_calidad, plan_accion, "
                f"created_at, updated_at, created_by_id FROM {old}")}
            self.assertEqual([
                (1, "Rotación de personal", "Rotación de personal\nSalida de técnicos clave",
                 "interno", "identificado", date(2026, 3, 31),
                 "Objetivo de la calidad: Retener talento\n\nPlan de acción: Plan de carrera",
                 None, None, *created[1]),
                (3, long_line[:200].rstrip(), f"{long_line}\nSegunda línea", "interno",
                 "identificado", date(2026, 2, 1), None, None, None, *created[3]),
                (5, "Riesgo 5", "  ", "interno", "identificado", today, None, None, None,
                 *created[5]),
            ], self._rows(moved.format(risks)))
            self.assertEqual([
                (2, "Nuevo mercado", "Nuevo mercado", "interno", "identificada", today,
                 "Plan de acción: Estudio de mercado", None, None, *created[2]),
                (4, "Certificar ISO 14001", "\n  Certificar ISO 14001  \r\nAmpliar el alcance",
                 "interno", "identificada", date(2026, 4, 15),
                 "Objetivo de la calidad: Mejorar la imagen", None, None, *created[4]),
            ], self._rows(moved.format(opportunities)))
            self.assertEqual(user_id, created[1][2])
            self.assertEqual(before, self._rows(legacy))  # kept until RO-2 drops it
            self.assertEqual([(None,) * 6], self._rows(
                "SELECT max(probabilidad), max(impacto), max(probabilidad_residual), "
                "max(impacto_residual), max(tratamiento), max(parte_interesada_id) "
                f"FROM {risks}"))
            inspector = inspect(db.engine)
            foreign_keys = {fk["name"]: (fk["referred_table"], fk["options"].get("ondelete"))
                            for table in (risks, opportunities, actions)
                            for fk in inspector.get_foreign_keys(table)}
            self.assertLessEqual(expected.items(), foreign_keys.items())
            for table, columns in ((risks, ("parte_interesada_id", "proceso_id",
                                            "responsable_id")),
                                   (opportunities, ("parte_interesada_id", "proceso_id",
                                                    "responsable_id")),
                                   (actions, ("riesgo_id", "oportunidad_id",
                                              "responsable_id"))):
                self.assertLessEqual({f"ix_{table}_{column}" for column in columns},
                                     {index["name"] for index in inspector.get_indexes(table)})
            with db.engine.begin() as connection:
                # New rows are numbered after the copied ones.
                self.assertEqual(6, connection.execute(text(
                    f"INSERT INTO {risks} (titulo, descripcion, origen, fecha_identificacion) "
                    "VALUES ('Nuevo', 'Nuevo', 'externo', '2026-10-17') RETURNING id"
                )).scalar_one())
                self.assertEqual(5, connection.execute(text(
                    f"INSERT INTO {opportunities} (titulo, descripcion, origen, "
                    "fecha_identificacion) VALUES ('Nueva', 'Nueva', 'externo', '2026-10-17') "
                    "RETURNING id")).scalar_one())
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    "INSERT INTO procesos_operacion (proceso) VALUES ('Compras')",
                    "INSERT INTO partes_interesadas (nombre) VALUES ('Clientes')",
                    f"UPDATE {risks} SET responsable_id = (SELECT id FROM personas), "
                    "proceso_id = (SELECT id_proceso FROM procesos_operacion), "
                    "parte_interesada_id = (SELECT id_interesado FROM partes_interesadas), "
                    "probabilidad = 4, impacto = 5, tratamiento = 'mitigar', "
                    "probabilidad_residual = 2, impacto_residual = 2, estado = 'controlado'",
                    f"UPDATE {opportunities} SET responsable_id = (SELECT id FROM personas), "
                    "beneficio = 3, viabilidad = 5, decision = 'aprovechar'",
                    f"INSERT INTO {actions} (riesgo_id, descripcion, responsable_id, "
                    f"fecha_limite) SELECT r.id, 'Plan de carrera', p.id, '2026-12-31' "
                    f"FROM {risks} r, personas p WHERE r.id = 1",
                    f"INSERT INTO {actions} (oportunidad_id, descripcion, responsable_id, "
                    f"fecha_limite, estado, fecha_completada, eficacia, fecha_evaluacion) "
                    f"SELECT o.id, 'Estudio', p.id, '2026-12-31', 'completada', '2026-11-30', "
                    f"'eficaz', '2027-01-15' FROM {opportunities} o, personas p WHERE o.id = 2",
                ):
                    connection.execute(text(statement))
            for statement in (
                f"UPDATE {risks} SET probabilidad = NULL",  # half an assessment
                f"UPDATE {risks} SET impacto = 6",
                f"UPDATE {risks} SET impacto_residual = NULL",
                f"UPDATE {risks} SET probabilidad_residual = 0",
                f"UPDATE {risks} SET origen = 'Interno'",
                f"UPDATE {risks} SET tratamiento = 'compartir'",
                f"UPDATE {risks} SET estado = 'Identificado'",
                f"UPDATE {opportunities} SET viabilidad = NULL",
                f"UPDATE {opportunities} SET beneficio = 0",
                f"UPDATE {opportunities} SET decision = 'Aprovechar'",
                f"UPDATE {opportunities} SET estado = 'identificado'",
                f"UPDATE {actions} SET riesgo_id = 1, oportunidad_id = 2",
                f"UPDATE {actions} SET riesgo_id = NULL, oportunidad_id = NULL",
                f"UPDATE {actions} SET estado = 'completada' WHERE riesgo_id = 1",
                f"UPDATE {actions} SET fecha_completada = '2026-11-30' WHERE riesgo_id = 1",
                f"UPDATE {actions} SET eficacia = 'eficaz', fecha_evaluacion = '2027-01-15' "
                "WHERE riesgo_id = 1",
                f"UPDATE {actions} SET fecha_evaluacion = NULL",
                f"UPDATE {actions} SET estado = 'Pendiente'",
                f"UPDATE {actions} SET eficacia = 'Eficaz'",
                f"DELETE FROM {risks} WHERE id = 1",
                f"DELETE FROM {opportunities} WHERE id = 2",
                "DELETE FROM personas",
                "DELETE FROM procesos_operacion",
                "DELETE FROM partes_interesadas",
            ):
                with self.subTest(refused=statement):
                    with self.assertRaises(IntegrityError):
                        with db.engine.begin() as connection:
                            connection.execute(text(statement))
            downgrade(directory=MIGRATIONS_DIR, revision="a9c1e3f5d7b2")
            self.assertFalse({risks, opportunities, actions}
                             & set(inspect(db.engine).get_table_names()))
            self.assertEqual(before, self._rows(legacy))
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([(3, 2, 0)], self._rows(
                f"SELECT (SELECT count(*) FROM {risks}), (SELECT count(*) FROM "
                f"{opportunities}), (SELECT count(*) FROM {actions})"))
            self.assertEqual([], self._schema_differences())

    def test_rows_written_after_the_copy_reach_the_registers_and_move_back(self) -> None:
        """RO-2 (e3b5d7f9a1c4) re-syncs the old table into the registers, then drops it."""
        old, risks, opportunities, actions = (
            "riesgos_oportunidades", "riesgos", "oportunidades", "acciones_riesgo")
        legacy = ("SELECT id_riesgo, tipo, descripcion, objetivo_calidad, plan_accion, "
                  f"created_at, updated_at, created_by_id FROM {old} ORDER BY id_riesgo")
        moved = "SELECT id, titulo, descripcion, notas, updated_at FROM {} ORDER BY id"
        insert = (f"INSERT INTO {old} (tipo, descripcion, objetivo_calidad, plan_accion, "
                  "created_at, updated_at) VALUES (:tipo, :descripcion, :objetivo, :plan, "
                  ":created, :created)")
        with self.app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision="a9c1e3f5d7b2")
            with db.engine.begin() as connection:
                for tipo, descripcion, objetivo, plan in (
                    ("Riesgo", "Rotación de personal", None, "Plan de carrera"),
                    ("Oportunidad", "Nuevo mercado", "Crecer un 10 %", None),
                    ("Riesgo", "Riesgo que se elimina", None, None),
                    ("Oportunidad", "Cambia de tipo", None, None),
                ):
                    connection.execute(text(insert), {
                        "tipo": tipo, "descripcion": descripcion, "objetivo": objetivo,
                        "plan": plan, "created": "2026-03-01 12:00+00"})
            upgrade(directory=MIGRATIONS_DIR, revision="c6e8a2f4b1d9")
            with db.engine.begin() as connection:  # the old service keeps writing until RO-2
                connection.execute(text(insert), {
                    "tipo": "Riesgo", "descripcion": "Corte de suministro\nProveedor único",
                    "objetivo": "Entregas a tiempo", "plan": "Segundo proveedor",
                    "created": "2026-10-01 12:00+00"})
                for statement in (
                    f"UPDATE {old} SET descripcion = 'Rotación de técnicos', "
                    "plan_accion = 'Plan de carrera y salario', "
                    "updated_at = '2026-10-02 12:00+00' WHERE id_riesgo = 1",
                    f"DELETE FROM {old} WHERE id_riesgo = 3",
                    f"UPDATE {old} SET tipo = 'Riesgo' WHERE id_riesgo = 4",
                ):
                    connection.execute(text(statement))
            upgrade(directory=MIGRATIONS_DIR)
            tables = set(inspect(db.engine).get_table_names())
            self.assertNotIn(old, tables)
            self.assertEqual([(0,)], self._rows(
                "SELECT count(*) FROM pg_type WHERE typname = 'tipoenum'"))
            stamp = self._rows(f"SELECT updated_at FROM {risks} WHERE id = 1")[0][0]
            self.assertEqual("2026-10-02", stamp.date().isoformat())
            self.assertEqual([
                (1, "Rotación de técnicos", "Rotación de técnicos",
                 "Plan de acción: Plan de carrera y salario"),
                (4, "Cambia de tipo", "Cambia de tipo", None),
                (5, "Corte de suministro", "Corte de suministro\nProveedor único",
                 "Objetivo de la calidad: Entregas a tiempo\n\n"
                 "Plan de acción: Segundo proveedor"),
            ], [row[:4] for row in self._rows(moved.format(risks))])
            self.assertEqual([(2, "Nuevo mercado", "Nuevo mercado",
                               "Objetivo de la calidad: Crecer un 10 %")],
                             [row[:4] for row in self._rows(moved.format(opportunities))])
            with db.engine.begin() as connection:
                self.assertEqual(6, connection.execute(text(
                    f"INSERT INTO {risks} (titulo, descripcion, origen, fecha_identificacion, "
                    "notas) VALUES ('Nuevo', 'Riesgo nuevo', 'externo', '2026-10-17', "
                    "'Nota libre') RETURNING id")).scalar_one())
                self.assertEqual(3, connection.execute(text(
                    f"INSERT INTO {opportunities} (titulo, descripcion, origen, "
                    "fecha_identificacion) VALUES ('Nueva', 'Oportunidad nueva', 'interno', "
                    "'2026-10-17') RETURNING id")).scalar_one())
                for statement in (
                    "INSERT INTO personas (nombre) VALUES ('Ana')",
                    f"UPDATE {risks} SET probabilidad = 4, impacto = 4, "
                    "responsable_id = (SELECT id FROM personas) WHERE id = 1",
                    f"INSERT INTO {actions} (riesgo_id, descripcion, responsable_id, "
                    f"fecha_limite) SELECT 1, 'Plan', id, '2026-12-31' FROM personas",
                ):
                    connection.execute(text(statement))
            downgrade(directory=MIGRATIONS_DIR, revision="c6e8a2f4b1d9")
            # Risks come back first, then opportunities, each in id order, with new ids.
            self.assertEqual([
                (1, "Riesgo", "Rotación de técnicos", None, "Plan de carrera y salario"),
                (2, "Riesgo", "Cambia de tipo", None, None),
                (3, "Riesgo", "Corte de suministro\nProveedor único", "Entregas a tiempo",
                 "Segundo proveedor"),
                (4, "Riesgo", "Riesgo nuevo", None, "Nota libre"),
                (5, "Oportunidad", "Nuevo mercado", "Crecer un 10 %", None),
                (6, "Oportunidad", "Oportunidad nueva", None, None),
            ], [row[:5] for row in self._rows(legacy)])
            self.assertEqual(stamp, self._rows(legacy)[0][6])
            # The registers are rebuilt from it, as RO-1 left them: keyed by the old ids.
            ids = "SELECT id FROM {} ORDER BY id"
            self.assertEqual([(1,), (2,), (3,), (4,)], self._rows(ids.format(risks)))
            self.assertEqual([(5,), (6,)], self._rows(ids.format(opportunities)))
            self.assertEqual([(0, None)], self._rows(
                f"SELECT (SELECT count(*) FROM {actions}), max(probabilidad) FROM {risks}"))
            with db.engine.begin() as connection:
                self.assertEqual(7, connection.execute(text(
                    f"INSERT INTO {old} (tipo, descripcion) VALUES ('Riesgo', 'Otro') "
                    "RETURNING id_riesgo")).scalar_one())
            upgrade(directory=MIGRATIONS_DIR)
            self.assertEqual([(1,), (2,), (3,), (4,), (7,)], self._rows(ids.format(risks)))
            self.assertEqual([], self._schema_differences())


if __name__ == "__main__":
    unittest.main()
