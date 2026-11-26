"""S5: process-specific conditions and audited, immutable check evidence."""
from datetime import date, timedelta
from importlib import import_module
from unittest.mock import patch

from app.extensions import db
from app.models import RoleEnum
from app.services import people, process_operations, policy
from app.services.actor import Actor
from app.services.audit import snapshot
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationError
from test_nonconformity_service import ServiceBase

DAY = date(2020, 1, 2)
NEXT = DAY + timedelta(days=30)
ADMIN = Actor(7, 'maintainer', RoleEnum.ADMINISTRADOR, 'cli')
MASTER = dict(proceso_id=1, factor='fisico', descripcion='Temperatura del taller',
              criterio='Entre 18 y 25 °C', frecuencia='Cada mes', fecha_proxima=DAY)
ENTRY = dict(condicion_id=1, fecha=DAY, resultado='conforme', realizado_por='Ana',
             evidencia='Lectura 21 °C', fecha_proxima=NEXT)


class ProcessEnvironmentTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.service = import_module('app.services.process_environment')
        self.process = process_operations.create(db.session, ADMIN,
            {'proceso': 'Fabricación', 'recursos': 'Original'})
        self.item = self.service.items.create(db.session, ADMIN, MASTER)
        db.session.commit()

    def state(self):
        return ([snapshot(row) for row in self.service.items.list_(db.session, ADMIN)],
                [snapshot(row) for row in self.service.checks.list_(db.session, ADMIN)],
                [snapshot(row) for row in self.audit_rows()])

    def record(self, **changes):
        row = self.service.checks.create(db.session, ADMIN, ENTRY | changes, today=DAY)
        db.session.commit()
        return row

    def test_fields_factors_attribution_noop_and_process_resources_preserved(self):
        self.assertEqual((1, 'fisico', 'Temperatura del taller', 'Entre 18 y 25 °C',
                          'Cada mes', DAY, False, 7, 7),
            (self.item.proceso_id, self.item.factor.name, self.item.descripcion,
             self.item.criterio, self.item.frecuencia, self.item.fecha_proxima,
             self.item.retirado, self.item.created_by_id, self.item.updated_by_id))
        for factor in ('social', 'psicologico'):
            row = self.service.items.create(db.session, ADMIN, MASTER | {'factor': factor})
            self.assertEqual(factor, row.factor.name)
        db.session.commit()
        before = self.state()
        self.service.items.update(db.session, ADMIN, self.item.id, MASTER)
        db.session.commit()
        self.assertEqual(before, self.state())
        self.assertEqual('Original', process_operations.get(db.session, ADMIN, 1).recursos)

    def test_invalid_master_fields_leave_rows_and_audit_unchanged(self):
        for data, message in (
            ({'proceso_id': None}, 'El campo «proceso_id» debe ser un número entero.'),
            ({'proceso_id': 999}, 'El campo «proceso_id» no corresponde a ningún proceso.'),
            ({'factor': 'otro'}, 'El valor del campo «factor» no es válido.'),
            ({'descripcion': ' '}, 'El campo «descripcion» es obligatorio.'),
            ({'criterio': ' '}, 'El campo «criterio» es obligatorio.'),
            ({'frecuencia': ' '}, 'El campo «frecuencia» es obligatorio.'),
            ({'fecha_proxima': None}, 'El campo «fecha_proxima» es obligatorio y debe ser una fecha.'),
            ({'responsable_id': 999}, 'El campo «responsable_id» no corresponde a ninguna persona.'),
            ({'retirado': None}, 'El campo «retirado» debe ser verdadero o falso.'),
            ({'id': 9}, 'Campos no permitidos: id.'),
        ):
            with self.subTest(data=data):
                before = self.state()
                with self.assertRaises(ValidationError) as caught:
                    self.service.items.update(db.session, ADMIN, self.item.id, data)
                self.assertEqual(message, caught.exception.message)
                self.assertEqual(before, self.state())
                db.session.rollback()

    def test_active_responsible_retained_and_person_deletion_guard(self):
        person = people.create(db.session, ADMIN, {'nombre': 'Ana'})
        self.service.items.update(db.session, ADMIN, 1, {'responsable_id': person.id})
        db.session.commit()
        people.update(db.session, ADMIN, person.id, {'activo': False})
        db.session.commit()
        self.service.items.update(db.session, ADMIN, 1,
            {'responsable_id': person.id, 'criterio': 'Actualizado'})
        db.session.commit()
        before = self.state()
        with self.assertRaises(Conflict) as caught:
            people.delete(db.session, ADMIN, person.id)
        self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
        self.assertEqual(before, self.state())
        with self.assertRaises(ValidationError) as caught:
            self.service.items.create(db.session, ADMIN, MASTER | {'responsable_id': person.id})
        self.assertEqual('La persona del campo «responsable_id» está desactivada; elige una persona activa.',
                         caught.exception.message)
        self.assertEqual(before, self.state())
        unused = people.create(db.session, ADMIN, {'nombre': 'Unused'})
        db.session.commit()
        people.delete(db.session, ADMIN, unused.id)
        db.session.commit()
        self.assertEqual([person.id], [p.id for p in people.list_(db.session, ADMIN)])

    def test_process_citation_prevents_deletion_but_uncited_process_still_deletes(self):
        before = self.state()
        with self.assertRaises(Conflict) as caught:
            process_operations.delete(db.session, ADMIN, 1)
        self.assertEqual('No se puede eliminar un proceso con condiciones de ambiente.', caught.exception.message)
        self.assertEqual(before, self.state())
        unused = process_operations.create(db.session, ADMIN, {'proceso': 'Unused'})
        db.session.commit()
        process_operations.delete(db.session, ADMIN, unused.id_proceso)
        db.session.commit()
        self.assertEqual([1], [p.id_proceso for p in process_operations.list_(db.session, ADMIN)])

    def test_check_audits_parent_and_history_and_rollback_is_atomic(self):
        before = self.state()
        self.service.checks.create(db.session, ADMIN, ENTRY, today=DAY)
        self.assertEqual(NEXT, self.item.fecha_proxima)
        db.session.rollback()
        self.assertEqual(before, self.state())
        row = self.record()
        self.assertEqual((DAY, NEXT, 'conforme', 'Lectura 21 °C', 7, 7),
            (row.fecha, row.fecha_proxima, row.resultado.name, row.evidencia,
             row.created_by_id, row.updated_by_id))
        self.assertEqual(NEXT, self.service.items.get(db.session, ADMIN, 1).fecha_proxima)
        rows = self.audit_rows()
        self.assertEqual(['create', 'create', 'update', 'create'], [r.action for r in rows])
        self.assertEqual({'fecha_proxima', 'updated_at'}, set(rows[-2].after))
        self.assertEqual(NEXT.isoformat(), rows[-2].after['fecha_proxima'])

    def test_failed_history_append_rolls_back_next_date_and_audit(self):
        before = self.state()
        with patch('app.services.process_environment.crud.create', side_effect=RuntimeError('append failed')):
            with self.assertRaisesRegex(RuntimeError, 'append failed'):
                self.service.checks.create(db.session, ADMIN, ENTRY, today=DAY)
        db.session.rollback()
        self.assertEqual(before, self.state())

    def test_rejected_checks_leave_parent_history_and_audit_unchanged(self):
        self.record()
        for changes, message in (
            ({'fecha': DAY + timedelta(days=1)}, 'La fecha de comprobación no puede ser futura.'),
            ({'fecha': DAY - timedelta(days=1)}, 'La comprobación no puede ser anterior a la última registrada.'),
            ({'fecha_proxima': DAY}, 'Indica una nueva próxima fecha posterior a la comprobación.'),
            ({'fecha_proxima': DAY - timedelta(days=1)}, 'Indica una nueva próxima fecha posterior a la comprobación.'),
            ({'fecha_proxima': None}, 'El campo «fecha_proxima» es obligatorio y debe ser una fecha.'),
            ({'evidencia': ' '}, 'El campo «evidencia» es obligatorio.'),
            ({'realizado_por': ' '}, 'El campo «realizado_por» es obligatorio.'),
            ({'resultado': 'apto'}, 'El valor del campo «resultado» no es válido.'),
            ({'resultado': 'no_conforme', 'acciones': ' '}, 'Un resultado no conforme requiere indicar las acciones realizadas.'),
            ({'condicion_id': 999}, 'Condición de ambiente no encontrada.'),
            ({'no_conformidad_id': 1}, 'Campos no permitidos: no_conformidad_id.'),
        ):
            with self.subTest(changes=changes):
                before = self.state()
                with self.assertRaises(ValidationError) as caught:
                    self.record(**changes)
                self.assertEqual(message, caught.exception.message)
                self.assertEqual(before, self.state())
                db.session.rollback()
        before = self.state()
        with self.assertRaises(ValidationError) as caught:
            self.service.checks.create(db.session, ADMIN,
                {k: v for k, v in ENTRY.items() if k != 'fecha_proxima'}, today=DAY)
        self.assertEqual('Faltan campos obligatorios: fecha_proxima.', caught.exception.message)
        self.assertEqual(before, self.state())

    def test_same_day_immutable_history_retirement_and_deletion(self):
        first = self.record()
        second = self.record(resultado='no_conforme', acciones='Regular climatización')
        self.assertEqual(second, self.service.latest(db.session, 1))
        before = self.state()
        for operation in (lambda: self.service.checks.update(db.session, ADMIN, first.id, {}, today=DAY),
                          lambda: self.service.checks.delete(db.session, ADMIN, first.id)):
            with self.assertRaises(ValidationError) as caught:
                operation()
            self.assertEqual('Las comprobaciones no se modifican ni se eliminan; registra una nueva.', caught.exception.message)
            self.assertEqual(before, self.state())
        with self.assertRaises(Conflict) as caught:
            self.service.items.delete(db.session, ADMIN, 1)
        self.assertEqual('No se puede eliminar una condición con comprobaciones; retírala en su lugar.', caught.exception.message)
        self.assertEqual(before, self.state())
        self.service.items.update(db.session, ADMIN, 1, {'retirado': True})
        db.session.commit()
        before = self.state()
        with self.assertRaises(ValidationError) as caught:
            self.record()
        self.assertEqual('No se pueden registrar comprobaciones de una condición retirada.', caught.exception.message)
        self.assertEqual(before, self.state())
        empty = self.service.items.create(db.session, ADMIN, MASTER)
        db.session.commit()
        self.service.items.delete(db.session, ADMIN, empty.id)
        db.session.commit()
        self.assertEqual(1, self.service.items.list_page(db.session, ADMIN)[1])

    def test_filters_paging_overdue_and_permissions_preserve_equipment_grant(self):
        self.assertFalse(self.service.is_overdue(self.item, today=DAY))
        self.assertTrue(self.service.is_overdue(self.item, today=NEXT))
        for filters, count in (({'factor': 'social'}, 0), ({'factor': 'unknown', 'retirado': 'x', 'vencida': 'x'}, 1),
                               ({'proceso_id': 1}, 1), ({'proceso_id': 999}, 0),
                               ({'retirado': True}, 0), ({'vencida': True, 'today': DAY}, 0),
                               ({'vencida': True, 'today': NEXT}, 1)):
            self.assertEqual(count, self.service.items.list_page(db.session, ADMIN, **filters)[1])
        self.assertEqual(([], 1), self.service.items.list_page(db.session, ADMIN, page=2, per_page=1))
        for role in RoleEnum:
            who = Actor(None, 'role', role, 'cli')
            self.assertEqual(1, self.service.items.list_page(db.session, who)[1])
            for action in policy.Action:
                self.assertEqual(policy.can(who, action, policy.Resource.MEASURING_EQUIPMENT),
                                 policy.can(who, action, policy.Resource.PROCESS_ENVIRONMENT))
            if role is RoleEnum.OPERATIVO:
                before = self.state()
                for operation in (lambda: self.service.items.create(db.session, who, MASTER),
                                  lambda: self.service.items.update(db.session, who, 1, {}),
                                  lambda: self.service.checks.create(db.session, who, {}, today=DAY)):
                    with self.assertRaises(PermissionDenied):
                        operation()
                self.assertEqual(before, self.state())
        self.service.items.update(db.session, ADMIN, 1, {'retirado': True})
        db.session.commit()
        self.assertFalse(self.service.is_overdue(self.item, today=NEXT))
