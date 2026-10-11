"""Audit findings and their automatic nonconformities (AP-2: decisions AP4, AP5 and AP7).

A finding belongs to one audit and is a major or minor nonconformity, an
observation or an opportunity for improvement, with an optional ISO clause
(dotted numbers from 4 to 10) and evidence. A nonconformity finding creates its
nonconformity in the same transaction (origin ``auditoria``, its severity, the
audit's area and date, the audited-area owner as responsible) and keeps the
link; downgrading or deleting the finding never deletes that nonconformity.
Findings share the ``AUDITS`` grant; a cancelled audit takes no new or edited
findings, and an audit with findings cannot be deleted.

The SQLite cases run with the audit flush guard (``ServiceBase``). The foreign
keys are exercised on SQLite with ``PRAGMA foreign_keys=ON`` and on PostgreSQL
when ``TEST_POSTGRES_URI`` is set (CI provides it).
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    Auditoria, AuditLog, EstadoAuditoriaEnum, EstadoNoConformidad, GravedadNoConformidad,
    NoConformidad, OrigenNoConformidad, Person, RoleEnum,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
AUDIT = {"area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Luis",
         "resultado": "Dos hallazgos"}
FINDING = {"tipo": "observacion", "descripcion": "Registro de compras sin firmar"}


def findings():
    from app.services import audit_findings

    return audit_findings


def finding_model():
    from app.models import HallazgoAuditoria

    return HallazgoAuditoria


def tipo(name: str):
    from app.models import TipoHallazgo

    return TipoHallazgo[name]


def audits():
    from app.services import audits as module

    return module


def nonconformities():
    from app.services import nonconformities as module

    return module


class Helpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        """Core insert: these tests must not depend on the people write API."""
        result = db.session.execute(Person.__table__.insert().values(nombre=nombre, activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def deactivate(self, person_id: int) -> None:
        table = Person.__table__
        db.session.execute(table.update().where(table.c.id == person_id).values(activo=False))
        db.session.commit()

    def new_audit(self, **overrides) -> Auditoria:
        found = audits().create(db.session, CLI, AUDIT | overrides)
        db.session.commit()
        return found

    def add(self, audit_id: int, who: Actor = CLI, **overrides):
        found = findings().create(db.session, who, {"auditoria_id": audit_id} | FINDING | overrides)
        db.session.commit()
        return found

    def edit(self, finding_id: int, data: dict, who: Actor = CLI):
        found = findings().update(db.session, who, finding_id, data)
        db.session.commit()
        return found

    def nc(self, nc_id: int) -> NoConformidad:
        db.session.expire_all()
        return db.session.get(NoConformidad, nc_id)

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def count(self, model) -> int:
        return db.session.query(model).count()

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message


class CreateTestCase(Helpers, ServiceBase):
    def test_each_type_is_recorded_and_only_nonconformities_link_one(self) -> None:
        found = self.new_audit()
        for name in ("nc_mayor", "nc_menor", "observacion", "oportunidad_mejora"):
            with self.subTest(tipo=name):
                created = self.add(found.id, actor(AUDITOR), tipo=name, clausula="8.5.1",
                                   evidencia="Pedido 42")
                self.assertEqual((found.id, tipo(name), "8.5.1", "Pedido 42"),
                                 (created.auditoria_id, created.tipo, created.clausula,
                                  created.evidencia))
                self.assertEqual(name.startswith("nc_"), created.no_conformidad_id is not None)
                self.assertEqual((7, 7), (created.created_by_id, created.updated_by_id))
                row = self.rows("hallazgos_auditoria")[-1]
                self.assertEqual(("create", created.id), (row.action, row.entity_id))
                self.assertEqual((tipo(name).value, created.no_conformidad_id),
                                 (row.after["tipo"], row.after["no_conformidad_id"]))
        self.assertEqual(
            ["No conformidad mayor", "No conformidad menor", "Observación",
             "Oportunidad de mejora"],
            [member.value for member in type(tipo("nc_mayor"))])
        self.assertEqual(2, self.count(NoConformidad))

    def test_a_nonconformity_finding_creates_its_nonconformity_with_mapped_fields(self) -> None:
        ana = self.person()
        found = self.new_audit(responsable_area_id=ana)
        for name, severity in (("nc_mayor", GravedadNoConformidad.mayor),
                               ("nc_menor", GravedadNoConformidad.menor)):
            with self.subTest(tipo=name):
                created = self.add(found.id, actor(AUDITOR), tipo=name,
                                   descripcion="  Pedido sin aprobar  ")
                nc = self.nc(created.no_conformidad_id)
                self.assertEqual(
                    ("Hallazgo de auditoría (Compras, 01/10/2026): Pedido sin aprobar",
                     date(2026, 10, 1), OrigenNoConformidad.auditoria, severity,
                     ana, "Ana Pérez", EstadoNoConformidad.abierta, 7),
                    (nc.descripcion, nc.fecha_detectada, nc.origen, nc.gravedad,
                     nc.responsable_id, nc.responsable, nc.estado, nc.created_by_id))
                created_rows = [row for row in self.rows("no_conformidades")
                                if row.entity_id == nc.id]
                self.assertEqual(["create"], [row.action for row in created_rows])
                self.assertEqual(("web", 7), (created_rows[0].channel,
                                              created_rows[0].actor_user_id))

    def test_without_an_active_owner_the_nonconformity_has_no_responsible(self) -> None:
        eva = self.person("Eva Gil")
        cases = {"no owner": self.new_audit(), "owner deactivated": self.new_audit(
            responsable_area_id=eva)}
        self.deactivate(eva)
        for name, found in cases.items():
            with self.subTest(case=name):
                created = self.add(found.id, tipo="nc_menor")
                nc = self.nc(created.no_conformidad_id)
                self.assertEqual((None, None), (nc.responsable_id, nc.responsable))

    def test_invalid_findings_are_refused_and_nothing_is_written(self) -> None:
        found = self.new_audit()
        valid = {"auditoria_id": found.id} | FINDING
        cases = {
            "missing audit": FINDING,
            "missing type": {k: v for k, v in valid.items() if k != "tipo"},
            "missing description": {k: v for k, v in valid.items() if k != "descripcion"},
            "unknown audit": valid | {"auditoria_id": 999},
            "audit id out of range": valid | {"auditoria_id": 2 ** 40},
            "audit id as text": valid | {"auditoria_id": "1"},
            "unknown type": valid | {"tipo": "grave"},
            "type by label": valid | {"tipo": "Observación"},
            "blank description": valid | {"descripcion": "  "},
            "evidence as a number": valid | {"evidencia": 5},
            "link is not writable": valid | {"no_conformidad_id": 1},
            "unknown field": valid | {"created_by_id": 1},
            "not a mapping": ["auditoria_id"],
            "nothing": None,
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.refused(errors().ValidationError, findings().create, actor(AUDITOR), data)
        self.assertEqual((0, 0), (self.count(finding_model()), self.count(NoConformidad)))
        self.assertEqual([], self.rows("hallazgos_auditoria"))

    def test_the_clause_is_a_dotted_number_from_4_to_10(self) -> None:
        found = self.new_audit()
        for clause in ("4", "10", "8.5.1", " 7.1.5.1 ", "10.3"):
            with self.subTest(clause=clause):
                self.assertEqual(clause.strip(), self.add(found.id, clausula=clause).clausula)
        for blank in ("", "   ", None):
            with self.subTest(clause=blank):
                self.assertIsNone(self.add(found.id, clausula=blank).clausula)
        for clause in ("3", "11", "3.1", "8,5", "8.5.", ".8", "8..5", "A.1", "08.1", "8.0",
                       "8.5.1.2.3", "8.5 a)", 851):
            with self.subTest(clause=clause):
                message = self.refused(errors().ValidationError, findings().create, CLI,
                                       {"auditoria_id": found.id, "clausula": clause} | FINDING)
                self.assertIn("cláusula", message)

    def test_a_cancelled_audit_takes_no_findings_but_a_completed_one_does(self) -> None:
        completed = self.new_audit(estado="COMPLETADA")
        kept = self.add(completed.id, tipo="nc_menor")
        self.assertEqual("Otra", self.edit(kept.id, {"descripcion": "Otra"}).descripcion)
        cancelled = self.new_audit()
        earlier = self.add(cancelled.id)
        audits().update(db.session, CLI, cancelled.id, {"estado": EstadoAuditoriaEnum.CANCELADA})
        db.session.commit()
        message = self.refused(errors().ValidationError, findings().create, CLI,
                               {"auditoria_id": cancelled.id} | FINDING)
        self.assertIn("cancelada", message)
        message = self.refused(errors().ValidationError, findings().update, CLI, earlier.id,
                               {"tipo": "nc_mayor"})
        self.assertIn("cancelada", message)
        self.assertEqual((tipo("observacion"), None),
                         (earlier.tipo, earlier.no_conformidad_id))
        self.assertEqual(1, self.count(NoConformidad))


class TypeChangeTestCase(Helpers, ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.ana = self.person()
        self.audit = self.new_audit(responsable_area_id=self.ana)

    def test_an_observation_that_becomes_a_nonconformity_creates_it_then(self) -> None:
        created = self.add(self.audit.id, descripcion="Firma ausente")
        self.assertIsNone(created.no_conformidad_id)
        changed = self.edit(created.id, {"tipo": "nc_mayor"}, actor(AUDITOR))
        nc = self.nc(changed.no_conformidad_id)
        self.assertEqual(("Hallazgo de auditoría (Compras, 01/10/2026): Firma ausente",
                          GravedadNoConformidad.mayor, self.ana),
                         (nc.descripcion, nc.gravedad, nc.responsable_id))
        row = self.rows("hallazgos_auditoria")[-1]
        self.assertEqual("update", row.action)
        self.assertLessEqual({"tipo": "Observación", "no_conformidad_id": None}.items(),
                             row.before.items())
        self.assertLessEqual({"tipo": "No conformidad mayor", "no_conformidad_id": nc.id}.items(),
                             row.after.items())

    def test_a_downgraded_nonconformity_keeps_its_nonconformity_and_link(self) -> None:
        created = self.add(self.audit.id, tipo="nc_mayor")
        nc_id = created.no_conformidad_id
        nc_rows = len(self.rows("no_conformidades"))
        changed = self.edit(created.id, {"tipo": "oportunidad_mejora"})
        self.assertEqual((tipo("oportunidad_mejora"), nc_id),
                         (changed.tipo, changed.no_conformidad_id))
        self.assertEqual(GravedadNoConformidad.mayor, self.nc(nc_id).gravedad)
        self.assertEqual(nc_rows, len(self.rows("no_conformidades")))
        row = self.rows("hallazgos_auditoria")[-1]
        self.assertEqual(("No conformidad mayor", "Oportunidad de mejora"),
                         (row.before["tipo"], row.after["tipo"]))
        self.assertNotIn("no_conformidad_id", row.after)

    def test_the_severity_follows_only_while_the_nonconformity_is_open(self) -> None:
        created = self.add(self.audit.id, tipo="nc_mayor")
        nc_id = created.no_conformidad_id
        self.edit(created.id, {"tipo": "nc_menor"})
        self.assertEqual(GravedadNoConformidad.menor, self.nc(nc_id).gravedad)
        nc_row = self.rows("no_conformidades")[-1]
        self.assertEqual(("update", "Menor"), (nc_row.action, nc_row.after["gravedad"]))
        table = NoConformidad.__table__
        db.session.execute(table.update().where(table.c.id == nc_id)
                           .values(estado=EstadoNoConformidad.accion_planificada))
        db.session.commit()
        message = self.refused(errors().ValidationError, findings().update, CLI, created.id,
                               {"tipo": "nc_mayor"})
        self.assertIn("abierta", message)
        db.session.expire_all()
        self.assertEqual(tipo("nc_menor"), db.session.get(finding_model(), created.id).tipo)
        self.assertEqual(GravedadNoConformidad.menor, self.nc(nc_id).gravedad)

    def test_upgrading_again_reuses_the_kept_nonconformity(self) -> None:
        created = self.add(self.audit.id, tipo="nc_mayor")
        nc_id = created.no_conformidad_id
        self.edit(created.id, {"tipo": "observacion"})
        changed = self.edit(created.id, {"tipo": "nc_menor"})
        self.assertEqual(nc_id, changed.no_conformidad_id)
        self.assertEqual(GravedadNoConformidad.menor, self.nc(nc_id).gravedad)
        self.assertEqual(1, self.count(NoConformidad))

    def test_other_edits_leave_the_nonconformity_and_a_no_op_writes_nothing(self) -> None:
        created = self.add(self.audit.id, tipo="nc_menor", clausula="7.5")
        nc_before = self.nc(created.no_conformidad_id).descripcion
        rows = len(self.rows("hallazgos_auditoria")) + len(self.rows("no_conformidades"))
        self.edit(created.id, {"descripcion": " Registro de compras sin firmar ",
                               "clausula": "7.5", "tipo": "nc_menor",
                               "auditoria_id": self.audit.id})
        self.assertEqual(rows, len(self.rows("hallazgos_auditoria"))
                         + len(self.rows("no_conformidades")))
        changed = self.edit(created.id, {"descripcion": "Nuevo texto", "evidencia": "Foto 3",
                                         "clausula": ""})
        self.assertEqual((None, "Foto 3"), (changed.clausula, changed.evidencia))
        self.assertEqual(nc_before, self.nc(created.no_conformidad_id).descripcion)
        other = self.new_audit()
        message = self.refused(errors().ValidationError, findings().update, CLI, created.id,
                               {"auditoria_id": other.id})
        self.assertIn("auditoría", message)
        for data in ({"no_conformidad_id": None}, {"tipo": None}, {"descripcion": ""}, ["x"]):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, findings().update, CLI, created.id, data)
        self.refused(errors().NotFound, findings().update, CLI, 999, {"descripcion": "x"})


class DeleteTestCase(Helpers, ServiceBase):
    def test_an_administrator_deletes_a_finding_but_never_its_nonconformity(self) -> None:
        found = self.new_audit()
        created = self.add(found.id, tipo="nc_mayor")
        nc_id = created.no_conformidad_id
        for who in (actor(AUDITOR), mcp_actor(ADMIN)):
            with self.subTest(role=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, findings().delete, who, created.id)
        findings().delete(db.session, actor(ADMIN), created.id)
        db.session.commit()
        self.assertEqual(0, self.count(finding_model()))
        self.assertIsNotNone(self.nc(nc_id))
        row = self.rows("hallazgos_auditoria")[-1]
        self.assertEqual(("delete", None, nc_id),
                         (row.action, row.after, row.before["no_conformidad_id"]))
        self.refused(errors().NotFound, findings().delete, actor(ADMIN), created.id)

    def test_an_audit_with_findings_cannot_be_deleted(self) -> None:
        found = self.new_audit()
        created = self.add(found.id)
        message = self.refused(errors().Conflict, audits().delete, actor(ADMIN), found.id)
        self.assertIn("elimina antes sus hallazgos", message)
        self.assertIsNotNone(db.session.get(Auditoria, found.id))
        findings().delete(db.session, actor(ADMIN), created.id)
        audits().delete(db.session, actor(ADMIN), found.id)
        db.session.commit()
        self.assertEqual(0, self.count(Auditoria))

    def test_deleting_a_nonconformity_unlinks_its_finding_with_an_audit_row(self) -> None:
        found = self.new_audit()
        created = self.add(found.id, tipo="nc_menor")
        nc_id = created.no_conformidad_id
        nonconformities().delete(db.session, actor(ADMIN), nc_id)
        db.session.commit()
        db.session.expire_all()
        self.assertIsNone(db.session.get(finding_model(), created.id).no_conformidad_id)
        row = self.rows("hallazgos_auditoria")[-1]
        self.assertEqual(("update", nc_id, None), (row.action, row.before["no_conformidad_id"],
                                                   row.after["no_conformidad_id"]))


class PolicyTestCase(Helpers, ServiceBase):
    def test_administrators_and_auditors_work_and_operativo_is_refused(self) -> None:
        found = self.new_audit()
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                created = self.add(found.id, actor(role), tipo="nc_menor")
                self.edit(created.id, {"evidencia": role.name}, actor(role))
                self.assertIs(created, findings().get(db.session, actor(role), created.id))
                self.assertIn(created, findings().list_(db.session, actor(role)))
        existing = findings().list_(db.session, CLI)[0].id
        calls = {
            "list": lambda who: findings().list_(db.session, who),
            "page": lambda who: findings().list_page(db.session, who),
            "get": lambda who: findings().get(db.session, who, existing),
            "create": lambda who: findings().create(
                db.session, who, {"auditoria_id": found.id} | FINDING),
            "update": lambda who: findings().update(db.session, who, existing,
                                                    {"evidencia": "x"}),
            "delete": lambda who: findings().delete(db.session, who, existing),
        }
        for name, call in calls.items():
            with self.subTest(operation=name):
                with self.assertRaises(errors().PermissionDenied):
                    call(actor(OPERATIVO))
                db.session.rollback()
        for name in ("create", "update"):
            with self.subTest(read_only_token=name):
                with self.assertRaises(errors().PermissionDenied):
                    calls[name](actor(AUDITOR, scopes={"read"}))
                db.session.rollback()
        self.assertEqual(2, self.count(finding_model()))

    def test_lists_filter_by_audit_and_type(self) -> None:
        first, second = self.new_audit(), self.new_audit(area_auditada="Ventas")
        a = self.add(first.id, tipo="nc_menor")
        b = self.add(first.id)
        c = self.add(second.id)
        self.assertEqual([c, a, b], findings().list_(db.session, CLI))
        self.assertEqual([a, b], findings().list_(db.session, CLI, auditoria_id=first.id))
        self.assertEqual([b], findings().list_(db.session, CLI, auditoria_id=first.id,
                                               tipo=tipo("observacion")))
        self.assertEqual([a], findings().list_(db.session, CLI, tipo="nc_menor"))
        self.assertEqual([], findings().list_(db.session, CLI, auditoria_id=2 ** 40))
        page, total = findings().list_page(db.session, CLI, auditoria_id=first.id,
                                           page=2, per_page=1)
        self.assertEqual(([b], 2), (page, total))
        self.refused(errors().ValidationError, findings().list_, CLI, tipo="Observación")
        self.refused(errors().NotFound, findings().get, CLI, 999)


class ForeignKeyCases:
    """The schema backs the service; shared by the SQLite and PostgreSQL cases."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_audit_foreign_key_refuses_a_delete_the_check_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        found = self.new_audit()
        self.add(found.id)
        with patch.object(audits(), "_has_findings", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                audits().delete(db.session, CLI, found.id)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        self.assertIn("elimina antes sus hallazgos", caught.exception.message)
        db.session.rollback()
        self.assertEqual(1, self.count(Auditoria))

    def test_the_link_is_cleared_by_the_database_and_never_shared(self) -> None:
        self.enforce_foreign_keys()
        found = self.new_audit()
        first = self.add(found.id, tipo="nc_mayor")
        second = self.add(found.id)
        table = finding_model().__table__
        with self.assertRaises(IntegrityError):
            db.session.execute(table.update().where(table.c.id == second.id)
                               .values(no_conformidad_id=first.no_conformidad_id))
            db.session.commit()
        db.session.rollback()
        db.session.execute(NoConformidad.__table__.delete())  # bypasses the service
        db.session.commit()
        db.session.expire_all()
        self.assertIsNone(db.session.get(finding_model(), first.id).no_conformidad_id)


class SqliteForeignKeyTestCase(ForeignKeyCases, Helpers, ServiceBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresForeignKeyTestCase(ForeignKeyCases, Helpers, unittest.TestCase):
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
    async def test_findings_round_trip_and_create_their_nonconformity(self) -> None:
        ana = self.seed("personas")
        audit_id = self.seed("auditorias", responsable_area_id=ana)
        who = mcp_actor(AUDITOR)
        created = await self.call(who, "qms_create", {"module": "hallazgos_auditoria", "data": {
            "auditoria_id": audit_id, "tipo": "nc_mayor", "descripcion": "Pedido sin aprobar",
            "clausula": "8.4.1", "evidencia": "Pedido 42"}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("No conformidad mayor", "8.4.1", audit_id),
                         (record["tipo"], record["clausula"], record["auditoria_id"]))
        nc = await self.call(who, "qms_get", {"module": "no_conformidades",
                                              "id": record["no_conformidad_id"]})
        self.assertEqual(("Auditoría", "Mayor", ana, "2026-10-01"),
                         tuple(nc.structured_content[k] for k in (
                             "origen", "gravedad", "responsable_id", "fecha_detectada")))
        for filters, total in (({"tipo": "nc_mayor"}, 1), ({"tipo": "observacion"}, 0),
                               ({"auditoria_id": audit_id}, 1)):
            listed = await self.call(who, "qms_list", {"module": "hallazgos_auditoria",
                                                       "filters": filters})
            self.assertFalse(listed.is_error, listed.content)
            self.assertEqual(total, listed.structured_content["total"])
        updated = await self.call(who, "qms_update", {"module": "hallazgos_auditoria",
                                                      "id": record["id"],
                                                      "data": {"tipo": "nc_menor"}})
        self.assertFalse(updated.is_error, updated.content)
        self.assertEqual(record["no_conformidad_id"],
                         updated.structured_content["no_conformidad_id"])
        nc = await self.call(who, "qms_get", {"module": "no_conformidades",
                                              "id": record["no_conformidad_id"]})
        self.assertEqual("Menor", nc.structured_content["gravedad"])
        got = (await self.call(who, "qms_get", {"module": "hallazgos_auditoria",
                                                "id": record["id"]})).structured_content
        self.assertEqual(("No conformidad menor", record["no_conformidad_id"]),
                         (got["tipo"], got["no_conformidad_id"]))
        refused = await self.call(who, "qms_update", {"module": "hallazgos_auditoria",
                                                      "id": record["id"],
                                                      "data": {"no_conformidad_id": None}})
        self.assertTrue(refused.is_error)
        self.assertIn("Campos no permitidos", refused.content[0].text)
        hidden = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                 {"module": "hallazgos_auditoria"})
        self.assertTrue(hidden.is_error)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}["hallazgos_auditoria"]
        fields = {f["name"]: f for f in described["fields"]}
        self.assertEqual({"auditoria_id", "tipo", "descripcion", "clausula", "evidencia"},
                         set(fields))
        self.assertEqual(["nc_mayor", "nc_menor", "observacion", "oportunidad_mejora"],
                         fields["tipo"]["allowed"])
        self.assertEqual({"auditoria_id", "tipo"}, {f["name"] for f in described["filters"]})


if __name__ == "__main__":
    unittest.main()
