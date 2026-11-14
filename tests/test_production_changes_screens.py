"""PS3 rendered forms, MCP adapter, restrictive schema and additive migration."""
import importlib.util
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from test_production_changes import ChangeCase, CHANGE
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum


class ChangeScreens(ChangeCase):
    def login(self, role=RoleEnum.OPERATIVO):
        super().login(role)
        # This service fixture retains an app context across browser requests.
        from flask import g
        g.pop('_login_user', None)

    def test_rendered_noop_and_every_dropped_unknown_uploaded_foreign_field(self):
        row = self.propose(description='Tooling <A> & B', review='Reviewed')
        self.login(RoleEnum.ADMINISTRADOR)
        path = f'/produccion/cambios/{row.id}/editar'
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        form = parser.values
        self.assertEqual(['1'], form.getlist('order_ids'))
        self.assertEqual('Tooling <A> & B', form['description'])
        before = hashes()
        new_path = '/produccion/cambios/nuevo'
        self.assertEqual(200, self.client.get(new_path).status_code)
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())
        for key in form:
            if key == 'csrf_token':
                continue
            bad = form.copy()
            bad.pop(key)
            self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
            self.assertEqual(before, hashes())
        for foreign in ({'id': '2'}, {'order_id': '2'}, {'project_id': '1'}, {'state': 'authorized'},
                        {'reason': (BytesIO(b'x'), 'value.txt')}):
            self.assertEqual(400, self.client.post(path, data=dict(form) | foreign).status_code)
            self.assertEqual(before, hashes())
        self.assertIn('Cambios de producción', self.client.get('/produccion/1').get_data(as_text=True))
        self.assertIn('Tooling &lt;A&gt; &amp; B', self.client.get('/produccion/cambios/1').get_data(as_text=True))
        self.assertEqual(200, self.client.get('/produccion/cambios/?state=proposed&order_id=1').status_code)

    def test_web_roles_csrf_and_decided_forms(self):
        row = self.propose(review='Reviewed')
        for role in RoleEnum:
            self.login(role)
            before = hashes()
            for action in ('autorizar', 'rechazar'):
                if role is RoleEnum.ADMINISTRADOR:
                    continue
                self.client.post(f'/produccion/cambios/{row.id}/{action}', data={'reason': 'No'})
                self.assertEqual(before, hashes())
            self.assertEqual(302 if role is RoleEnum.OPERATIVO else 200,
                self.client.get('/produccion/cambios/nuevo').status_code)
        self.login(RoleEnum.ADMINISTRADOR)
        before = hashes()
        for action in ('autorizar', 'rechazar'):
            self.assertEqual(400, self.client.post(f'/produccion/cambios/{row.id}/{action}',
                data={'order_id': '2'}).status_code)
            self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = True
        for action in ('autorizar', 'rechazar', 'editar'):
            self.assertEqual(400, self.client.post(f'/produccion/cambios/{row.id}/{action}').status_code)
            self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.assertEqual(302, self.client.post('/produccion/cambios/1/autorizar').status_code)
        self.assertEqual('authorized', self.changes.changes.get(db.session, self.who, 1).state.name)
        self.assertEqual(302, self.client.get('/produccion/cambios/1/editar').status_code)
        page = self.client.get('/produccion/cambios/1').get_data(as_text=True)
        self.assertNotIn('action="/produccion/cambios/1/autorizar"', page)
        self.assertEqual(404, self.client.post('/produccion/cambios/1/eliminar').status_code)

    def test_additive_migration_preserves_ps1_ps2_and_restricts_all_citations(self):
        from app.models import ProductionChange, production_change_orders
        from app.services import production_records
        self.service.transition(db.session, self.who, 1, 'in_progress')
        production_records.preservation.create(db.session, self.who, dict(order_id=1,
            date=date(2020, 1, 1), responsible_id=1, aspect='storage',
            description='Dry', result='conforming'), today=date(2026, 1, 1))
        db.session.commit()
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_production_changes.py'))
        spec = importlib.util.spec_from_file_location('ps3_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('b7d9f1a3c5e7', migration.down_revision)
        tables = [production_change_orders, ProductionChange.__table__]
        db.metadata.drop_all(db.engine, tables=tables)
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all()) for name in legacy]
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                for table in tables:
                    info = inspect(connection)
                    self.assertTrue(info.get_pk_constraint(table.name)['name'])
                    indexes = [i['column_names'] for i in info.get_indexes(table.name)]
                    for fk in info.get_foreign_keys(table.name):
                        self.assertTrue(fk['name'])
                        self.assertTrue(fk['constrained_columns'] in indexes or
                            fk['constrained_columns'] == ['change_id'])
                        if fk['referred_table'] != 'users':
                            self.assertEqual('RESTRICT', fk['options']['ondelete'])
                            self.assertEqual('RESTRICT', fk['options']['onupdate'])
                table = ProductionChange.__table__
                connection.execute(table.insert().values(reason='Why', description='What', actions='Act'))
                for bad in ({'state': 'released'}, {'reason': ''}, {'state': 'rejected'},
                            {'rejection_reason': 'Extra'}, {'state': 'authorized'},
                            {'authorized_by_id': 2}):
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(table.update().values(**bad))
                migration.downgrade()
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())


class ChangeMcp(McpDbCase):
    async def test_proposed_create_update_read_filters_and_refused_lifecycle_hashes(self):
        self.seed('production_orders')
        slug = 'production_changes'
        for role in RoleEnum:
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {'module': slug, 'data': CHANGE})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                self.assertEqual(before, hashes())
                continue
            rid = result.structured_content['id']
            result = await self.call(mcp_actor(role), 'qms_update',
                {'module': slug, 'id': rid, 'data': {'review': 'Checked'}})
            self.assertFalse(result.is_error, result.content)
            for reader in RoleEnum:
                result = await self.call(mcp_actor(reader), 'qms_get', {'module': slug, 'id': rid})
                self.assertFalse(result.is_error, result.content)
                result = await self.call(mcp_actor(reader), 'qms_list',
                    {'module': slug, 'filters': {'state': 'proposed', 'order_id': 1}})
                self.assertFalse(result.is_error, result.content)
            for data in ({'state': 'authorized'}, {'authorized_by_id': 1}, {'target': 'rejected'}):
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_update',
                    {'module': slug, 'id': rid, 'data': data})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
        self.assertEqual({'qms_create', 'qms_update', 'qms_get', 'qms_list', 'qms_modules'},
                         {t.name for t in await self.list_tools()})
