"""PS2 browser/MCP boundaries, referential refusals and additive migration."""
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
from test_production_records import CONTROL, PRESERVATION
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people, process_operations, production, nonconformities


class RecordScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.seed_with(process_operations, {'proceso': 'Manufacturing'})
        with self.app.app_context():
            production.orders.create(db.session, actor(), dict(code='O-1', kind='production',
                title='Container', process_id=1))
            production.transition(db.session, actor(), 1, 'in_progress')
            db.session.commit()
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        with self.app.app_context():
            return hashes()

    def browser(self, section):
        response = self.client.get(f'/produccion/1/{section}/nuevo')
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_record_rendered_defaults_and_dropped_unknown_uploaded_foreign_fields(self):
        for section, key, default in (('controles', 'kind', 'inspection'),
                                       ('preservacion', 'aspect', 'identification')):
            form = self.browser(section)
            self.assertEqual(default, form[key])
            self.assertEqual('conforming', form['result'])
            form['responsible_id'], form['description'] = '1', 'Checked <A> & B'
            before = self.state()
            for dropped in ('date', key, 'result', 'responsible_id', 'description'):
                bad = form.copy()
                bad.pop(dropped)
                response = self.client.post(f'/produccion/1/{section}/nuevo', data=bad)
                self.assertEqual(400, response.status_code)
                self.assertEqual(before, self.state())
            for foreign in ({'order_id': '2'}, {'id': '1'}, {'target': 'finished'},
                            {'attachment': (BytesIO(b'x'), 'evidence.txt')},
                            {key: (BytesIO(b'x'), 'value.txt')}):
                response = self.client.post(f'/produccion/1/{section}/nuevo', data=dict(form) | foreign)
                self.assertEqual(400, response.status_code)
                self.assertEqual(before, self.state())
            if section == 'controles':
                form['evidence'] = 'Report'
            self.assertEqual(302, self.client.post(f'/produccion/1/{section}/nuevo', data=form).status_code)
        page = self.client.get('/produccion/1').get_data(as_text=True)
        for text in ('Registros de ejecución y control', 'Preservación', 'Checked &lt;A&gt; &amp; B'):
            self.assertIn(text, page)

    def test_record_web_permissions_csrf_and_no_edit_delete_routes(self):
        for role in RoleEnum:
            self.login(role)
            for section in ('controles', 'preservacion'):
                before = self.state()
                response = self.client.get(f'/produccion/1/{section}/nuevo')
                self.assertEqual(302 if role is RoleEnum.OPERATIVO else 200, response.status_code)
                if role is RoleEnum.OPERATIVO:
                    self.assertEqual(302, self.client.post(f'/produccion/1/{section}/nuevo', data={}).status_code)
                for action in ('editar', 'eliminar'):
                    self.assertEqual(404, self.client.post(f'/produccion/1/{section}/1/{action}').status_code)
                self.assertEqual(before, self.state())
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        before = self.state()
        self.assertEqual(400, self.client.post('/produccion/1/controles/nuevo', data={}).status_code)
        self.assertEqual(before, self.state())

    def test_record_cited_people_equipment_nc_user_facing_delete_refusals(self):
        from app.services import equipment, production_records
        with self.app.app_context():
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            tool = equipment.equipment.create(db.session, actor(), {'codigo': 'E', 'nombre': 'Tool'})
            equipment.checks.create(db.session, actor(), dict(equipo_id=tool.id,
                fecha=date(2020, 1, 1), tipo='calibracion', resultado='apto', realizado_por='Lab',
                referencia='Standard', fecha_proxima=date(2099, 1, 1)), today=date(2026, 1, 1))
            nc = nonconformities.create(db.session, actor(),
                                       {'descripcion': 'Defect', 'fecha_detectada': date(2020, 1, 1)})
            db.session.commit()
            production_records.controls.create(db.session, actor(), CONTROL |
                {'equipment_id': tool.id, 'nonconformity_id': nc.id}, today=CONTROL['date'])
            db.session.commit()
        for path in ('/personas/1/eliminar', '/equipos/1/eliminar', '/no_conformidades/eliminar/1'):
            before = self.state()
            self.assertEqual(302, self.client.post(path).status_code, path)
            self.assertTrue(any(level == 'danger' for level, _ in self.flashes()), path)
            self.assertEqual(before, self.state())

    def test_record_migration_preserves_ps1_rows_and_checks(self):
        from app.models import ProductionControlRecord, ProductionPreservationRecord
        tables = [ProductionControlRecord.__table__, ProductionPreservationRecord.__table__]
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_production_records.py'))
        spec = importlib.util.spec_from_file_location('ps2_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('a6c8e0f2b4d6', migration.down_revision)
        with self.app.app_context():
            db.metadata.drop_all(db.engine, tables=tables)
            with db.engine.begin() as connection:
                legacy = inspect(connection).get_table_names()
                def snapshot():
                    return [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all())
                            for name in sorted(legacy)]
                before = snapshot()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(before, snapshot())
                    for table, data in zip(tables, (CONTROL, PRESERVATION)):
                        info = inspect(connection)
                        self.assertTrue(info.get_pk_constraint(table.name)['name'])
                        indexes = [i['column_names'] for i in info.get_indexes(table.name)]
                        self.assertIn(['order_id', 'date', 'id'], indexes)
                        for fk in info.get_foreign_keys(table.name):
                            self.assertTrue(fk['name'])
                            self.assertIn(fk['constrained_columns'], indexes)
                            if fk['referred_table'] != 'users':
                                self.assertEqual('RESTRICT', fk['options']['ondelete'])
                                self.assertEqual('RESTRICT', fk['options']['onupdate'])
                        connection.execute(table.insert().values(**data))
                        for changes in ({'result': 'released'}, {'description': ''},
                                        {'kind': 'unknown'} if 'kind' in data else {'aspect': 'unknown'},
                                        {'result': 'nonconforming'} if 'kind' in data else {'date': None}):
                            with self.assertRaises(IntegrityError):
                                with connection.begin_nested():
                                    connection.execute(table.update().values(**changes))
                    migration.downgrade()
                    self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                    self.assertEqual(before, snapshot())


