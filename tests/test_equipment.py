"""Equipment/calibration public service contract (7.1.5), in isolated databases."""
import os
import unittest
from uuid import uuid4
from datetime import date, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateSchema
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import EquipoMedicion, ComprobacionEquipo, Person, RoleEnum
from app.services import equipment, people
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError
from test_nonconformity_service import ServiceBase

TODAY = date(2026, 11, 2)
ADMIN = Actor(None, "test", RoleEnum.ADMINISTRADOR, "cli")
OPERATOR = Actor(None, "test", RoleEnum.OPERATIVO, "cli")


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
