"""PS5 rendered forms, channel boundary, filters and additive migration."""
import importlib.util
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from test_production_post_delivery import PostDeliveryCase, ACTIVITY, RESULT, TODAY
from test_production_property import PropertyCase
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum


class PostDeliveryAvailability(PropertyCase):
    def test_list_route_is_available(self):
        self.login()
        self.assertEqual(200, self.client.get('/produccion/posventa/').status_code)


class PostDeliveryScreens(PostDeliveryCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        from flask import g
        g.pop('_login_user', None)

    def test_roundtrip_dropped_unknown_uploaded_foreign_and_nonform(self):
        row = self.activity(order_ids=[self.create().id])
        self.login()
        path = f'/produccion/posventa/{row.id}/editar'
        parser = BrowserForm()
        parser.feed(self.client.get(path).get_data(as_text=True))
        form = parser.values
        self.assertEqual(['1'], form.getlist('order_ids'))
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())
        for key in form:
            if key != 'csrf_token':
                bad = form.copy()
                bad.pop(key)
                self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
                self.assertEqual(before, hashes())
        bad = form.copy()
        bad.setlist('order_ids', ['', '1'])
        response = self.client.post(path, data=bad)
        self.assertEqual(400, response.status_code)
        self.assertIn('Elige «Sin órdenes» o una o varias órdenes, no ambas cosas.', response.get_data(as_text=True))
        self.assertEqual(before, hashes())
        for data in ({'id': '2'}, {'item_id': '2'}, {'status': 'completed'},
                     {'description': (BytesIO(b'x'), 'x.txt')}):
            self.assertEqual(400, self.client.post(path, data=dict(form) | data).status_code)
            self.assertEqual(before, hashes())
        for suffix in ('editar', 'realizar', 'anular', 'eliminar'):
            self.assertEqual(400, self.client.post(f'/produccion/posventa/1/{suffix}', json={'id': 2}).status_code)
            self.assertEqual(before, hashes())
        self.assertEqual(200, self.client.get('/produccion/posventa/nueva').status_code)

    def test_filter_rendering_ignores_unknown_values_and_due_today_not_overdue(self):
        self.activity(description='Late visit', due_date=TODAY.replace(day=1))
        self.activity(description='Today visit', kind='other', customer_id=None)
        self.login()
        with patch('app.routes.production_routes.local_today', return_value=TODAY):
            for query, yes, no in (('overdue=true', 'Late visit', 'Today visit'),
                ('kind=other', 'Today visit', 'Late visit'), ('customer_id=1', 'Late visit', 'Today visit'),
                ('status=pending&responsible_id=1', 'Today visit', None),
                ('status=bad&kind=bad&customer_id=999&responsible_id=no&overdue=bad', 'Today visit', None)):
                response = self.client.get('/produccion/posventa/?' + query)
                self.assertEqual(200, response.status_code)
                text = response.get_data(as_text=True)
                self.assertIn(yes, text)
                if no:
                    self.assertNotIn(no, text)
            self.assertIn('Vencida', self.client.get('/produccion/posventa/').get_data(as_text=True))

    def test_web_permissions_csrf_terminal_and_transition_payloads(self):
        row = self.activity()
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/produccion/posventa/1').status_code)
            self.assertEqual(302 if role is RoleEnum.OPERATIVO else 200,
                self.client.get('/produccion/posventa/nueva').status_code)
            if role is RoleEnum.OPERATIVO:
                before = hashes()
                for suffix in ('editar', 'realizar', 'anular', 'eliminar'):
                    self.assertEqual(302, self.client.post('/produccion/posventa/1/' + suffix).status_code)
                    self.assertEqual(before, hashes())
            elif role is RoleEnum.AUDITOR:
                before = hashes()
                self.client.post('/produccion/posventa/1/eliminar')
                self.assertEqual(before, hashes())
        self.login()
        self.app.config['WTF_CSRF_ENABLED'] = True
        before = hashes()
        self.assertEqual(400, self.client.post('/produccion/posventa/1/realizar').status_code)
        self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = False
        for suffix, data in (('realizar', {'result_date': '2020-01-01', 'result': 'Done', 'evidence': 'R-1'}),
                             ('anular', {'result_date': '2020-01-01', 'reason': 'Stop'})):
            for key in data:
                self.assertEqual(400, self.client.post('/produccion/posventa/1/' + suffix,
                    data={k: v for k, v in data.items() if k != key}).status_code)
                self.assertEqual(before, hashes())
        self.assertEqual(302, self.client.post('/produccion/posventa/1/realizar', data={
            'result_date': '2020-01-01', 'result': 'Done', 'evidence': 'R-1'}).status_code)
        self.assertEqual('completed', self.post.activities.get(db.session, self.who, row.id).status.name)
        self.assertEqual(302, self.client.get('/produccion/posventa/1/editar').status_code)
        self.assertEqual(405, self.client.get('/produccion/posventa/1/realizar').status_code)
        self.assertIn('R-1', self.client.get('/produccion/posventa/1').get_data(as_text=True))

    def test_cited_target_delete_paths_refuse_with_messages(self):
        from app.services import clientes, nonconformities, reclamaciones
        from app.services.errors import Conflict
        order = self.create()
        nc = nonconformities.create(db.session, self.who, {'descripcion': 'Fault', 'fecha_detectada': TODAY})
        complaint = reclamaciones.create(db.session, self.who, {'cliente_id': 1, 'descripcion': 'Fault'}, today=TODAY)
        db.session.commit()
        self.activity(order_ids=[order.id], complaint_id=complaint.id, nonconformity_id=nc.id)
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        self.reject(lambda: reclamaciones.delete(db.session, self.who, complaint.id), error=Conflict)
        self.login()
        for path in ('/clientes/1/eliminar', '/personas/1/eliminar', '/produccion/1/eliminar', '/no_conformidades/eliminar/1'):
            with self.client.session_transaction() as session:
                session.pop('_flashes', None)
            before = hashes()
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertEqual(before, hashes())
            self.assertTrue(any(level == 'danger' and message for level, message in self.flashes()))

    def test_migration_preserves_ps1_to_ps4_constraints_and_reverse(self):
        from app.models import PostDeliveryActivity, post_delivery_orders
        from app.services import production_changes, production_records
        self.create()
        self.item(order_ids=[1])
        self.incident()
        production_changes.changes.create(db.session, self.who,
            dict(reason='Why', description='What', actions='Act', order_ids=[1]))
        self.service.transition(db.session, self.who, 1, 'in_progress')
        production_records.preservation.create(db.session, self.who, dict(order_id=1,
            date=TODAY, responsible_id=1, aspect='storage', description='Dry', result='conforming'), today=TODAY)
        db.session.commit()
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_post_delivery_activities.py'))
        spec = importlib.util.spec_from_file_location('ps5', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('d9f1a3b5c7e0', migration.down_revision)
        tables = [post_delivery_orders, PostDeliveryActivity.__table__]
        db.metadata.drop_all(db.engine, tables=tables)
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return [(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all()) for t in legacy]
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                for table in tables:
                    info = inspect(connection)
                    self.assertTrue(info.get_pk_constraint(table.name)['name'])
                    for fk in info.get_foreign_keys(table.name):
                        self.assertTrue(fk['name'])
                        if fk['referred_table'] != 'users':
                            self.assertEqual('RESTRICT', fk['options']['ondelete'])
                connection.execute(PostDeliveryActivity.__table__.insert().values(**ACTIVITY))
                for values in ({'status': 'released'}, {'status': 'completed'}, {'status': 'cancelled'},
                    {'result': 'Extra'}, {'result_date': TODAY}, {'reason': 'Extra'}, {'evidence': 'Extra'},
                    {'status': 'completed', 'result_date': TODAY, 'result': ' ', 'evidence': 'R-1'}):
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(PostDeliveryActivity.__table__.update().values(**values))
                migration.downgrade()
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())


