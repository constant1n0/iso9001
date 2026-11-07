"""Equipment/calibration public service contract (7.1.5), in isolated databases."""
import os
import unittest
from uuid import uuid4
from datetime import date, timedelta
from unittest.mock import patch

from app.mcp_server.registry import MODULES

from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateSchema
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import EquipoMedicion, ComprobacionEquipo, Person, RoleEnum
from app.services import equipment, people
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError
from test_nonconformity_service import ServiceBase
from mcp_support import McpDbCase, SEEDS, mcp_actor

TODAY = date(2026, 11, 2)
ADMIN = Actor(None, "test", RoleEnum.ADMINISTRADOR, "cli")
OPERATOR = Actor(None, "test", RoleEnum.OPERATIVO, "cli")
REPLAY_MESSAGE = ('La comprobación apta repite una ya registrada; para recuperar el equipo '
                  'registra una nueva comprobación con otra referencia o certificado.')


class EquipmentTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.item = equipment.equipment.create(
            db.session, ADMIN, {"codigo": "EQ-1", "nombre": "Balanza"})
        db.session.commit()

    def check(self, **changes):
        data = dict(equipo_id=self.item.id, fecha=TODAY, tipo="calibracion",
                    resultado="apto", realizado_por="Laboratorio",
                    referencia="Patrón trazable C-1", certificado="CERT-1",
                    fecha_proxima=TODAY + timedelta(days=365))
        result = equipment.checks.create(db.session, ADMIN, data | changes, today=TODAY)
        db.session.commit()
        return result

    def test_mcp_extras_use_one_latest_check_for_status_and_due_date(self):
        success = self.check()
        failure = self.check(resultado="no_apto", fecha_proxima=None,
                             evaluacion_impacto="Reviewed lots", acciones="Recheck lots")
        with patch.object(equipment, "latest", side_effect=[success, failure]) as latest, \
                patch("app.mcp_server.registry.local_today", return_value=TODAY):
            extras = MODULES["equipos_medicion"].extras(self.item)
        self.assertEqual({"estado": "apto", "fecha_proxima": success.fecha_proxima.isoformat()},
                         extras)
        latest.assert_called_once()

    def test_mcp_extras_preserve_suitability_states_and_due_dates(self):
        extras = MODULES["equipos_medicion"].extras
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            self.assertEqual({"estado": "sin_comprobar", "fecha_proxima": None}, extras(self.item))
            self.check(fecha=TODAY - timedelta(days=2), fecha_proxima=TODAY - timedelta(days=1))
            self.assertEqual({"estado": "vencido", "fecha_proxima": (TODAY - timedelta(days=1)).isoformat()},
                             extras(self.item))
            self.check(resultado="no_apto", fecha_proxima=None,
                       evaluacion_impacto="Reviewed lots", acciones="Recheck lots")
            self.assertEqual({"estado": "no_apto", "fecha_proxima": None}, extras(self.item))
            equipment.equipment.update(db.session, ADMIN, self.item.id, {"retirado": True})
            db.session.commit()
            self.assertEqual({"estado": "retirado", "fecha_proxima": None}, extras(self.item))

    def test_new_equipment_has_no_valid_check_and_unchanged_edit_is_not_audited(self):
        self.assertEqual("sin_comprobar", equipment.status(db.session, self.item, today=TODAY))
        count = len(self.audit_rows())
        equipment.equipment.update(db.session, ADMIN, self.item.id, {"nombre": "Balanza"})
        db.session.commit()
        self.assertEqual(count, len(self.audit_rows()))
        with self.assertRaises(Conflict):
            equipment.equipment.create(db.session, ADMIN, {"codigo": "EQ-1", "nombre": "Otra"})
        db.session.rollback()
        self.assertEqual(([self.item], 1), equipment.equipment.list_page(db.session, ADMIN))

    def test_success_is_valid_through_due_date_then_expires(self):
        self.check()
        due = TODAY + timedelta(days=365)
        self.assertEqual("apto", equipment.status(db.session, self.item, today=due))
        self.assertEqual("vencido", equipment.status(db.session, self.item, today=due + timedelta(days=1)))

    def test_failure_takes_equipment_out_of_service_and_requires_impact_assessment(self):
        self.check()
        before = len(self.audit_rows())
        for changes in ({"evaluacion_impacto": None}, {"acciones": "  "}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.check(**(dict(resultado="no_apto", fecha_proxima=None,
                                   evaluacion_impacto="Lotes anteriores revisados",
                                   acciones="Revisar lotes") | changes))
            db.session.rollback()
            self.assertEqual("apto", equipment.status(db.session, self.item, today=TODAY))
            self.assertEqual(before, len(self.audit_rows()))
            self.assertEqual(1, equipment.checks.list_page(db.session, ADMIN)[1])
        self.check(resultado="no_apto", fecha_proxima=None,
                   evaluacion_impacto="Lotes anteriores revisados", acciones="Revisar lotes")
        self.assertEqual("no_apto", equipment.status(db.session, self.item, today=TODAY))
        self.check(tipo="verificacion")
        self.assertEqual("apto", equipment.status(db.session, self.item, today=TODAY))
        self.assertEqual(3, equipment.checks.list_page(db.session, ADMIN)[1])

    def test_replayed_success_after_same_day_failure_is_rejected_without_writes(self):
        success = self.check(evaluacion_impacto="Reviewed", acciones="Verified")
        failure = self.check(resultado="no_apto", fecha_proxima=None,
                             evaluacion_impacto="Affected lots", acciones="Recheck lots")
        before = [self.audit.snapshot(row) for row in self.audit_rows()]
        for changes in ({}, {"realizado_por": "  Laboratorio ",
                             "referencia": " Patrón trazable C-1  ",
                             "certificado": " CERT-1 ",
                             "evaluacion_impacto": " Reviewed ", "acciones": " Verified "}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError) as caught:
                    self.check(**(dict(evaluacion_impacto="Reviewed", acciones="Verified") | changes))
                self.assertEqual(REPLAY_MESSAGE, caught.exception.message)
                self.assertEqual("no_apto", equipment.status(db.session, self.item, today=TODAY))
                history, total = equipment.checks.list_page(db.session, ADMIN, equipo_id=self.item.id)
                self.assertEqual((2, [failure.id, success.id]), (total, [row.id for row in history]))
                self.assertEqual(before, [self.audit.snapshot(row) for row in self.audit_rows()])
                db.session.rollback()

    def test_replay_matches_any_prior_success_not_just_the_most_recent_success(self):
        self.check(certificado=None)
        self.check(certificado="CERT-2")
        self.check(resultado="no_apto", fecha_proxima=None,
                   evaluacion_impacto="Affected lots", acciones="Recheck lots")
        before = len(self.audit_rows())
        with self.assertRaises(ValidationError) as caught:
            self.check(certificado="  ", evaluacion_impacto=None, acciones="")
        self.assertEqual(REPLAY_MESSAGE, caught.exception.message)
        self.assertEqual("no_apto", equipment.status(db.session, self.item, today=TODAY))
        self.assertEqual(3, equipment.checks.list_page(db.session, ADMIN)[1])
        self.assertEqual(before, len(self.audit_rows()))

    def test_different_certificate_or_reference_recovers_and_keeps_failed_evidence(self):
        self.check()
        for changes in ({"certificado": "CERT-2"}, {"referencia": "Patrón trazable C-2"}):
            with self.subTest(changes=changes):
                failure = self.check(resultado="no_apto", fecha_proxima=None,
                                     evaluacion_impacto="Affected lots", acciones="Recheck lots")
                before = len(self.audit_rows())
                recovered = self.check(**changes)
                self.assertEqual("apto", equipment.status(db.session, self.item, today=TODAY))
                history, total = equipment.checks.list_page(db.session, ADMIN, equipo_id=self.item.id)
                self.assertEqual(total, len(history))
                self.assertEqual(recovered.id, history[0].id)
                self.assertIn(failure.id, [row.id for row in history if row.resultado.name == "no_apto"])
                self.assertEqual(before + 1, len(self.audit_rows()))

    def test_identical_success_without_intervening_failure_remains_accepted(self):
        success = self.check()
        before = len(self.audit_rows())
        repeated = self.check()
        self.assertGreater(repeated.id, success.id)
        self.assertEqual("apto", equipment.status(db.session, self.item, today=TODAY))
        self.assertEqual(2, equipment.checks.list_page(db.session, ADMIN)[1])
        self.assertEqual(before + 1, len(self.audit_rows()))

    def test_checks_cannot_be_changed_deleted_or_backdated_to_hide_a_failure(self):
        check = self.check()
        before = len(self.audit_rows())
        with self.assertRaises(ValidationError):
            equipment.checks.update(db.session, ADMIN, check.id, {"resultado": "no_apto"}, today=TODAY)
        with self.assertRaises(ValidationError):
            equipment.checks.delete(db.session, ADMIN, check.id)
        with self.assertRaises(ValidationError):
            self.check(fecha=TODAY - timedelta(days=1), resultado="no_apto",
                       fecha_proxima=None, evaluacion_impacto="Lotes", acciones="Revisar")
        db.session.rollback()
        self.assertEqual(before, len(self.audit_rows()))
        self.assertEqual("apto", equipment.status(db.session, self.item, today=TODAY))
        self.assertEqual(1, equipment.checks.list_page(db.session, ADMIN)[1])

    def test_withdrawal_is_not_cleared_by_success_and_referenced_equipment_cannot_be_deleted(self):
        self.check()
        equipment.equipment.update(db.session, ADMIN, self.item.id, {"retirado": True})
        db.session.commit()
        self.check()
        self.assertEqual("retirado", equipment.status(db.session, self.item, today=TODAY))
        with self.assertRaises(Conflict):
            equipment.equipment.delete(db.session, ADMIN, self.item.id)
        db.session.rollback()
        self.assertEqual(2, equipment.checks.list_page(db.session, ADMIN)[1])

    def test_responsible_person_protection_and_unchanged_existing_people_behavior(self):
        person = people.create(db.session, ADMIN, {'nombre': 'Ana'})
        db.session.commit()
        equipment.equipment.update(db.session, ADMIN, self.item.id, {'responsable_id': person.id})
        db.session.commit()
        before = len(self.audit_rows())
        with self.assertRaises(Conflict):
            people.delete(db.session, ADMIN, person.id)
        db.session.rollback()
        self.assertEqual(person.id, equipment.equipment.get(db.session, ADMIN, self.item.id).responsable_id)
        self.assertEqual(before, len(self.audit_rows()))
        unreferenced = people.create(db.session, ADMIN, {'nombre': 'Sin referencias'})
        db.session.commit()
        people.delete(db.session, ADMIN, unreferenced.id)
        db.session.commit()
        self.assertEqual(1, people.list_page(db.session, ADMIN)[1])

    def test_public_permissions_filters_paging_and_rejected_dates_are_atomic(self):
        self.check()
        for changes in ({"fecha": TODAY + timedelta(days=1)},
                        {"fecha_proxima": TODAY}, {"equipo_id": 999},
                        {"referencia": " "}, {"resultado": "Apto"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.check(**changes)
            db.session.rollback()
            self.assertEqual(1, equipment.checks.list_page(db.session, ADMIN)[1])
            self.assertEqual(2, len(self.audit_rows()))
        with self.assertRaises(PermissionDenied):
            equipment.equipment.create(db.session, OPERATOR, {"codigo": "X", "nombre": "X"})
        with self.assertRaises(PermissionDenied):
            equipment.checks.create(db.session, OPERATOR, {}, today=TODAY)
        self.assertEqual(1, equipment.equipment.list_page(db.session, OPERATOR)[1])
        self.assertEqual(1, equipment.checks.list_page(db.session, ADMIN, equipo_id=self.item.id)[1])
        self.assertEqual(([], 1), equipment.checks.list_page(db.session, ADMIN, page=2, per_page=1))
        self.assertEqual(([], 0), equipment.checks.list_page(db.session, ADMIN, equipo_id=999))


class EquipmentReplayMcpTestCase(McpDbCase):
    async def test_mcp_rejects_replayed_success_but_accepts_new_certificate(self):
        item_id = self.seed("equipos_medicion")
        data = SEEDS["comprobaciones_equipo"] | {"equipo_id": item_id}
        actor = mcp_actor()

        async def create(values):
            return await self.call(actor, "qms_create", {
                "module": "comprobaciones_equipo", "data": values})

        async def history():
            return await self.call(actor, "qms_list", {
                "module": "comprobaciones_equipo", "filters": {"equipo_id": item_id}})

        async def item():
            return await self.call(actor, "qms_get", {"module": "equipos_medicion", "id": item_id})

        for values in (data, data, data | dict(resultado="no_apto", fecha_proxima=None,
                                               evaluacion_impacto="Affected lots", acciones="Recheck lots")):
            created = await create(values)
            self.assertFalse(created.is_error, created.content)
        before = (await history()).structured_content
        with self.app.app_context():
            from app.models import AuditLog
            audit_before = [row.id for row in db.session.query(AuditLog).order_by(AuditLog.id)]
        rejected = await create(data)
        self.assertTrue(rejected.is_error, rejected.content)
        self.assertEqual(f"Error executing tool qms_create: {REPLAY_MESSAGE}", rejected.content[0].text)
        self.assertEqual(before, (await history()).structured_content)
        self.assertEqual("no_apto", (await item()).structured_content["estado"])
        with self.app.app_context():
            self.assertEqual(audit_before, [row.id for row in db.session.query(AuditLog).order_by(AuditLog.id)])
        recovered = await create(data | {"certificado": "CERT-NEW"})
        self.assertFalse(recovered.is_error, recovered.content)
        self.assertEqual("apto", (await item()).structured_content["estado"])
        after = (await history()).structured_content
        self.assertEqual(4, after["total"])
        self.assertEqual(1, sum(row["resultado"] == "No apto" for row in after["items"]))