class RecordMcp(McpDbCase):
    async def test_record_real_mcp_roles_scopes_filters_immutable_rejected_hashes(self):
        from app.models import ProductionOrder, ProductionState
        self.seed('personas')
        self.seed('production_orders')
        db.session.execute(ProductionOrder.__table__.update().values(state=ProductionState.in_progress))
        db.session.commit()
        for slug, data in (('production_control_records', CONTROL),
                            ('production_preservation_records', PRESERVATION)):
            data = data | {'date': data['date'].isoformat()}
            for role in RoleEnum:
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_create', {'module': slug, 'data': data})
                self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
                if result.is_error:
                    self.assertEqual(before, hashes())
                    continue
                rid = result.structured_content['id']
                for reader in RoleEnum:
                    result = await self.call(mcp_actor(reader), 'qms_get', {'module': slug, 'id': rid})
                    self.assertFalse(result.is_error, result.content)
                    result = await self.call(mcp_actor(reader), 'qms_list', {'module': slug,
                        'filters': {k: v for k, v in data.items() if k in ('order_id', 'kind', 'aspect', 'result')}})
                    self.assertFalse(result.is_error, result.content)
                    self.assertGreaterEqual(result.structured_content['total'], 1)
                for who, tool, args in ((mcp_actor(), 'qms_update', {'id': rid, 'data': {}}),
                    (mcp_actor(scopes=('read',)), 'qms_create', {'data': data}),
                    (mcp_actor(scopes=('write',)), 'qms_get', {'id': rid}),
                    (mcp_actor(), 'qms_create', {'data': data | {'id': 1}})):
                    before = hashes()
                    result = await self.call(who, tool, {'module': slug} | args)
                    self.assertTrue(result.is_error)
                    self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})