class PostDeliveryMcp(McpDbCase):
    async def test_roles_roundtrip_overdue_and_no_lifecycle_or_delete(self):
        self.seed('personas')
        self.seed('clientes')
        data = {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in ACTIVITY.items()}
        for role in RoleEnum:
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'post_delivery_activities', 'data': data})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                self.assertEqual(before, hashes())
                continue
            rid = result.structured_content['id']
            self.assertIsInstance(result.structured_content['overdue'], bool)
            self.assertFalse((await self.call(mcp_actor(role), 'qms_update', {
                'module': 'post_delivery_activities', 'id': rid, 'data': {'source': 'Contract'}})).is_error)
            for key in ('status', 'result_date', 'result', 'evidence', 'reason', 'overdue'):
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_update', {
                    'module': 'post_delivery_activities', 'id': rid, 'data': {key: 'completed'}})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
            for reader in RoleEnum:
                self.assertFalse((await self.call(mcp_actor(reader), 'qms_get', {'module': 'post_delivery_activities', 'id': rid})).is_error)
                self.assertFalse((await self.call(mcp_actor(reader), 'qms_list', {'module': 'post_delivery_activities', 'filters': {'overdue': True}})).is_error)
        from test_design import actor
        from app.services import production_post_delivery
        production_post_delivery.transition(db.session, actor(), 1, 'completed', today=TODAY, **RESULT)
        db.session.commit()
        before = hashes()
        result = await self.call(mcp_actor(), 'qms_get', {'module': 'post_delivery_activities', 'id': 1})
        self.assertFalse(result.is_error)
        self.assertFalse(result.structured_content['overdue'])
        self.assertEqual('realizada', result.structured_content['status'])
        result = await self.call(mcp_actor(), 'qms_update', {'module': 'post_delivery_activities', 'id': 1, 'data': {}})
        self.assertTrue(result.is_error)
        self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})
