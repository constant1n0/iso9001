"""PS1 browser round trips, refusals, migration and real MCP adapter probes."""
import importlib.util
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from register_routes import RegisterRoutesBase
from test_communication_independent import BrowserForm, hashes
from test_design import actor
from test_design_io import approved_revision
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people, process_operations, clientes

FORM = dict(code='O-1', kind='service', title='Service <A> & B', process_id='1',
            owner_id='1', lot='Batch & A', planned_start='2026-01-01', planned_end='2026-12-31')


class ProductionScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.seed_with(process_operations, {'proceso': 'Service'})
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        with self.app.app_context():
            return hashes()

    def browser(self):
        response = self.client.get('/produccion/1/editar')
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_rendered_noop_selectors_dropped_fields_files_and_filters(self):
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        with self.app.app_context():
            _, rid = approved_revision(actor())
        values = FORM | {'customer_id': str(customer), 'person_ids': ['1'], 'revision_ids': [str(rid)]}
        self.assertEqual(302, self.client.post('/produccion/nuevo', data=values).status_code)
        form = self.browser()
        for key, value in values.items():
            self.assertEqual(value if isinstance(value, list) else [value], form.getlist(key), key)
        before = self.state()
        self.assertEqual(302, self.client.post('/produccion/1/editar', data=form).status_code)
        self.assertEqual(before, self.state())
        with patch('app.routes.production_routes.qms_scope.current', return_value=type('Scope', (), {'id': 1})()), patch(
                'app.routes.production_routes.qms_scope.applicability_of', return_value={
                    '8.5.1': {'aplica': True}, '8.5.2': {'aplica': False, 'justificacion': 'Excluded scope'}}):
            page = self.client.get('/produccion/1').get_data(as_text=True)
            self.assertIn('8.5.1: Aplica', page)
            self.assertIn('8.5.2: No aplica', page)
            self.assertIn('Excluded scope', page)
        self.assertEqual(before, self.state())
        for data in ({'state': 'terminada'}, {'project_id': '1'}, {'order_id': '2'},
                     {'attachment': (BytesIO(b'x'), 'file.txt')}):
            response = self.client.post('/produccion/1/editar', data=dict(form) | data)
            self.assertEqual(400, response.status_code)
            self.assertEqual(before, self.state())
        self.assertEqual(200, self.client.get('/produccion/?state=unknown&kind=wrong&process_id=999').status_code)
        self.assertIn('O-1', self.client.get('/produccion/?lot=Batch').get_data(as_text=True))
        self.assertNotIn('O-1', self.client.get('/produccion/?lot=missing').get_data(as_text=True))

    def test_web_roles_csrf_terminal_label_and_restricted_revision(self):
        with self.app.app_context():
            _, rid = approved_revision(actor(), code='SECRET-42')
        self.client.post('/produccion/nuevo', data=FORM | {'revision_ids': [str(rid)]})
        self.login(RoleEnum.OPERATIVO)
        page = self.client.get('/produccion/1').get_data(as_text=True)
        self.assertIn('Revisión vinculada', page)
        for secret in ('SECRET-42', 'Restricted title', 'Restricted revision content'):
            self.assertNotIn(secret, page)
        self.assertIn('href="/produccion/"', page)
        for path in ('nuevo', '1/editar', '1/transicion', '1/eliminar'):
            before = self.state()
            self.assertEqual(302, self.client.post('/produccion/' + path, data=FORM).status_code)
            self.assertEqual(before, self.state())
        for role in (RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR):
            self.login(role)
            self.assertIn('action="/produccion/1/transicion"', self.client.get('/produccion/1').get_data(as_text=True))
        for target in ('in_progress', 'finished'):
            self.assertEqual(302, self.client.post('/produccion/1/transicion', data={'target': target}).status_code)
        page = self.client.get('/produccion/1').get_data(as_text=True)
        self.assertIn('trabajo terminado, NO es una autorización de entrega', page)
        self.assertNotIn('liberada', page.lower())
        self.assertNotIn('action="/produccion/1/transicion"', page)
        self.assertEqual(405, self.client.get('/produccion/1/transicion').status_code)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for path in ('/produccion/nuevo', '/produccion/1/editar', '/produccion/1/transicion'):
            before = self.state()
            self.assertEqual(400, self.client.post(path, data=FORM).status_code)
            self.assertEqual(before, self.state())

    def test_cited_deletions_are_user_facing_refusals(self):
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        with self.app.app_context():
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        self.client.post('/produccion/nuevo', data=FORM | {'customer_id': str(customer), 'person_ids': ['1']})
        for path in ('/personas/1/eliminar', '/procesos/1/eliminar', f'/clientes/{customer}/eliminar'):
            before = self.state()
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertTrue(any(level == 'danger' for level, _ in self.flashes()))
            self.assertEqual(before, self.state())

    def test_additive_migration_reverse_preserves_existing_rows(self):
        from app.models import ProductionOrder, PRODUCTION_ASSOCIATIONS
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_production_orders.py'))
        spec = importlib.util.spec_from_file_location('production_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('f5b7c9d1e3a5', migration.down_revision)
        with self.app.app_context():
            tables = [*PRODUCTION_ASSOCIATIONS.values(), ProductionOrder.__table__]
            db.metadata.drop_all(db.engine, tables=tables)
            with db.engine.begin() as connection:
                legacy = inspect(connection).get_table_names()
                def snapshot():
                    return [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all()) for name in legacy]
                before = snapshot()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(before, snapshot())
                    for table in tables:
                        self.assertTrue(inspect(connection).get_pk_constraint(table.name)['name'])
                        for fk in inspect(connection).get_foreign_keys(table.name):
                            self.assertTrue(fk['name'])
                            if fk['referred_table'] != 'users':
                                self.assertEqual('RESTRICT', fk['options']['ondelete'])
                    checks = {c['name'] for c in inspect(connection).get_check_constraints('production_orders')}
                    self.assertLessEqual({f'ck_production_orders_{key}' for key in
                        ('code', 'title', 'kind', 'state', 'dates', 'cancellation')}, checks)
                    table = ProductionOrder.__table__
                    valid = dict(code='O-1', kind='production', title='Container', process_id=1)
                    connection.execute(table.insert().values(**valid))
                    for changes in ({'code': ''}, {'kind': 'other'}, {'state': 'unknown'},
                                    {'state': 'cancelled'}, {'cancellation_reason': 'Unexpected'},
                                    {'planned_start': date(2026, 2, 1),
                                     'planned_end': date(2026, 1, 1)}):
                        with self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(table.update().values(**changes))
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(table.insert().values(**valid))
                    migration.downgrade()
                    self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                    self.assertEqual(before, snapshot())


class ProductionMcp(McpDbCase):
    async def test_real_mcp_roles_filters_unknown_fields_no_lifecycle_tools(self):
        self.seed('procesos')
        tools = {tool.name for tool in await self.list_tools()}
        self.assertFalse({'qms_delete', 'qms_transition'} & tools)
        for role in RoleEnum:
            who = mcp_actor(role)
            result = await self.call(who, 'qms_create', {'module': 'production_orders',
                'data': {'code': role.name, 'kind': 'production', 'title': 'Order', 'process_id': 1}})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                continue
            rid = result.structured_content['id']
            for data in ({'state': 'terminada'}, {'target': 'en_curso'}, {'cancellation_reason': 'Stop'}):
                before = hashes()
                result = await self.call(who, 'qms_update', {'module': 'production_orders', 'id': rid, 'data': data})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
            for reader in RoleEnum:
                result = await self.call(mcp_actor(reader), 'qms_get', {'module': 'production_orders', 'id': rid})
                self.assertFalse(result.is_error, result.content)
                result = await self.call(mcp_actor(reader), 'qms_list', {'module': 'production_orders', 'filters': {'state': 'planned'}})
                self.assertFalse(result.is_error, result.content)
