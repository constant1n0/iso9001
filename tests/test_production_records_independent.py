"""Independent PS2 probes: S1 evidence/security, S2 citations, S3 non-release."""
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from register_routes import RegisterRoutesBase
from test_communication_independent import BrowserForm, hashes
from test_design import actor
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import ProductionOrder, ProductionState, RoleEnum
from app.services import people, process_operations, production, production_records as records
from app.services import equipment, nonconformities
from app.services.errors import Conflict, ValidationError

DAY = date(2026, 1, 15)
CONTROL = dict(order_id=1, responsible_id=1, date=DAY, kind='measurement',
               description='Diameter', result='conforming', evidence='Gauge sheet')
PRESERVE = dict(order_id=1, responsible_id=1, date=DAY, aspect='protection',
                description='Sealed container', result='nonconforming')


class IndependentRecords(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Inspector'})
        self.seed_with(process_operations, {'proceso': 'Service'})
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        production.orders.create(db.session, actor(), dict(code='S-1', kind='service',
            process_id=1, title='Maintenance', lot='Optional reference'))
        production.transition(db.session, actor(), 1, 'in_progress')
        db.session.commit()
        self.login(RoleEnum.ADMINISTRADOR)

    def append(self, register, data):
        row = register.create(db.session, actor(), data, today=DAY)
        db.session.commit()
        return row

    def refuse(self, call, message, error=ValidationError):
        before = hashes()
        with self.assertRaises(error) as caught:
            call()
        self.assertEqual(message, str(caught.exception))
        db.session.rollback()
        self.assertEqual(before, hashes())

    def test_s1_s2_dated_equipment_history(self):
        tool = equipment.equipment.create(db.session, actor(), {'codigo': 'G', 'nombre': 'Gauge'})
        base = dict(equipo_id=tool.id, fecha=DAY-timedelta(days=3), tipo='calibracion',
                    resultado='apto', realizado_por='Lab', referencia='C1', fecha_proxima=DAY- timedelta(days=1))
        equipment.checks.create(db.session, actor(), base, today=DAY)
        db.session.commit()
        payload = CONTROL | {'equipment_id': tool.id}
        msg = 'Elige un equipo apto en la fecha del registro.'
        self.refuse(lambda: self.append(records.controls, payload), msg)
        self.append(records.controls, payload | {'date': DAY-timedelta(days=1)})
        equipment.checks.create(db.session, actor(), base | dict(fecha=DAY,
            resultado='no_apto', fecha_proxima=None, evaluacion_impacto='Quarantine', acciones='Repair'), today=DAY)
        db.session.commit()
        self.refuse(lambda: self.append(records.controls, payload), msg)
        equipment.checks.create(db.session, actor(), base | dict(fecha=DAY,
            referencia='C2', fecha_proxima=DAY+timedelta(days=30)), today=DAY)
        db.session.commit()
        self.append(records.controls, payload)
        equipment.equipment.update(db.session, actor(), tool.id, {'retirado': True})
        db.session.commit()
        self.refuse(lambda: self.append(records.controls, payload | {'date': DAY-timedelta(days=1)}), msg)

    def test_s1_s3_failure_actions_preservation_and_finish(self):
        bad = CONTROL | {'result': 'nonconforming', 'actions': ' \n '}
        self.refuse(lambda: self.append(records.controls, bad), records.ACTIONS_REQUIRED)
        self.append(records.controls, bad | {'actions': 'Rework'})
        self.append(records.preservation, PRESERVE)
        production.transition(db.session, actor(), 1, 'finished')
        db.session.commit()
        self.append(records.preservation, PRESERVE)
        self.refuse(lambda: self.append(records.controls, CONTROL), 'El estado de la orden no admite este registro.')
        self.assertEqual(ProductionState.finished, db.session.get(ProductionOrder, 1).state)

    def test_s1_s2_citations_immutable_and_nc_delete_atomic(self):
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        nc = nonconformities.create(db.session, actor(), {'descripcion': 'Damage', 'fecha_detectada': DAY})
        db.session.commit()
        for register, payload in ((records.controls, CONTROL), (records.preservation, PRESERVE)):
            row = self.append(register, payload | {'nonconformity_id': nc.id})
            self.refuse(lambda: register.update(db.session, actor(), row.id, {}, today=DAY), records.IMMUTABLE)
            self.refuse(lambda: register.delete(db.session, actor(), row.id), records.IMMUTABLE)
        self.refuse(lambda: nonconformities.delete(db.session, actor(), nc.id), Conflict.default_message, Conflict)
        self.refuse(lambda: people.delete(db.session, actor(), 1), people.STILL_REFERENCED, Conflict)

    def test_s1_web_duplicate_optional_dropped_foreign_upload_and_csrf(self):
        for section in ('controles', 'preservacion'):
            url = f'/produccion/1/{section}/nuevo'
            parser = BrowserForm()
            parser.feed(self.client.get(url).get_data(as_text=True))
            form = parser.values
            form.update({'responsible_id': '1', 'description': 'Stored', 'evidence': 'Sheet'})
            if section == 'preservacion':
                form.pop('evidence')
            duplicate, missing = form.copy(), form.copy()
            duplicate.add('description', 'Discarded')
            missing.pop('nonconformity_id')
            for bad in (duplicate, missing, dict(form) | {'order_id': '2'},
                        dict(form) | {'attachment': (BytesIO(b'x'), 'x.txt')}):
                before = hashes()
                self.assertEqual(400, self.client.post(url, data=bad).status_code)
                self.assertEqual(before, hashes())
            self.app.config['WTF_CSRF_ENABLED'] = True
            before = hashes()
            self.assertEqual(400, self.client.post(url, data=form).status_code)
            self.assertEqual(before, hashes())
            self.app.config['WTF_CSRF_ENABLED'] = False

    def test_s1_parent_lock_refresh_rejects_cached_cancelled_order(self):
        cached = db.session.get(ProductionOrder, 1)
        db.session.execute(ProductionOrder.__table__.update().values(state=ProductionState.cancelled,
            cancellation_reason='Stop'))
        with patch.object(db.session(), 'expire_on_commit', False):
            db.session.commit()
        self.assertEqual(ProductionState.in_progress, cached.state)
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        self.addCleanup(event.remove, db.session(), 'do_orm_execute', capture)
        self.refuse(lambda: self.append(records.preservation, PRESERVE), 'El estado de la orden no admite este registro.')
        self.assertIn('FROM production_orders', seen[0][0])
        self.assertIn('FOR UPDATE', seen[0][0])
        self.assertTrue(seen[0][1])


class IndependentRecordMcp(McpDbCase):
    async def test_s1_operativo_and_explicit_foreign_fields(self):
        self.seed('production_control_records')
        for slug, payload in (('production_control_records', CONTROL), ('production_preservation_records', PRESERVE)):
            for who, data in ((mcp_actor(RoleEnum.OPERATIVO), payload),
                              (mcp_actor(), payload | {'created_by_id': 7})):
                before = hashes()
                result = await self.call(who, 'qms_create', {'module': slug, 'data': data | {'date': DAY.isoformat()}})
                self.assertTrue(result.is_error, result.content)
                msg = ('No tienes permiso para realizar esta acción.' if who.role is RoleEnum.OPERATIVO
                       else 'Campos no permitidos: created_by_id.')
                self.assertEqual('Error executing tool qms_create: ' + msg, result.content[0].text)
                self.assertEqual(before, hashes())
