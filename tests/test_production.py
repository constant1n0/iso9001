"""PS1 public order contract: validation, citations, lifecycle and audit integrity."""
from datetime import date
from itertools import product
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from register_routes import RegisterRoutesBase
from test_design import actor
from test_design_io import approved_revision, TODAY
from test_communication_independent import hashes
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import people, process_operations, clientes, equipment
from app.services.errors import Conflict, PermissionDenied, ValidationError

ORDER = dict(code='O-1', kind='production', title='Container', process_id=1)


class ProductionTests(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.seed_with(process_operations, {'proceso': 'Manufacturing'})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        from app.services import production
        self.service = production
        self.who = actor()
        self.number = 0

    def create(self, **values):
        self.number += 1
        row = self.service.orders.create(db.session, self.who,
            ORDER | {'code': f'O-{self.number}'} | values)
        db.session.commit()
        return row

    def reject(self, operation, message=None, error=ValidationError):
        before = hashes()
        with self.assertRaises(error) as caught:
            operation()
        if message is not None:
            self.assertEqual(message, str(caught.exception))
        db.session.rollback()
        self.assertEqual(before, hashes())

    def test_required_unique_kind_process_dates(self):
        for key in ORDER:
            self.reject(lambda: self.service.orders.create(db.session, self.who,
                {k: v for k, v in ORDER.items() if k != key}),
                f'Faltan campos obligatorios: {key}.')
        for key, bad in (('code', ''), ('title', ' \n '), ('kind', 'serial'),
                         ('process_id', None), ('process_id', 999), ('process_id', True)):
            self.reject(lambda: self.create(**{key: bad}))
        row = self.create(planned_start=TODAY, planned_end=TODAY, kind='service')
        self.assertEqual(('planned', 'service'), (row.state.name, row.kind.name))
        self.reject(lambda: self.create(code=row.code),
                    'Ya existe una orden con ese código.', error=Conflict)
        self.reject(lambda: self.create(planned_start=TODAY, planned_end=date(2020, 1, 1)),
                    'La fecha final prevista no puede ser anterior a la fecha inicial.')
        self.reject(lambda: self.service.orders.update(db.session, self.who, row.id,
                    {'planned_start': date(2099, 1, 1)}))
        customer = clientes.create(db.session, self.who, {'nombre': 'Customer'})
        db.session.commit()
        self.service.orders.update(db.session, self.who, row.id,
                                    {'customer_id': customer.id, 'lot': 'B-7'})
        db.session.commit()
        for filters in ({'state': 'planned'}, {'kind': 'service'}, {'process_id': 1},
                        {'customer_id': customer.id}, {'lot': 'B-'}):
            self.assertEqual([row.id], [r.id for r in self.service.orders.list_(
                db.session, self.who, **filters)])
        self.reject(lambda: self.create(customer_id=999))

    def test_people_eligibility_retention_and_deletion(self):
        inactive = people.create(db.session, self.who, {'nombre': 'Inactive', 'activo': False})
        db.session.commit()
        for data in ({'owner_id': inactive.id}, {'person_ids': [inactive.id]}):
            self.reject(lambda: self.create(**data))
        row = self.create(owner_id=1, person_ids=[1])
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        self.service.orders.update(db.session, self.who, row.id, {'owner_id': 1, 'person_ids': [1]})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.reject(lambda: people.delete(db.session, self.who, 1),
                    people.STILL_REFERENCED, error=Conflict)
        unused = people.create(db.session, self.who, {'nombre': 'Unused'})
        people.delete(db.session, self.who, unused.id)
        db.session.commit()

    def equipment(self, code):
        row = equipment.equipment.create(db.session, self.who, {'codigo': code, 'nombre': code})
        equipment.checks.create(db.session, self.who, dict(equipo_id=row.id,
            fecha=date(2020, 1, 1), tipo='calibracion', resultado='apto', realizado_por='Lab',
            referencia='Standard', fecha_proxima=date(2099, 1, 1)), today=TODAY)
        db.session.commit()
        return row

    def test_each_condition_set_replace_clear_audited_and_invalid_atomic(self):
        revisions = [approved_revision(self.who, code=f'R{n}', publish=bool(n))[1] for n in (0, 1)]
        devices = [self.equipment(f'E{n}').id for n in (0, 1)]
        person = people.create(db.session, self.who, {'nombre': 'Second'})
        db.session.commit()
        row = self.create()
        for key, ids in (('revision_ids', revisions), ('equipment_ids', devices),
                         ('person_ids', [1, person.id])):
            for values in (ids[:1], ids, ids[1:], []):
                self.service.orders.update(db.session, self.who, row.id, {key: values})
                db.session.commit()
                self.assertEqual(values, getattr(self.service.orders.get(db.session, self.who, row.id), key))
                log = AuditLog.query.filter_by(entity_type='production_orders').order_by(AuditLog.id.desc()).first()
                self.assertEqual(values, log.after[key])
            for bad in ([999], [True], None):
                self.reject(lambda: self.service.orders.update(db.session, self.who, row.id,
                            {'title': 'Must not persist', key: bad}))
        from app.services import document_revisions
        draft = document_revisions.start_draft(db.session, self.who, 2, {'author_id': 1})
        db.session.commit()
        self.reject(lambda: self.create(revision_ids=[draft.id]), self.service.INVALID_REVISION)
        device = equipment.equipment.create(db.session, self.who, {'codigo': 'Unchecked', 'nombre': 'Tool'})
        db.session.commit()
        self.reject(lambda: self.create(equipment_ids=[device.id]), 'Elige un equipo apto.')
        self.reject(lambda: self.create(revision_ids=[999]), self.service.INVALID_REVISION)
        with patch('app.services.document_revisions.get', side_effect=PermissionDenied()):
            self.reject(lambda: self.create(revision_ids=revisions), self.service.INVALID_REVISION)
        retained = self.create(revision_ids=revisions, equipment_ids=devices)
        document_revisions.withdraw(db.session, self.who, 2, 'Withdrawn', today=TODAY)
        equipment.equipment.update(db.session, self.who, devices[0], {'retirado': True})
        db.session.commit()
        before = hashes()
        self.service.orders.update(db.session, self.who, retained.id,
                                    {'revision_ids': revisions, 'equipment_ids': devices})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.reject(lambda: self.create(revision_ids=revisions), self.service.INVALID_REVISION)
        self.reject(lambda: self.create(equipment_ids=devices), 'Elige un equipo apto.')

    def test_exact_approved_output_only(self):
        from app.services import design, design_io, design_changes
        project = design.projects.create(db.session, self.who,
            dict(title='Design', description='Container', owner_id=1))
        output = design_io.outputs.create(db.session, self.who, dict(project_id=project.id,
            kind='other', text='Spec', acceptance_criteria='Fits'))
        db.session.commit()
        self.reject(lambda: self.create(design_output_id=output.id),
                    'Elige una versión aprobada de una salida del diseño.')
        approver = people.create(db.session, self.who, {'nombre': 'Approver',
            'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        db.session.commit()
        design_changes.approve_output(db.session, type(self.who)(approver.user_id, 'Approver', RoleEnum.ADMINISTRADOR, 'web'),
            output.id, today=TODAY)
        db.session.commit()
        row = self.create(design_output_id=output.id)
        self.assertEqual(output.id, row.design_output_id)
        before = hashes()
        self.service.orders.update(db.session, self.who, row.id, {'design_output_id': output.id})
        db.session.commit()
        self.assertEqual(before, hashes())

    def test_lifecycle_all_edges_reason_and_terminal_readonly(self):
        from app.models import ProductionState
        allowed = {('planned', 'in_progress'), ('in_progress', 'finished'),
                   ('planned', 'cancelled'), ('in_progress', 'cancelled')}
        for source, target in product(ProductionState, repeat=2):
            row = self.create()
            if source.name != 'planned':
                if source.name != 'cancelled':
                    self.service.transition(db.session, self.who, row.id, 'in_progress')
                if source.name in ('finished', 'cancelled'):
                    self.service.transition(db.session, self.who, row.id, source.name,
                                            cancellation_reason='Stop' if source.name == 'cancelled' else None)
                db.session.commit()
            if (source.name, target.name) in allowed:
                before = AuditLog.query.count()
                self.service.transition(db.session, self.who, row.id, target.name,
                                        cancellation_reason=' Stop ' if target.name == 'cancelled' else None)
                db.session.commit()
                self.assertEqual(target, row.state)
                self.assertEqual(before + 1, AuditLog.query.count())
            else:
                self.reject(lambda: self.service.transition(db.session, self.who, row.id,
                            target.name, cancellation_reason='Stop'))
            if row.state.name in ('finished', 'cancelled'):
                for data in ({}, {'title': row.title}, {'title': 'Edit'}):
                    self.reject(lambda: self.service.orders.update(db.session, self.who, row.id, data))
                self.reject(lambda: self.service.orders.delete(db.session, self.who, row.id))
        row = self.create()
        for reason in (None, '', ' \n '):
            self.reject(lambda: self.service.transition(db.session, self.who, row.id,
                        'cancelled', cancellation_reason=reason), 'Indica el motivo de la cancelación.')

    def test_permissions_and_readonly_fields(self):
        row = self.create()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, self.service.orders.get(db.session, who, row.id).id)
            self.assertIn(row, self.service.orders.list_(db.session, who))
            for operation in ('create', 'update'):
                call = (lambda: self.service.orders.create(db.session, who, ORDER | {'code': str(role) + channel})) if operation == 'create' else (
                    lambda: self.service.orders.update(db.session, who, row.id, {'title': str(role) + channel}))
                if role is RoleEnum.OPERATIVO:
                    self.reject(call, error=PermissionDenied)
                else:
                    call()
                    db.session.commit()
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.service.transition(db.session, who, row.id, 'in_progress'), error=PermissionDenied)
            if channel == 'mcp' or role is not RoleEnum.ADMINISTRADOR:
                self.reject(lambda: self.service.orders.delete(db.session, who, row.id), error=PermissionDenied)
        for key in ('state', 'cancellation_reason', 'target', 'id'):
            self.reject(lambda: self.service.orders.update(db.session, self.who, row.id, {key: 'x'}))
        self.service.orders.delete(db.session, self.who, row.id)
        db.session.commit()
        self.assertEqual('delete', AuditLog.query.order_by(AuditLog.id.desc()).first().action)

    def test_writes_lock_parent_and_refresh_targets(self):
        row = self.create(owner_id=1)
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            for operation in (lambda: self.service.orders.update(db.session, self.who, row.id, {'owner_id': 1}),
                              lambda: self.service.transition(db.session, self.who, row.id, 'in_progress')):
                seen.clear()
                operation()
                db.session.commit()
                self.assertTrue(any('FROM production_orders' in sql and 'FOR UPDATE' in sql and refresh
                                    for sql, refresh in seen), seen)
                if len(seen) > 1:
                    self.assertIn('FROM production_orders', seen[0][0])
                    self.assertTrue(any('FROM personas' in sql and refresh for sql, refresh in seen[1:]))
            self.assertTrue(seen)
            planned = self.create()
            seen.clear()
            self.service.orders.delete(db.session, self.who, planned.id)
            db.session.commit()
            self.assertTrue(any('FROM production_orders' in sql and refresh for sql, refresh in seen))
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
