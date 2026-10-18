"""Process and role registers: model, services and MCP fields (RG-1 of ``process-role-registers``).

ISO 9001 clauses 4.4, 8.1 and 5.3, decisions PR1, PR3 and PR5. A process gains a
type, an owner, its inputs, outputs, resources and acceptance criteria and the
processes it interacts with: a mutual relation stored once per pair, lower id
first, never with the process itself. A role gains its responsibilities, its
authorities and the role it reports to, never itself nor a role below it in the
line. Related processes, roles others report to and people owning a process are
not deleted; ``cited_by`` and ``holders`` gather what RG-2's pages show. The
SQLite cases run with the audit flush guard; the database cases also run on
PostgreSQL, which names the foreign key a delete breaks.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import ADMIN, AUDITOR, OPERATIVO, McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, errors

from app.extensions import db
from app.models import (
    AuditLog, CompetenceRequirement, CompetenceType, FrecuenciaMedicion, ObjetivoCalidad,
    Oportunidad, Person, ProcesoOperacion, Riesgo, RolResponsabilidad, RoleEnum,
    SentidoObjetivo, persona_roles,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=RoleEnum.ADMINISTRADOR, channel="cli")
OBJECTIVE = {"titulo": "Entregas a tiempo", "indicador": "% de pedidos a tiempo", "meta": 95,
             "sentido": SentidoObjetivo.mayor_igual, "anio": 2026,
             "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2026, 12, 31),
             "frecuencia": FrecuenciaMedicion.mensual}
RISK = {"titulo": "Fallo de proveedor", "descripcion": "Proveedor único", "origen": "externo",
        "fecha_identificacion": date(2026, 10, 1)}
OPPORTUNITY = {"titulo": "Nuevo mercado", "descripcion": "Sector naval", "origen": "externo",
               "fecha_identificacion": date(2026, 10, 2)}


def models():
    import app.models as module

    return module


def po():
    from app.services import process_operations

    return process_operations


def rr():
    from app.services import roles_responsibilities

    return roles_responsibilities


def people():
    from app.services import people as module

    return module


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        return self.insert(Person.__table__, nombre=nombre, activo=activo)

    def process(self, nombre: str = "Compras", **values) -> int:
        return self.insert(ProcesoOperacion.__table__, proceso=nombre, **values)

    def role(self, nombre: str = "Dirección", **values) -> int:
        return self.insert(RolResponsabilidad.__table__, rol=nombre, **values)

    def relate(self, process_id: int, related: list[int]) -> None:
        po().update(db.session, CLI, process_id, {"relacionados": related})
        db.session.commit()

    def pairs(self) -> list[tuple]:
        return [tuple(row) for row in db.session.execute(text(
            "SELECT proceso_id, relacionado_id FROM proceso_relaciones ORDER BY 1, 2"))]

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message


class ProcessFieldsTestCase(Helpers, ServiceBase):
    def test_a_process_records_its_type_owner_and_what_it_takes_and_delivers(self) -> None:
        tipo = models().TipoProceso
        ana = self.person()
        created = po().create(db.session, CLI, {
            "proceso": "Compras", "tipo": "operativo", "responsable_id": ana,
            "entradas": "Solicitudes de compra", "salidas": "Pedidos a proveedor",
            "recursos": "ERP", "criterio_calidad": "Proveedores homologados",
            "criterios_aceptacion": "Recepción conforme"})
        db.session.commit()
        self.assertEqual(
            (tipo.operativo, ana, "Solicitudes de compra", "Pedidos a proveedor", "ERP",
             "Recepción conforme"),
            (created.tipo, created.responsable_id, created.entradas, created.salidas,
             created.recursos, created.criterios_aceptacion))
        after = self.rows("procesos_operacion")[-1].after
        self.assertEqual(("Operativo", ana, []), (after["tipo"], after["responsable_id"],
                                                   after["relacionados"]))
        changed = po().update(db.session, CLI, created.id_proceso,
                              {"tipo": tipo.soporte, "responsable_id": None})
        self.assertEqual((tipo.soporte, None), (changed.tipo, changed.responsable_id))
        self.assertIsNone(po().update(db.session, CLI, created.id_proceso, {"tipo": ""}).tipo)
        legacy = po().create(db.session, CLI, {"proceso": "Ventas"})
        self.assertEqual((None, None, None, None),
                         (legacy.tipo, legacy.responsable_id, legacy.entradas,
                          legacy.criterios_aceptacion))
        self.assertEqual(["Estratégico", "Operativo", "Soporte"], [t.value for t in tipo])

    def test_an_unknown_type_or_owner_and_unknown_keys_are_refused(self) -> None:
        inactive = self.person("Eva Gil", activo=False)
        for data, message in (
            ({"tipo": "clave"}, "El valor del campo «tipo» no es válido."),
            ({"responsable_id": 999},
             "El campo «responsable_id» no corresponde a ninguna persona."),
            ({"responsable_id": inactive},
             "La persona del campo «responsable_id» está desactivada; elige una persona activa."),
            ({"responsable_id": "1"}, "El campo «responsable_id» debe ser un número entero."),
            ({"interacciones": "Ventas"}, "Campos no permitidos: interacciones."),
        ):
            with self.subTest(data=data):
                self.assertEqual(message, self.refused(errors().ValidationError, po().create,
                                                       CLI, {"proceso": "Compras"} | data))
        self.assertEqual(0, db.session.query(ProcesoOperacion).count())

    def test_lists_filter_by_type_and_owner(self) -> None:
        tipo = models().TipoProceso
        ana, eva = self.person(), self.person("Eva Gil")
        compras = self.process("Compras", tipo=tipo.operativo, responsable_id=ana)
        ventas = self.process("Ventas", tipo=tipo.operativo, responsable_id=eva)
        direccion = self.process("Dirección", tipo=tipo.estrategico, responsable_id=ana)
        legado = self.process("Legado")
        items, total = po().list_page(db.session, CLI, tipo="operativo", page=1, per_page=1)
        self.assertEqual((2, [compras]), (total, [p.id_proceso for p in items]))
        for filters, expected in (
            ({"tipo": tipo.operativo, "responsable_id": eva}, [ventas]),
            ({"responsable_id": ana, "tipo": "operativo"}, [compras]),
            ({"responsable_id": 2**40}, []),
            ({}, [compras, ventas, direccion, legado]),
        ):
            with self.subTest(filters=filters):
                self.assertEqual(expected, [p.id_proceso for p in
                                            po().list_(db.session, CLI, **filters)])
        self.assertEqual("El filtro «tipo» no es válido.",
                         self.refused(errors().ValidationError, po().list_, CLI, tipo="clave"))

    def test_a_person_owning_a_process_cannot_be_deleted(self) -> None:
        ana, eva = self.person(), self.person("Eva Gil")
        self.process(responsable_id=ana)
        self.assertEqual(people().STILL_REFERENCED,
                         self.refused(errors().Conflict, people().delete, CLI, ana))
        people().delete(db.session, CLI, eva)  # control: nothing cites her
        db.session.commit()
        self.assertEqual([ana], [p.id for p in db.session.query(Person)])


class ProcessRelationsTestCase(Helpers, ServiceBase):
    def test_relations_are_mutual_and_each_pair_is_stored_once_lower_id_first(self) -> None:
        compras, ventas, almacen = (self.process(n) for n in ("Compras", "Ventas", "Almacén"))
        self.relate(almacen, [ventas, compras, compras])
        self.assertEqual([(compras, almacen), (ventas, almacen)], self.pairs())
        self.assertEqual([compras, ventas], po().related_ids(db.session, almacen))
        self.assertEqual([almacen], po().related_ids(db.session, compras))
        self.assertEqual(["Compras", "Ventas"],
                         [p.proceso for p in po().related(db.session, CLI, almacen)])
        self.assertEqual(["Almacén"], [p.proceso for p in po().related(db.session, CLI, ventas)])
        self.relate(compras, [])  # the other side sees and drops the same pair
        self.assertEqual([(ventas, almacen)], self.pairs())
        self.assertEqual(po().SPEC.not_found,
                         self.refused(errors().NotFound, po().related, CLI, 999))

    def test_relacionados_replaces_the_set_and_the_audit_rows_carry_it(self) -> None:
        compras, ventas, almacen = (self.process(n) for n in ("Compras", "Ventas", "Almacén"))
        created = po().create(db.session, CLI, {"proceso": "Calidad",
                                                "relacionados": [ventas, compras]})
        db.session.commit()
        calidad = created.id_proceso
        self.assertEqual([compras, ventas], self.rows("procesos_operacion")[-1].after["relacionados"])
        self.relate(calidad, [compras, ventas])  # the same set: nothing is written
        self.assertEqual(["create"], [r.action for r in self.rows("procesos_operacion")])
        self.relate(calidad, [almacen])
        row = self.rows("procesos_operacion")[-1]
        self.assertEqual(("update", [compras, ventas], [almacen]),
                         (row.action, row.before["relacionados"], row.after["relacionados"]))
        self.assertEqual([(almacen, calidad)], self.pairs())
        po().update(db.session, CLI, calidad, {"proceso": "Gestión de la calidad"})
        db.session.commit()
        self.assertNotIn("relacionados", self.rows("procesos_operacion")[-1].after)
        self.relate(calidad, None)  # None clears the set, like []
        self.assertEqual(([], []), (self.pairs(), po().related_ids(db.session, calidad)))

    def test_unknown_ids_the_process_itself_and_malformed_lists_are_refused(self) -> None:
        compras = self.process()
        malformed = "El campo «relacionados» debe ser una lista de identificadores de proceso."
        for value, message in (
            ([999, compras, 2**40], "Procesos desconocidos en «relacionados»: 999, 1099511627776."),
            ("1", malformed), ([True], malformed), ([str(compras)], malformed),
            (compras, malformed),
        ):
            with self.subTest(value=value):
                self.assertEqual(message, self.refused(
                    errors().ValidationError, po().create, CLI,
                    {"proceso": "Ventas", "relacionados": value}))
        self.assertEqual("Un proceso no puede relacionarse consigo mismo.", self.refused(
            errors().ValidationError, po().update, CLI, compras, {"relacionados": [compras]}))
        self.assertEqual(([], 1), (self.pairs(), db.session.query(ProcesoOperacion).count()))
        self.assertEqual(po().WRITABLE_FIELDS, po().SPEC.writable | {"relacionados"})

    def test_a_related_process_cannot_be_deleted_until_its_relations_go(self) -> None:
        compras, ventas = self.process("Compras"), self.process("Ventas")
        self.relate(compras, [ventas])
        for process_id in (compras, ventas):
            with self.subTest(process=process_id):
                self.assertEqual(
                    "No se puede eliminar un proceso relacionado con otros procesos; "
                    "quita antes esas relaciones.",
                    self.refused(errors().Conflict, po().delete, CLI, process_id))
        self.relate(ventas, [])
        po().delete(db.session, CLI, ventas)
        db.session.commit()
        self.assertIsNone(db.session.get(ProcesoOperacion, ventas))
        self.assertEqual("delete", self.rows("procesos_operacion")[-1].action)


class CitationsAndHoldersTestCase(Helpers, ServiceBase):
    def test_cited_by_gathers_the_objectives_risks_and_opportunities_of_a_process(self) -> None:
        ana = self.person()
        compras, ventas = self.process("Compras"), self.process("Ventas")
        objective = self.insert(ObjetivoCalidad.__table__, **OBJECTIVE, proceso_id=compras,
                                responsable_id=ana)
        self.insert(ObjetivoCalidad.__table__, **OBJECTIVE, proceso_id=ventas, responsable_id=ana)
        risk = self.insert(Riesgo.__table__, **RISK, proceso_id=compras)
        opportunity = self.insert(Oportunidad.__table__, **OPPORTUNITY, proceso_id=compras)
        self.insert(Riesgo.__table__, **RISK)
        found = po().cited_by(db.session, CLI, compras)
        self.assertEqual(([objective], [risk], [opportunity]),
                         ([o.id for o in found.objetivos], [r.id for r in found.riesgos],
                          [o.id for o in found.oportunidades]))
        empty = po().cited_by(db.session, CLI, self.process("Almacén"))
        self.assertEqual(([], [], []), (empty.objetivos, empty.riesgos, empty.oportunidades))
        self.assertEqual(po().SPEC.not_found,
                         self.refused(errors().NotFound, po().cited_by, CLI, 999))
        unread = Actor(user_id=None, label="w", role=RoleEnum.ADMINISTRADOR, channel="mcp",
                       scopes=frozenset({"write"}))
        self.refused(errors().PermissionDenied, po().cited_by, unread, compras)

    def test_holders_are_the_people_holding_a_role_and_its_requirements(self) -> None:
        calidad, compras = self.role("Calidad"), self.role("Compras")
        bea, ana, eva = self.person("Bea"), self.person("Ana"), self.person("Eva", activo=False)
        for person_id, role_id in ((bea, calidad), (ana, calidad), (eva, calidad), (ana, compras)):
            self.insert(persona_roles, persona_id=person_id, rol_id=role_id)
        requirement = CompetenceRequirement.__table__
        first = self.insert(requirement, rol_id=calidad, tipo=CompetenceType.formacion,
                            descripcion="ISO 9001")
        second = self.insert(requirement, rol_id=calidad, tipo=CompetenceType.experiencia,
                             descripcion="Dos años en calidad")
        self.insert(requirement, rol_id=compras, tipo=CompetenceType.formacion,
                    descripcion="Negociación")
        found = rr().holders(db.session, CLI, calidad)
        self.assertEqual((["Ana", "Bea", "Eva"], [first, second]),
                         ([p.nombre for p in found.personas], [r.id for r in found.requisitos]))
        empty = rr().holders(db.session, CLI, self.role("Ventas"))
        self.assertEqual(([], []), (empty.personas, empty.requisitos))
        self.assertEqual(rr().SPEC.not_found,
                         self.refused(errors().NotFound, rr().holders, CLI, 999))


class RoleLineTestCase(Helpers, ServiceBase):
    def test_a_role_records_its_responsibilities_authorities_and_reporting_line(self) -> None:
        direccion = self.role()
        created = rr().create(db.session, CLI, {
            "rol": "Responsable de calidad", "responsabilidades": "Mantener el SGC",
            "autoridades": "Detener envíos no conformes", "reporta_a_id": direccion})
        db.session.commit()
        self.assertEqual(("Mantener el SGC", "Detener envíos no conformes", direccion),
                         (created.responsabilidades, created.autoridades, created.reporta_a_id))
        self.assertEqual(direccion, self.rows("roles_responsabilidades")[-1].after["reporta_a_id"])
        self.assertIsNone(rr().update(db.session, CLI, created.id_rol,
                                      {"reporta_a_id": None}).reporta_a_id)

    def test_the_role_reported_to_must_exist_and_never_be_the_role_itself(self) -> None:
        direccion = self.role()
        unknown = "El campo «reporta_a_id» no corresponde a ningún rol."
        for value, message in ((999, unknown), (2**40, unknown),
                               ("1", "El campo «reporta_a_id» debe ser un número entero.")):
            with self.subTest(value=value):
                self.assertEqual(message, self.refused(errors().ValidationError, rr().create, CLI,
                                                       {"rol": "Calidad", "reporta_a_id": value}))
        self.assertEqual("Un rol no puede reportar a sí mismo.", self.refused(
            errors().ValidationError, rr().update, CLI, direccion, {"reporta_a_id": direccion}))
        self.assertEqual(unknown, self.refused(errors().ValidationError, rr().update, CLI,
                                               direccion, {"reporta_a_id": 999}))

    def test_a_reporting_line_never_closes_a_cycle(self) -> None:
        cycle = ("La línea de reporte no puede formar un ciclo: ese rol ya reporta, "
                 "directa o indirectamente, a este.")
        a = self.role("A")
        b = self.role("B", reporta_a_id=a)
        c = self.role("C", reporta_a_id=b)
        for manager in (c, b):  # A → C → B → A, and A → B → A
            with self.subTest(manager=manager):
                self.assertEqual(cycle, self.refused(errors().ValidationError, rr().update, CLI,
                                                     a, {"reporta_a_id": manager}))
        self.assertIsNone(db.session.get(RolResponsabilidad, a).reporta_a_id)
        rr().update(db.session, CLI, c, {"reporta_a_id": a})  # roles move inside the line
        rr().update(db.session, CLI, b, {"reporta_a_id": c})  # now B → C → A
        db.session.commit()
        rr().update(db.session, CLI, b, {"reporta_a_id": c})  # unchanged: nothing written
        self.assertEqual(["update", "update"],
                         [r.action for r in self.rows("roles_responsabilidades")])
        self.assertEqual(cycle, self.refused(errors().ValidationError, rr().update, CLI, a,
                                             {"reporta_a_id": b}))

    def test_a_role_others_report_to_cannot_be_deleted(self) -> None:
        direccion = self.role()
        calidad = self.role("Calidad", reporta_a_id=direccion)
        self.assertEqual(
            "No se puede eliminar un rol al que reportan otros roles; cambia antes a quién "
            "reportan.", self.refused(errors().Conflict, rr().delete, CLI, direccion))
        rr().delete(db.session, CLI, calidad)  # reporting to a role does not hold the reporter
        rr().delete(db.session, CLI, direccion)
        db.session.commit()
        self.assertEqual(0, db.session.query(RolResponsabilidad).count())


class DatabaseCases:
    """The schema guards the new links itself; a delete the checks missed still fails."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_schema_orders_relations_and_keeps_roles_off_their_own_line(self) -> None:
        self.enforce_foreign_keys()
        a, b = self.process("Compras"), self.process("Ventas")
        direccion = self.role()
        self.insert(models().proceso_relaciones, proceso_id=a, relacionado_id=b)
        relation = "INSERT INTO proceso_relaciones (proceso_id, relacionado_id) VALUES (:x, :y)"
        for statement, params in (
            (relation, {"x": a, "y": a}),  # itself
            (relation, {"x": b, "y": a}),  # the same pair, higher id first
            (relation, {"x": a, "y": b}),  # stored once
            (relation, {"x": a, "y": 999}),  # unknown process
            ("UPDATE procesos_operacion SET tipo = 'clave'", {}),
            ("UPDATE procesos_operacion SET responsable_id = 999", {}),
            ("UPDATE roles_responsabilidades SET reporta_a_id = :x", {"x": direccion}),
            ("UPDATE roles_responsabilidades SET reporta_a_id = 999", {}),
            ("DELETE FROM procesos_operacion WHERE id_proceso = :x", {"x": b}),
        ):
            with self.subTest(refused=statement, params=params):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement), params)
                    db.session.commit()
                db.session.rollback()
        self.assertEqual([(a, b)], self.pairs())

    def test_foreign_keys_refuse_deletes_the_checks_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        ana = self.person()
        compras, ventas = self.process("Compras", responsable_id=ana), self.process("Ventas")
        self.relate(compras, [ventas])
        direccion = self.role()
        self.role("Calidad", reporta_a_id=direccion)
        named = self.names_violated_constraints
        cases = (
            (po(), "_has_relations", po().delete, compras,
             po().HAS_RELATIONS if named else errors().Conflict.default_message),
            (po(), "_has_relations", po().delete, ventas,
             po().HAS_RELATIONS if named else errors().Conflict.default_message),
            (rr(), "_has_subordinates", rr().delete, direccion,
             rr().HAS_SUBORDINATES if named else rr().IN_USE),
            (people(), "_is_referenced", people().delete, ana, people().STILL_REFERENCED),
        )
        for module, check, delete, record_id, expected in cases:
            with self.subTest(check=check, record=record_id):
                with patch.object(module, check, return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        delete(db.session, CLI, record_id)
                db.session.rollback()
                self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                self.assertEqual(expected, caught.exception.message)
        self.assertEqual((2, 2, 1), (db.session.query(ProcesoOperacion).count(),
                                     db.session.query(RolResponsabilidad).count(),
                                     db.session.query(Person).count()))


class SqliteDatabaseTestCase(DatabaseCases, Helpers, ServiceBase):
    names_violated_constraints = False

    def tearDown(self) -> None:
        # Dropping a table deletes its rows, which a role reporting to another refuses.
        db.session.rollback()
        db.session.execute(text("PRAGMA foreign_keys=OFF"))
        db.session.commit()
        super().tearDown()

    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDatabaseTestCase(DatabaseCases, Helpers, unittest.TestCase):
    """The same cases against PostgreSQL, whose foreign keys are always enforced."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


class McpTestCase(McpDbCase):
    async def test_processes_and_roles_round_trip_their_new_fields(self) -> None:
        ana = self.seed("personas")
        ventas = self.seed("procesos", proceso="Ventas")
        who = mcp_actor(AUDITOR)
        created = await self.call(who, "qms_create", {"module": "procesos", "data": {
            "proceso": "Compras", "tipo": "estrategico", "responsable_id": ana,
            "entradas": "Necesidades", "salidas": "Pedidos", "recursos": "ERP",
            "criterios_aceptacion": "Recepción conforme", "relacionados": [ventas]}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("Estratégico", ana, [ventas], "Recepción conforme"),
                         tuple(record[k] for k in ("tipo", "responsable_id", "relacionados",
                                                   "criterios_aceptacion")))
        other = await self.call(who, "qms_get", {"module": "procesos", "id": ventas})
        self.assertEqual([record["id"]], other.structured_content["relacionados"])
        for filters, total in (({"tipo": "estrategico"}, 1), ({"tipo": "soporte"}, 0),
                               ({"responsable_id": ana}, 1),
                               ({"responsable_id": ana, "tipo": "operativo"}, 0)):
            with self.subTest(filters=filters):
                listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                         {"module": "procesos", "filters": filters})
                self.assertFalse(listed.is_error, listed.content)
                self.assertEqual(total, listed.structured_content["total"])
        bad = await self.call(who, "qms_list", {"module": "procesos", "filters": {"tipo": "clave"}})
        self.assertTrue(bad.is_error)
        itself = await self.call(who, "qms_update", {"module": "procesos", "id": ventas,
                                                     "data": {"relacionados": [ventas]}})
        self.assertTrue(itself.is_error)
        self.assertIn("consigo mismo", itself.content[0].text)
        cleared = await self.call(who, "qms_update", {"module": "procesos", "id": record["id"],
                                                      "data": {"relacionados": []}})
        self.assertEqual([], cleared.structured_content["relacionados"])
        direccion = self.seed("roles_responsabilidades", rol="Dirección")
        calidad = await self.call(who, "qms_create", {"module": "roles_responsabilidades", "data": {
            "rol": "Calidad", "responsabilidades": "Mantener el SGC",
            "autoridades": "Detener envíos", "reporta_a_id": direccion}})
        self.assertFalse(calidad.is_error, calidad.content)
        role = calidad.structured_content
        self.assertEqual((direccion, "Mantener el SGC", "Detener envíos"),
                         (role["reporta_a_id"], role["responsabilidades"], role["autoridades"]))
        cycle = await self.call(who, "qms_update", {"module": "roles_responsabilidades",
                                                    "id": direccion,
                                                    "data": {"reporta_a_id": role["id"]}})
        self.assertTrue(cycle.is_error)
        self.assertIn("ciclo", cycle.content[0].text)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        processes = {f["name"]: f for f in described["procesos"]["fields"]}
        self.assertEqual(("integer_list", ["estrategico", "operativo", "soporte"], "integer"),
                         (processes["relacionados"]["type"], processes["tipo"]["allowed"],
                          processes["responsable_id"]["type"]))
        self.assertLessEqual({"entradas", "salidas", "recursos", "criterios_aceptacion"},
                             set(processes))
        self.assertEqual({"tipo", "responsable_id"},
                         {f["name"] for f in described["procesos"]["filters"]})
        roles = {f["name"]: f for f in described["roles_responsabilidades"]["fields"]}
        self.assertEqual(("integer", "string", "string"),
                         (roles["reporta_a_id"]["type"], roles["responsabilidades"]["type"],
                          roles["autoridades"]["type"]))


if __name__ == "__main__":
    unittest.main()
