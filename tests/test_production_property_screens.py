"""PS4 forms, MCP boundary and additive SQLite migration probes."""
import importlib.util
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from test_production_property import PropertyCase, ITEM, INCIDENT, TODAY
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum


class PropertyScreens(PropertyCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        from flask import g
        g.pop('_login_user', None)

    def test_forms_noop_dropped_foreign_uploaded_nonform_and_filters(self):
        row = self.item(order_ids=[self.create().id])
        self.login()
        path = f'/produccion/propiedad/{row.id}/editar'
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        form = parser.values
        self.assertEqual(['1'], form.getlist('order_ids'))
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())
        for key in form:
            if key == 'csrf_token':
                continue
            bad = form.copy()
            bad.pop(key)
            self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
            self.assertEqual(before, hashes())
        for data in ({'id': '2'}, {'item_id': '2'}, {'status': 'returned'},
                     {'description': (BytesIO(b'x'), 'x.txt')}):
            self.assertEqual(400, self.client.post(path, data=dict(form) | data).status_code)
            self.assertEqual(before, hashes())
        for suffix in ('editar', 'transicion', 'eliminar', 'incidencias/nueva'):
            self.assertEqual(400, self.client.post(f'/produccion/propiedad/1/{suffix}', json={'id': 2}).status_code)
            self.assertEqual(before, hashes())
        self.assertEqual(200, self.client.get('/produccion/propiedad/?status=bogus&owner_type=bogus&customer_id=999&provider_id=no').status_code)
        self.assertEqual(200, self.client.get('/produccion/propiedad/nueva').status_code)
        self.assertEqual(404, self.client.post('/produccion/propiedad/1/incidencias/1/editar').status_code)
        self.assertEqual(404, self.client.post('/produccion/propiedad/1/incidencias/1/eliminar').status_code)

    def test_web_roles_csrf_terminal_and_incident_forms(self):
        self.item()
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/produccion/propiedad/1').status_code)
            self.assertEqual(302 if role is RoleEnum.OPERATIVO else 200,
                self.client.get('/produccion/propiedad/nueva').status_code)
            if role is RoleEnum.OPERATIVO:
                before = hashes()
                for suffix in ('editar', 'transicion', 'eliminar', 'incidencias/nueva'):
                    self.client.post('/produccion/propiedad/1/' + suffix)
                    self.assertEqual(before, hashes())
        self.login()
        self.app.config['WTF_CSRF_ENABLED'] = True
        before = hashes()
        self.assertEqual(400, self.client.post('/produccion/propiedad/1/transicion').status_code)
        self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = False
        form = BrowserForm()
        form.feed(self.client.get('/produccion/propiedad/1/incidencias/nueva').get_data(as_text=True))
        data = form.values
        for key, value in INCIDENT.items():
            if key != 'item_id':
                data[key] = str(value).lower() if isinstance(value, bool) else value.isoformat() if hasattr(value, 'isoformat') else str(value)
        self.assertEqual(302, self.client.post('/produccion/propiedad/1/incidencias/nueva', data=data).status_code)
        self.assertIn('Scratch', self.client.get('/produccion/propiedad/1').get_data(as_text=True))
        self.assertEqual(302, self.client.post('/produccion/propiedad/1/transicion', data={
            'target': 'returned', 'terminal_date': '2020-01-02', 'terminal_note': 'Returned'}).status_code)
        self.assertEqual(302, self.client.get('/produccion/propiedad/1/editar').status_code)

    def test_migration_preserves_ps1_ps2_ps3_rows_constraints_and_reverse(self):
        from app.models import CustomerProperty, PropertyIncident, property_orders
        from app.services import production_changes, production_records
        self.create()
        production_changes.changes.create(db.session, self.who,
            dict(reason='Why', description='What', actions='Act', order_ids=[1]))
        self.service.transition(db.session, self.who, 1, 'in_progress')
        production_records.preservation.create(db.session, self.who, dict(order_id=1,
            date=TODAY, responsible_id=1, aspect='storage', description='Dry', result='conforming'), today=TODAY)
        db.session.commit()
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_production_property.py'))
        spec = importlib.util.spec_from_file_location('ps4', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('c8e0f2a4b6d8', migration.down_revision)
        tables = [PropertyIncident.__table__, property_orders, CustomerProperty.__table__]
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
                connection.execute(CustomerProperty.__table__.insert().values(**ITEM))
                for values in ({'customer_id': None}, {'provider_id': 1}, {'status': 'released'},
                               {'status': 'returned'}, {'terminal_note': 'Extra'}):
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(CustomerProperty.__table__.update().values(**values))
                migration.downgrade()
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())


class PropertyMcp(McpDbCase):
    async def test_roles_roundtrip_incident_immutable_and_no_transitions_or_delete(self):
        self.seed('clientes')
        self.seed('personas')
        for role in RoleEnum:
            data = {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in ITEM.items()}
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'customer_property', 'data': data})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                self.assertEqual(before, hashes())
                continue
            rid = result.structured_content['id']
            result = await self.call(mcp_actor(role), 'qms_update', {'module': 'customer_property', 'id': rid, 'data': {'location': 'Store'}})
            self.assertFalse(result.is_error, result.content)
            incident = {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in INCIDENT.items()} | {'item_id': rid}
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'property_incidents', 'data': incident})
            self.assertFalse(result.is_error, result.content)
            for slug, record_id, values in (('customer_property', rid, {'status': 'returned'}),
                                            ('property_incidents', result.structured_content['id'], {})):
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_update', {'module': slug, 'id': record_id, 'data': values})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
                for reader in RoleEnum:
                    self.assertFalse((await self.call(mcp_actor(reader), 'qms_get', {'module': slug, 'id': record_id})).is_error)
                    self.assertFalse((await self.call(mcp_actor(reader), 'qms_list', {'module': slug})).is_error)
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})
