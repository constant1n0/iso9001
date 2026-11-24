"""Infrastructure public service contract, with audited, atomic maintenance history."""
from datetime import date, timedelta
from importlib import import_module

from app.extensions import db
from app.models import RoleEnum
from app.services import people, nonconformities, process_operations
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError
from test_nonconformity_service import ServiceBase

DAY = date(2026, 11, 2)
NEXT = DAY + timedelta(days=30)
ADMIN = Actor(7, 'maintainer', RoleEnum.ADMINISTRADOR, 'cli')
MASTER = dict(codigo='INF-1', nombre='Prensa', tipo='equipo_proceso', ubicacion='Taller',
              frecuencia='Cada mes', fecha_proxima=DAY)
ENTRY = dict(fecha=DAY, tipo='preventivo', resultado='conforme', realizado_por='Servicio técnico',
             evidencia='Parte M-1', fecha_proxima=NEXT)
IMMUTABLE = 'Los mantenimientos no se modifican ni se eliminan; registra uno nuevo.'


class InfrastructureTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.service = import_module('app.services.infrastructure')
        self.item = self.service.items.create(db.session, ADMIN, MASTER)
        db.session.commit()

    def record(self, **changes):
        row = self.service.maintenance.create(db.session, ADMIN,
            ENTRY | dict(elemento_id=self.item.id) | changes, today=DAY)
        db.session.commit()
        return row

    def state(self):
        """Observe through public reads; reject paths must not dirty data even before rollback."""
        from app.services.audit import snapshot
        item = self.service.items.get(db.session, ADMIN, self.item.id)
        return (snapshot(item), item.proceso_ids,
                [snapshot(row) for row in self.service.maintenance.list_(db.session, ADMIN)],
                [snapshot(row) for row in self.audit_rows()])

    def test_master_fields_types_uniqueness_attribution_and_noop(self):
        self.assertEqual(('INF-1', 'Prensa', 'equipo_proceso', 'Taller', 'Cada mes', DAY, False),
            (self.item.codigo, self.item.nombre, self.item.tipo.name, self.item.ubicacion,
             self.item.frecuencia, self.item.fecha_proxima, self.item.retirado))
        self.assertEqual((7, 7), (self.item.created_by_id, self.item.updated_by_id))
        for index, kind in enumerate(('edificio', 'hardware_software', 'transporte', 'tic')):
            row = self.service.items.create(db.session, ADMIN, MASTER | dict(codigo=f'I-{index}', tipo=kind))
            self.assertEqual(kind, row.tipo.name)
        db.session.commit()
        before = self.state()
        self.service.items.update(db.session, ADMIN, self.item.id, MASTER | dict(procesos=[]))
        db.session.commit()
        self.assertEqual(before, self.state())
        with self.assertRaises(Conflict) as caught:
            self.service.items.create(db.session, ADMIN, MASTER)
        self.assertEqual('Ya existe un elemento con ese código.', caught.exception.message)
        db.session.rollback()
        self.assertEqual(before, self.state())

    def test_master_invalid_inputs_are_atomic(self):
        for changes, message in (
            ({'codigo': ' '}, 'El campo «codigo» es obligatorio.'),
            ({'nombre': ' '}, 'El campo «nombre» es obligatorio.'),
            ({'tipo': 'otro'}, 'El valor del campo «tipo» no es válido.'),
            ({'frecuencia': ' '}, 'El campo «frecuencia» es obligatorio.'),
            ({'fecha_proxima': None}, 'El campo «fecha_proxima» es obligatorio y debe ser una fecha.'),
            ({'responsable_id': 999}, 'El campo «responsable_id» no corresponde a ninguna persona.'),
            ({'procesos': [999]}, 'El campo «procesos» contiene identificadores desconocidos: 999.'),
            ({'procesos': [True]}, 'El campo «procesos» debe ser una lista de identificadores.'),
            ({'retirado': None}, 'El campo «retirado» debe ser verdadero o falso.'),
            ({'id': 7}, 'Campos no permitidos: id.'),
        ):
            with self.subTest(changes=changes):
                before = self.state()
                with self.assertRaises(ValidationError) as caught:
                    self.service.items.update(db.session, ADMIN, self.item.id, changes)
                self.assertEqual(message, caught.exception.message)
                self.assertEqual(before, self.state())
                db.session.rollback()

    def test_active_responsible_retained_and_cited_person_delete_guard(self):
        person = people.create(db.session, ADMIN, {'nombre': 'Ana'})
        self.service.items.update(db.session, ADMIN, self.item.id, {'responsable_id': person.id})
        db.session.commit()
        people.update(db.session, ADMIN, person.id, {'activo': False})
        db.session.commit()
        self.service.items.update(db.session, ADMIN, self.item.id,
                                  {'responsable_id': person.id, 'nombre': 'Prensa revisada'})
        db.session.commit()
        before = self.state()
        with self.assertRaises(Conflict) as caught:
            people.delete(db.session, ADMIN, person.id)
        self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
        self.assertEqual(before, self.state())
        other = self.service.items.create(db.session, ADMIN, MASTER | dict(codigo='OTHER'))
        db.session.commit()
        with self.assertRaises(ValidationError):
            self.service.items.update(db.session, ADMIN, other.id, {'responsable_id': person.id})
        db.session.rollback()
        self.assertIsNone(self.service.items.get(db.session, ADMIN, other.id).responsable_id)
        unused = people.create(db.session, ADMIN, {'nombre': 'Unused'})
        db.session.commit()
        people.delete(db.session, ADMIN, unused.id)
        db.session.commit()
        self.assertEqual([person.id], [p.id for p in people.list_(db.session, ADMIN)])

    def test_process_sets_sorted_unique_replace_and_audit_without_changing_resources(self):
        ids = [process_operations.create(db.session, ADMIN, {'proceso': name, 'recursos': 'Original'}).id_proceso
               for name in ('Fabricación', 'Entrega')]
        db.session.commit()
        self.service.items.update(db.session, ADMIN, self.item.id, {'procesos': ids[::-1] + ids})
        db.session.commit()
        self.assertEqual(ids, self.item.proceso_ids)
        self.assertEqual(ids, self.audit_rows()[-1].after['procesos'])
        before = self.state()
        self.service.items.update(db.session, ADMIN, self.item.id, {'procesos': ids[::-1]})
        db.session.commit()
        self.assertEqual(before, self.state())
        self.service.items.update(db.session, ADMIN, self.item.id, {'procesos': []})
        db.session.commit()
        self.assertEqual([], self.item.proceso_ids)
        self.assertEqual(ids, self.audit_rows()[-1].before['procesos'])
        self.assertEqual(['Original', 'Original'], [process_operations.get(db.session, ADMIN, i).recursos for i in ids])

    def test_maintenance_audits_both_rows_and_rollback_keeps_plan_and_history(self):
        before = self.state()
        row = self.service.maintenance.create(db.session, ADMIN,
            ENTRY | dict(elemento_id=self.item.id), today=DAY)
        self.assertEqual(NEXT, self.item.fecha_proxima)
        self.assertEqual((7, 7), (row.created_by_id, row.updated_by_id))
        db.session.rollback()
        self.assertEqual(before, self.state())
        row = self.record()
        self.assertEqual(NEXT, self.service.items.get(db.session, ADMIN, self.item.id).fecha_proxima)
        self.assertEqual((DAY, NEXT, 'conforme', 'Parte M-1'),
                         (row.fecha, row.fecha_proxima, row.resultado.name, row.evidencia))
        rows = self.audit_rows()
        self.assertEqual(['create', 'update', 'create'], [r.action for r in rows])
        self.assertEqual({'fecha_proxima', 'updated_at'}, set(rows[1].after))
        self.assertEqual(NEXT.isoformat(), rows[1].after['fecha_proxima'])
        self.assertEqual('Parte M-1', rows[2].after['evidencia'])

    def test_rejected_entries_leave_parent_history_and_audit_unchanged(self):
        self.record()
        for changes, message in (
            ({'fecha': DAY + timedelta(days=1)}, 'La fecha de mantenimiento no puede ser futura.'),
            ({'fecha': DAY - timedelta(days=1)}, 'El mantenimiento no puede ser anterior al último registrado.'),
            ({'fecha_proxima': DAY}, 'Indica una nueva próxima fecha posterior al mantenimiento.'),
            ({'fecha_proxima': None}, 'El campo «fecha_proxima» es obligatorio y debe ser una fecha.'),
            ({'evidencia': ' '}, 'El campo «evidencia» es obligatorio.'),
            ({'realizado_por': ' '}, 'El campo «realizado_por» es obligatorio.'),
            ({'resultado': 'apto'}, 'El valor del campo «resultado» no es válido.'),
            ({'tipo': 'calibracion'}, 'El valor del campo «tipo» no es válido.'),
            ({'resultado': 'no_conforme', 'acciones': ' '}, 'Un resultado no conforme requiere indicar las acciones realizadas.'),
            ({'elemento_id': 999}, 'Elemento de infraestructura no encontrado.'),
            ({'no_conformidad_id': 999}, 'El campo «no_conformidad_id» no corresponde a ninguna no conformidad.'),
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
            self.service.maintenance.create(db.session, ADMIN,
                {k: v for k, v in ENTRY.items() if k != 'fecha_proxima'} | dict(elemento_id=self.item.id), today=DAY)
        self.assertEqual('Faltan campos obligatorios: fecha_proxima.', caught.exception.message)
        self.assertEqual(before, self.state())

    def test_same_day_entries_immutable_retirement_and_deletion(self):
        first = self.record()
        failed = self.record(tipo='correctivo', resultado='no_conforme', acciones='Cambiar junta')
        self.assertGreater(failed.id, first.id)
        self.assertEqual(failed, self.service.latest(db.session, self.item.id))
        before = self.state()
        for operation in (lambda: self.service.maintenance.update(db.session, ADMIN, first.id, {}, today=DAY),
                          lambda: self.service.maintenance.delete(db.session, ADMIN, first.id)):
            with self.assertRaises(ValidationError) as caught:
                operation()
            self.assertEqual(IMMUTABLE, caught.exception.message)
            self.assertEqual(before, self.state())
        with self.assertRaises(Conflict) as caught:
            self.service.items.delete(db.session, ADMIN, self.item.id)
        self.assertEqual('No se puede eliminar un elemento con mantenimientos; retíralo en su lugar.', caught.exception.message)
        self.assertEqual(before, self.state())
        self.service.items.update(db.session, ADMIN, self.item.id, {'retirado': True})
        db.session.commit()
        before = self.state()
        with self.assertRaises(ValidationError) as caught:
            self.record()
        self.assertEqual('No se pueden registrar mantenimientos de un elemento retirado.', caught.exception.message)
        self.assertEqual(before, self.state())
        empty = self.service.items.create(db.session, ADMIN, MASTER | dict(codigo='EMPTY'))
        db.session.commit()
        self.service.items.delete(db.session, ADMIN, empty.id)
        db.session.commit()
        self.assertEqual(1, self.service.items.list_page(db.session, ADMIN)[1])

    def test_optional_existing_nc_citation_never_creates_nc_and_protects_deletion(self):
        self.record(resultado='no_conforme', acciones='Reparar')
        self.assertEqual([], nonconformities.list_(db.session, ADMIN))
        nc = nonconformities.create(db.session, ADMIN,
            dict(descripcion='Fuga', fecha_detectada=DAY, responsable='Ana'))
        db.session.commit()
        cited = self.record(no_conformidad_id=nc.id)
        before = self.state()
        with self.assertRaises(Conflict):
            nonconformities.delete(db.session, ADMIN, nc.id)
        self.assertEqual(before, self.state())
        self.assertEqual(nc.id, self.service.maintenance.get(db.session, ADMIN, cited.id).no_conformidad_id)
        unused = nonconformities.create(db.session, ADMIN,
            dict(descripcion='Unused', fecha_detectada=DAY, responsable='Ana'))
        db.session.commit()
        nonconformities.delete(db.session, ADMIN, unused.id)
        db.session.commit()
        self.assertEqual([nc.id], [r.id for r in nonconformities.list_(db.session, ADMIN)])

    def test_permissions_filters_paging_and_overdue_boundaries(self):
        self.assertFalse(self.service.is_overdue(self.item, today=DAY))
        self.assertTrue(self.service.is_overdue(self.item, today=NEXT))
        for role in RoleEnum:
            who = Actor(None, 'role', role, 'cli')
            self.assertEqual(1, self.service.items.list_page(db.session, who)[1])
            if role is RoleEnum.OPERATIVO:
                before = self.state()
                for operation in (lambda: self.service.items.create(db.session, who, MASTER),
                                  lambda: self.service.items.update(db.session, who, self.item.id, {}),
                                  lambda: self.service.maintenance.create(db.session, who, {}, today=DAY)):
                    with self.assertRaises(PermissionDenied):
                        operation()
                self.assertEqual(before, self.state())
            else:
                self.service.items.update(db.session, who, self.item.id, {'ubicacion': role.name})
                db.session.commit()
        for filters, count in (({'codigo': 'inf'}, 1), ({'tipo': 'edificio'}, 0),
                               ({'tipo': 'unknown', 'retirado': 'unknown'}, 1),
                               ({'retirado': True}, 0), ({'proceso_id': 999}, 0)):
            self.assertEqual(count, self.service.items.list_page(db.session, ADMIN, **filters)[1])
        self.assertEqual(([], 1), self.service.items.list_page(db.session, ADMIN, page=2, per_page=1))
        self.service.items.update(db.session, ADMIN, self.item.id, {'retirado': True})
        db.session.commit()
        self.assertFalse(self.service.is_overdue(self.item, today=NEXT))
