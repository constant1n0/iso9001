"""PS2 public contracts; every refused write preserves complete DB/audit hashes."""
from datetime import date, timedelta
from itertools import product
from unittest.mock import patch

from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from test_production import ProductionTests
from test_design import actor
from test_design_io import TODAY
from test_communication_independent import hashes
from app.extensions import db
from app.models import AuditLog, Person, ProductionOrder, ProductionState, RoleEnum
from app.services import people, equipment, nonconformities
from app.services.errors import Conflict, PermissionDenied, ValidationError

CONTROL = dict(order_id=1, kind='inspection', date=TODAY, responsible_id=1,
               description='Check dimensions', result='conforming', evidence='Report R-1')
PRESERVATION = dict(order_id=1, aspect='storage', date=TODAY, responsible_id=1,
                    description='Dry warehouse', result='conforming')


class ProductionRecords(ProductionTests):
    # Reuse PS1 fixture and its hash-preserving rejection helper, not its tests.
    def setUp(self):
        super().setUp()
        self.order = self.create()
        self.service.transition(db.session, self.who, self.order.id, 'in_progress')
        db.session.commit()

    def registers(self):
        from app.services import production_records
        return ((production_records.controls, CONTROL),
                (production_records.preservation, PRESERVATION))

    def append(self, register, data):
        row = register.create(db.session, self.who, data, today=TODAY)
        db.session.commit()
        return row

    def test_record_required_fields_enums_and_unknown_fields(self):
        for register, data in self.registers():
            for field in register.SPEC.fields:
                if field.required:
                    self.reject(lambda: self.append(register, {k: v for k, v in data.items()
                                                              if k != field.name}),
                                f'Faltan campos obligatorios: {field.name}.')
            key = 'kind' if 'kind' in data else 'aspect'
            for changes in ({key: 'unknown'}, {'result': 'released'}, {'description': ' '},
                            {'responsible_id': True}, {'order_id': 999}, {'id': 1},
                            {'created_by_id': 1}, {'equipment_id': 999} if key == 'kind'
                            else {'evidence': 'foreign field'}):
                self.reject(lambda: self.append(register, data | changes))

    def test_record_date_not_future_and_same_day_backdates_allowed(self):
        for register, data in self.registers():
            self.reject(lambda: self.append(register, data | {'date': TODAY + timedelta(days=1)}),
                        'La fecha del registro no puede ser futura.')
            rows = [self.append(register, data | {'date': day}) for day in
                    (TODAY, TODAY, TODAY - timedelta(days=1))]
            self.assertEqual([rows[1].id, rows[0].id, rows[2].id],
                             [r.id for r in register.list_(db.session, self.who, order_id=1)])

    def test_record_responsible_active_when_selected_and_retained_history(self):
        for register, data in self.registers():
            self.reject(lambda: self.append(register, data | {'responsible_id': 999}))
            row = self.append(register, data)
            people.update(db.session, self.who, 1, {'activo': False})
            db.session.commit()
            self.assertEqual(1, register.get(db.session, self.who, row.id).responsible_id)
            self.reject(lambda: self.append(register, data),
                        'La persona del campo «responsible_id» está desactivada; elige una persona activa.')
            people.update(db.session, self.who, 1, {'activo': True})
            db.session.commit()

    def test_record_equipment_suitable_on_date_inclusive_and_refresh(self):
        register, data = self.registers()[0]
        tool = self.equipment('Tool')
        equipment.checks.create(db.session, self.who, dict(equipo_id=tool.id,
            fecha=TODAY, tipo='verificacion', resultado='no_apto', realizado_por='Lab',
            referencia='Failure', evaluacion_impacto='Assess', acciones='Repair'), today=TODAY)
        db.session.commit()
        self.append(register, data | {'equipment_id': tool.id, 'date': date(2020, 1, 1)})
        for day in (date(2019, 12, 31), TODAY):
            self.reject(lambda: self.append(register, data | {'equipment_id': tool.id, 'date': day}),
                        'Elige un equipo apto en la fecha del registro.')
        tool2 = equipment.equipment.create(db.session, self.who, {'codigo': 'Due', 'nombre': 'Due'})
        equipment.checks.create(db.session, self.who, dict(equipo_id=tool2.id,
            fecha=date(2020, 1, 1), tipo='calibracion', resultado='apto', realizado_por='Lab',
            referencia='Standard', fecha_proxima=TODAY), today=TODAY)
        db.session.commit()
        self.append(register, data | {'equipment_id': tool2.id})
        equipment.equipment.update(db.session, self.who, tool2.id, {'retirado': True})
        db.session.commit()
        self.reject(lambda: self.append(register, data | {'equipment_id': tool2.id}))

    def test_record_nonconforming_control_needs_nc_or_actions_preservation_optional(self):
        nc = nonconformities.create(db.session, self.who,
                                   {'descripcion': 'Defect', 'fecha_detectada': TODAY})
        db.session.commit()
        for register, data in self.registers():
            self.reject(lambda: self.append(register, data | {'nonconformity_id': 999}))
            failed = data | {'result': 'nonconforming'}
            if 'kind' in data:
                self.reject(lambda: self.append(register, failed),
                            'Indica una no conformidad o las acciones para un resultado no conforme.')
                self.append(register, failed | {'actions': 'Rework'})
            else:
                self.append(register, failed)
            row = self.append(register, failed | {'nonconformity_id': nc.id})
            self.assertEqual(nc.id, row.nonconformity_id)
            self.assertEqual([row.id], [r.id for r in register.list_(db.session, self.who,
                order_id=1, result='nonconforming', **({'kind': 'inspection'} if 'kind' in data
                                                      else {'aspect': 'storage'}))][:1])

    def test_record_allowed_order_states_and_cancelled_readonly(self):
        for state in ProductionState:
            order = self.create()
            if state is not ProductionState.planned:
                self.service.transition(db.session, self.who, order.id, 'in_progress')
                if state in (ProductionState.finished, ProductionState.cancelled):
                    self.service.transition(db.session, self.who, order.id, state.name,
                        cancellation_reason='Stop' if state is ProductionState.cancelled else None)
                db.session.commit()
            for register, data in self.registers():
                allowed = state is ProductionState.in_progress or (
                    state is ProductionState.finished and 'aspect' in data)
                call = lambda: self.append(register, data | {'order_id': order.id})
                if allowed:
                    call()
                else:
                    self.reject(call, 'El estado de la orden no admite este registro.')
        # PS1 finishing still needs no evidence and never introduces a release state.
        self.service.transition(db.session, self.who, 1, 'finished')
        db.session.commit()
        self.assertEqual('finished', self.service.orders.get(db.session, self.who, 1).state.name)

    def test_record_immutable_and_order_person_deletion_guards(self):
        for register, data in self.registers():
            row = self.append(register, data)
            for operation in (lambda: register.update(db.session, self.who, row.id, {}, today=TODAY),
                              lambda: register.delete(db.session, self.who, row.id)):
                self.reject(operation, 'Los registros no se modifican ni se eliminan; registra uno nuevo.')
        self.reject(lambda: people.delete(db.session, self.who, 1),
                    people.STILL_REFERENCED, error=Conflict)
        # Even a legacy/inconsistent planned order with evidence cannot be deleted.
        db.session.execute(ProductionOrder.__table__.update().where(ProductionOrder.id == 1)
                           .values(state=ProductionState.planned))
        db.session.commit()
        self.reject(lambda: self.service.orders.delete(db.session, self.who, 1),
                    'No puedes eliminar una orden con registros.', error=Conflict)

    def test_record_permission_matrix_and_attribution(self):
        for register, data in self.registers():
            for role, channel in product(RoleEnum, ('web', 'mcp')):
                who = actor(role, channel)
                if role is RoleEnum.OPERATIVO:
                    self.reject(lambda: register.create(db.session, who, data, today=TODAY),
                                error=PermissionDenied)
                else:
                    row = register.create(db.session, who, data, today=TODAY)
                    db.session.commit()
                    self.assertEqual(who.user_id, row.created_by_id)
                    log = db.session.scalar(select(AuditLog).where(
                        AuditLog.entity_type == row.__tablename__, AuditLog.entity_id == row.id))
                    self.assertEqual((channel, 'create', data['description']),
                                     (log.channel, log.action, log.after['description']))
                self.assertTrue(register.list_(db.session, who, order_id=1))
                self.assertEqual(1, register.get(db.session, who, 1).order_id)

    def test_record_parent_lock_precedes_refreshed_citations(self):
        from app.models import EquipoMedicion, NoConformidad
        tool = self.equipment('Lock')
        nc = nonconformities.create(db.session, self.who,
                                   {'descripcion': 'Defect', 'fecha_detectada': TODAY})
        db.session.commit()
        for register, data in self.registers():
            seen = []
            def capture(state):
                if state.is_select and state.statement._for_update_arg is not None:
                    seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                                 state.execution_options.get('populate_existing')))
            event.listen(db.session(), 'do_orm_execute', capture)
            try:
                self.append(register, data | {'nonconformity_id': nc.id} |
                            ({'equipment_id': tool.id} if 'kind' in data else {}))
                self.assertIn('FROM production_orders', seen[0][0])
                self.assertIn('FOR UPDATE', seen[0][0])
                for table in ('personas', 'no_conformidades', *(
                        ('equipos_medicion',) if 'kind' in data else ())):
                    self.assertTrue(any(f'FROM {table}' in sql and refresh
                                        for sql, refresh in seen[1:]), seen)
            finally:
                event.remove(db.session(), 'do_orm_execute', capture)
            # Cached active person must not hide a post-lock deactivation.
            original = self.service._lock
            def deactivate(session, record_id):
                row = original(session, record_id)
                session.execute(Person.__table__.update().where(Person.id == 1).values(activo=False))
                return row
            with patch.object(self.service, '_lock', side_effect=deactivate):
                self.reject(lambda: self.append(register, data))


# Avoid rerunning inherited PS1 tests in this subclass.
for _name in tuple(ProductionTests.__dict__):
    if _name.startswith('test_'):
        setattr(ProductionRecords, _name, None)
del ProductionTests
