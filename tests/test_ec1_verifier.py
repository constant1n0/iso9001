"""Independent EC1 regression probes: S1 integration and S2 recovery."""
import hashlib
import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, inspect, text
from sqlalchemy.dialects import postgresql
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_nonconformity_service import ServiceBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import equipment, people
from app.services.actor import Actor
from app.services.errors import ValidationError, PermissionDenied

DAY = date(2026, 11, 2)
ADMIN = Actor(None, "verifier", RoleEnum.ADMINISTRADOR, "cli")
PASS = dict(fecha=DAY, tipo="verificacion", resultado="apto", realizado_por="Lab",
            referencia="REF-1", fecha_proxima=DAY + timedelta(days=1))
FAIL = dict(resultado="no_apto", fecha_proxima=None,
            evaluacion_impacto="Revisadas mediciones de lote A", acciones="Revisar lote A")


class IndependentEquipment(ServiceBase):
    def setUp(self):
        super().setUp()
        self.item = equipment.equipment.create(db.session, ADMIN,
                                               dict(codigo="I-1", nombre="Balanza"))
        db.session.commit()
        self.payload = PASS | dict(equipo_id=self.item.id)

    def digest(self):
        raw = db.session.connection().connection.driver_connection.serialize()
        return hashlib.sha256(raw).hexdigest()

    def record(self, **changes):
        row = equipment.checks.create(db.session, ADMIN, self.payload | changes, today=DAY)
        db.session.commit()
        return row

    def refused(self, payload, message):
        before = self.digest()
        with self.assertRaises(ValidationError) as caught:
            equipment.checks.create(db.session, ADMIN, payload, today=DAY)
        self.assertEqual(message, caught.exception.message)
        db.session.rollback()
        self.assertEqual(before, self.digest())

    def test_s2_failure_not_cleared_by_backdating_or_withdrawal(self):
        self.record(fecha=DAY - timedelta(days=1))
        failure = self.record(**FAIL)
        for changes in ({"fecha": DAY - timedelta(days=1)},
                        FAIL | {"fecha": DAY - timedelta(days=1)}):
            self.refused(self.payload | changes,
                         "La comprobación no puede ser anterior a la última registrada.")
        for withdrawn, state in ((True, "retirado"), (False, "no_apto")):
            equipment.equipment.update(db.session, ADMIN, self.item.id, {"retirado": withdrawn})
            db.session.commit()
            self.assertEqual(state, equipment.status(db.session, self.item, today=DAY))
        recovered = self.record()
        self.assertGreater(recovered.id, failure.id)
        self.assertEqual("apto", equipment.status(db.session, self.item, today=DAY))
        self.record(**FAIL)
        self.assertEqual("no_apto", equipment.status(db.session, self.item, today=DAY))
        self.assertEqual(4, equipment.checks.list_page(db.session, ADMIN)[1])
        self.assertEqual(7, len(self.audit_rows()))

    def test_s2_exact_validation_and_byte_identical_rejections(self):
        cases = [
            ({"fecha_proxima": None}, "Un resultado apto requiere una próxima fecha posterior a la comprobación."),
            ({"fecha_proxima": DAY}, "Un resultado apto requiere una próxima fecha posterior a la comprobación."),
            (FAIL | {"fecha_proxima": DAY + timedelta(days=2)}, "Un resultado no apto no admite próxima fecha; registra una nueva comprobación para recuperarlo."),
            (FAIL | {"evaluacion_impacto": "\t\n"}, "Un resultado no apto requiere evaluar las mediciones anteriores e indicar las acciones."),
            (FAIL | {"acciones": " "}, "Un resultado no apto requiere evaluar las mediciones anteriores e indicar las acciones."),
            ({"fecha": DAY + timedelta(days=1)}, "La fecha de comprobación no puede ser futura."),
            ({"equipo_id": 0}, "El campo «equipo_id» no corresponde a ningún equipo."),
            ({"equipo_id": True}, "El campo «equipo_id» debe ser un número entero."),
            ({"referencia": " "}, "El campo «referencia» es obligatorio."),
            ({"resultado": "Apto"}, "El valor del campo «resultado» no es válido."),
            ({"certificado": "x" * 201}, "El campo «certificado» admite como máximo 200 caracteres."),
            ({"estado": "apto"}, "Campos no permitidos: estado."),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                self.refused(self.payload | changes, message)
        self.assertEqual(1, len(self.audit_rows()))
        self.assertEqual(0, equipment.checks.list_page(db.session, ADMIN)[1])

    def test_s1_s2_locks_compile_without_joins_and_never_end_transaction(self):
        statements = []
        def observed(state):
            if getattr(state.statement, "_for_update_arg", None) is not None:
                statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
        session = db.session()
        event.listen(session, "do_orm_execute", observed)
        try:
            with patch.object(session, "commit", wraps=session.commit) as commit, \
                 patch.object(session, "rollback", wraps=session.rollback) as rollback:
                equipment.equipment.update(session, ADMIN, self.item.id, {"nombre": "Nueva"})
                transaction = session.get_transaction()
                check = equipment.checks.create(session, ADMIN, self.payload, today=DAY)
                self.assertIs(transaction, session.get_transaction())
                self.assertTrue(transaction.is_active)
                commit.assert_not_called()
                rollback.assert_not_called()
            session.rollback()
            self.assertEqual(0, equipment.checks.list_page(session, ADMIN)[1])
            equipment.equipment.delete(session, ADMIN, self.item.id)
            session.rollback()
        finally:
            event.remove(session, "do_orm_execute", observed)
        self.assertEqual(3, len(statements))
        for sql in statements:
            self.assertIn("FOR UPDATE", sql)
            self.assertNotIn("JOIN", sql)

    def test_s1_migration_matches_orm_and_preserves_unrelated_data(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/d7f9a1b3c5e7_add_equipment_calibration.py"
        spec = importlib.util.spec_from_file_location("ec1_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        db.session.remove()
        with db.engine.begin() as connection:
            def signatures():
                inspector = inspect(connection)
                result = {}
                for table in ("equipos_medicion", "comprobaciones_equipo"):
                    columns = [(c["name"], str(c["type"]), c["nullable"], c["default"],
                                getattr(c["type"], "timezone", None))
                               for c in inspector.get_columns(table)]
                    result[table] = (columns, inspector.get_pk_constraint(table),
                        sorted(inspector.get_foreign_keys(table), key=lambda x: x["name"]),
                        sorted(inspector.get_indexes(table), key=lambda x: x["name"]),
                        sorted(inspector.get_unique_constraints(table), key=lambda x: str(x["name"])),
                        sorted(inspector.get_check_constraints(table), key=lambda x: x["name"]))
                return result
            expected = signatures()
            equipment_before = connection.scalar(text("SELECT count(*) FROM personas"))
            connection.execute(text("DROP TABLE comprobaciones_equipo"))
            connection.execute(text("DROP TABLE equipos_medicion"))
            with patch.object(module, "op", Operations(MigrationContext.configure(connection))):
                module.upgrade()
                self.assertEqual(expected, signatures())
                module.downgrade()
                self.assertNotIn("equipos_medicion", inspect(connection).get_table_names())
                self.assertEqual(equipment_before, connection.scalar(text("SELECT count(*) FROM personas")))
                module.upgrade()
                self.assertEqual(expected, signatures())


class IndependentMcp(McpDbCase):
    async def test_s1_s2_round_trip_filters_and_immutable_evidence(self):
        master = await self.call(mcp_actor(), "qms_create", {
            "module": "equipos_medicion", "data": {"codigo": "M-1", "nombre": "Micrómetro"}})
        self.assertFalse(master.is_error, master.content)
        identity = master.structured_content["id"]
        payload = {key: value.isoformat() if isinstance(value, date) else value
                   for key, value in (PASS | FAIL | {"equipo_id": identity}).items()}
        failed = await self.call(mcp_actor(RoleEnum.AUDITOR), "qms_create", {
            "module": "comprobaciones_equipo", "data": payload})
        self.assertFalse(failed.is_error, failed.content)
        args = {"module": "comprobaciones_equipo", "id": failed.structured_content["id"]}
        before = await self.call(mcp_actor(), "qms_get", args)
        refused = await self.call(mcp_actor(), "qms_update", args | {"data": {"acciones": "Ocultar"}})
        self.assertTrue(refused.is_error)
        self.assertEqual("Error executing tool qms_update: Las comprobaciones no se modifican ni se eliminan; registra una nueva.", refused.content[0].text)
        after = await self.call(mcp_actor(), "qms_get", args)
        self.assertEqual(before.structured_content, after.structured_content)
        for role in RoleEnum:
            read = await self.call(mcp_actor(role), "qms_get", {"module": "equipos_medicion", "id": identity})
            self.assertFalse(read.is_error, read.content)
            self.assertEqual("no_apto", read.structured_content["estado"])
            self.assertIsNone(read.structured_content["fecha_proxima"])
        listed = await self.call(mcp_actor(), "qms_list", {"module": "comprobaciones_equipo", "filters": {"equipo_id": identity, "resultado": "no_apto"}})
        self.assertEqual(1, listed.structured_content["total"])
        self.assertEqual(before.structured_content, listed.structured_content["items"][0])
        self.assertEqual(2, db.session.scalar(text("SELECT count(*) FROM audit_logs")))
