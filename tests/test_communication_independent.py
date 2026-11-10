"""Independent CM1 probes: S1-S5; run with project root and tests on PYTHONPATH."""
import hashlib
import importlib.util
from datetime import date, timedelta
from itertools import product
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, select
from alembic.migration import MigrationContext
from alembic.operations import Operations
from register_routes import RegisterRoutesBase
from html.parser import HTMLParser
from werkzeug.datastructures import MultiDict
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people, clientes, providers, stakeholders


class BrowserForm(HTMLParser):
    """Successful text, select, textarea and checkbox controls from a rendered form."""
    def __init__(self):
        super().__init__()
        self.values = MultiDict()
        self.select = None
        self.options = []
        self.option = None
        self.textarea = None
        self.text = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.select = attrs
            self.options = []
        elif tag == 'option' and self.select:
            self.option = attrs
            self.options.append(attrs)
        elif tag == 'textarea':
            self.textarea = attrs['name']
            self.text = ''
        elif tag == 'input' and attrs.get('name'):
            kind = attrs.get('type', 'text')
            if kind not in ('submit', 'button', 'reset') and (
                    kind not in ('checkbox', 'radio') or 'checked' in attrs):
                self.values.add(attrs['name'], attrs.get('value', ''))

    def handle_data(self, text):
        if self.textarea:
            self.text += text

    def handle_endtag(self, tag):
        if tag == 'select':
            selected = [o for o in self.options if 'selected' in o]
            if not selected and 'multiple' not in self.select and self.options:
                selected = self.options[:1]
            for option in selected:
                self.values.add(self.select['name'], option.get('value', ''))
            self.select = None
        elif tag == 'textarea':
            # HTML ignores the first newline immediately after a textarea start tag.
            value = self.text.replace('\r\n', '\n').replace('\r', '\n').removeprefix('\n')
            self.values.add(self.textarea, value)
            self.textarea = None

FORM = dict(title='Resultados de calidad', content='Explicar los resultados del mes',
            scope='interna', channel='reunion', owner_id='1', frequency='Mensual',
            next_date='2026-12-01', audience_text='Todo el personal')
TARGETS = (('stakeholder_ids', stakeholders, {'nombre': 'Party'}),
           ('customer_ids', clientes, {'nombre': 'Customer'}),
           ('provider_ids', providers.providers, {'nombre': 'Provider', 'tipo_suministro': 'producto'}),
           ('person_ids', people, {'nombre': 'Recipient'}))


def hashes():
    """Byte hashes of complete SQLite dump and audit rows, not merely row counts."""
    connection = db.session.connection().connection.driver_connection
    dump = '\n'.join(connection.iterdump()).encode()
    audit = repr(connection.execute('SELECT * FROM audit_logs ORDER BY id').fetchall()).encode()
    return hashlib.sha256(dump).hexdigest(), hashlib.sha256(audit).hexdigest()


class IndependentWeb(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        with self.app.app_context():
            return hashes()

    def browser(self, id=1):
        response = self.client.get(f'/comunicaciones/{id}/editar')
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_s1_s5_populated_additive_migration_preserves_all_legacy_rows(self):
        from app.models import CommunicationPlan, COMMUNICATION_ASSOCIATIONS
        from app import __file__ as app_file
        ids = {key: self.seed_with(service, data) for key, service, data in TARGETS}
        path = Path(app_file).resolve().parent.parent / 'migrations/versions/a0c2e4f6b8d0_add_communication_plan.py'
        spec = importlib.util.spec_from_file_location('cm1_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('f9b1d3e5a7c9', migration.down_revision)
        with self.app.app_context():
            tables = [CommunicationPlan.__table__, *COMMUNICATION_ASSOCIATIONS.values()]
            db.metadata.drop_all(db.engine, tables=tables)
            with db.engine.begin() as connection:
                legacy = inspect(connection).get_table_names()
                def legacy_hash():
                    rows = [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').fetchall())
                            for name in sorted(legacy)]
                    return hashlib.sha256(repr(rows).encode()).hexdigest()
                before = legacy_hash()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(before, legacy_hash())
                    for table in tables:
                        info = inspect(connection)
                        self.assertTrue(info.get_pk_constraint(table.name)['name'])
                        for fk in info.get_foreign_keys(table.name):
                            self.assertTrue(fk['name'])
                            if fk['constrained_columns'] not in (['created_by_id'], ['updated_by_id']):
                                self.assertEqual('RESTRICT', fk['options']['ondelete'])
                                self.assertEqual('RESTRICT', fk['options']['onupdate'])
                                indexed = [i['column_names'] for i in info.get_indexes(table.name)]
                                indexed += [info.get_pk_constraint(table.name)['constrained_columns']]
                                self.assertTrue(any(cols[:1] == fk['constrained_columns'] for cols in indexed))
                    connection.execute(CommunicationPlan.__table__.insert().values(
                        **(FORM | {'owner_id': 1, 'next_date': date(2026, 12, 1)})))
                    for key, table in COMMUNICATION_ASSOCIATIONS.items():
                        connection.execute(table.insert().values(communication_id=1, target_id=ids[key]))
                    migration.downgrade()
                    self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                    self.assertEqual(before, legacy_hash())

    def test_s1_s2_s3_every_enum_and_text_browser_noop(self):
        for n, (scope, channel) in enumerate(product(
                ('interna', 'externa', 'ambas'),
                ('correo', 'reunion', 'telefono', 'tablon_intranet', 'web', 'otro')), 1):
            values = FORM | dict(scope=scope, channel=channel, content='\nLínea <uno> & dos\n',
                                 channel_detail='Sala & tablón', audience_text='Equipo <A>',
                                 frequency='Al cambiar el proceso')
            self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=values).status_code)
            browser = self.browser(n)
            for key, value in values.items():
                self.assertEqual([value], browser.getlist(key), key)
            before = self.state()
            self.assertEqual(302, self.client.post(f'/comunicaciones/{n}/editar', data=browser).status_code)
            self.assertEqual(before, self.state())

    def test_s3_web_each_audience_set_replace_clear_and_reject(self):
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM).status_code)
        for key, service, data in TARGETS:
            first = self.seed_with(service, data)
            second = self.seed_with(service, data | {'nombre': data['nombre'] + ' 2'})
            for values in ([first], [first, second], [second], []):
                form = self.browser()
                form.setlist(key, list(map(str, values)))
                form['audience_text'] = '' if values else 'Restored recipients'
                self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=form).status_code)
                self.assertEqual(list(map(str, values)), self.browser().getlist(key))
            for bad in ('999', 'not-an-id'):
                before = self.state()
                form = self.browser()
                form.setlist(key, [bad])
                response = self.client.post('/comunicaciones/1/editar', data=form)
                self.assertEqual(200, response.status_code)
                self.assertEqual(before, self.state())

    def test_s2_s5_rejections_and_cited_deletes_preserve_hashes_after_retirement(self):
        form = FORM | {'retired': 'y'}
        cases = []
        for key, service, data in TARGETS:
            id = self.seed_with(service, data)
            form[key] = [str(id)]
            url = {'stakeholder_ids': f'/partes_interesadas/eliminar/{id}',
                   'customer_ids': f'/clientes/{id}/eliminar',
                   'provider_ids': f'/proveedores/{id}/eliminar',
                   'person_ids': f'/personas/{id}/eliminar'}[key]
            cases.append(url)
        with self.app.app_context():
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=form).status_code)
        for url in cases + ['/personas/1/eliminar']:
            before = self.state()
            response = self.client.post(url)
            self.assertEqual(302, response.status_code)
            self.assertTrue(any(level == 'danger' for level, _ in self.flashes()))
            self.assertEqual(before, self.state())
        for key, bad in (('title', ''), ('title', 'x' * 201), ('scope', 'other'),
                         ('channel', 'email'), ('owner_id', '0'), ('next_date', '2026-02-30')):
            before = self.state()
            response = self.client.post('/comunicaciones/1/editar', data=form | {key: bad})
            self.assertEqual(200, response.status_code)
            self.assertEqual(before, self.state())
        self.login(RoleEnum.OPERATIVO)
        for url in ('/comunicaciones/nuevo', '/comunicaciones/1/editar', '/comunicaciones/1/eliminar'):
            before = self.state()
            self.assertEqual(302, self.client.post(url, data=FORM).status_code)
            self.assertEqual(before, self.state())
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for url in ('/comunicaciones/nuevo', '/comunicaciones/1/editar', '/comunicaciones/1/eliminar'):
            before = self.state()
            self.assertEqual(400, self.client.post(url, data=FORM).status_code)
            self.assertEqual(before, self.state())


class IndependentMcp(McpDbCase):
    async def test_s1_s2_s3_example_and_each_audience_set_replace_omit_clear(self):
        self.seed('personas')
        # Exact documented qms_create example (docs/mcp.md).
        result = await self.call(mcp_actor(RoleEnum.AUDITOR), 'qms_create',
                                 {'module': 'communication', 'data': FORM | {'owner_id': 1}})
        self.assertFalse(result.is_error, result.content)
        id = result.structured_content['id']
        for key, slug in (('stakeholder_ids', 'partes_interesadas'), ('customer_ids', 'clientes'),
                          ('provider_ids', 'proveedores'), ('person_ids', 'personas')):
            first = self.seed(slug, nombre=key + ' 1')
            second = self.seed(slug, nombre=key + ' 2')
            for values in ([first, first], [first, second], [second], []):
                result = await self.call(mcp_actor(), 'qms_update', {'module': 'communication',
                                        'id': id, 'data': {key: values, 'audience_text': None if values else 'Restored recipients'}})
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(sorted(set(values)), result.structured_content[key])
                self.assertEqual(None if values else 'Restored recipients', result.structured_content['audience_text'])
                before = hashes()
                noop = await self.call(mcp_actor(), 'qms_update',
                                      {'module': 'communication', 'id': id, 'data': {}})
                self.assertFalse(noop.is_error, noop.content)
                self.assertEqual(before, hashes())
                self.assertEqual(sorted(set(values)), noop.structured_content[key])
            for bad, message in ((None, f'El campo «{key}» debe ser una lista de identificadores.'),
                                 ([True], f'El campo «{key}» debe ser una lista de identificadores.'),
                                 ([0, -1], f'El campo «{key}» contiene identificadores desconocidos: -1, 0.')):
                before = hashes()
                result = await self.call(mcp_actor(), 'qms_update', {'module': 'communication',
                                         'id': id, 'data': {key: bad, 'title': 'Must not persist'}})
                self.assertTrue(result.is_error)
                self.assertEqual('Error executing tool qms_update: ' + message, result.content[0].text)
                self.assertEqual(before, hashes())

    async def test_s4_s5_date_boundaries_roles_scopes_and_retirement(self):
        from app.audit_notifications import local_today
        today = local_today()
        ids = []
        for offset, retired in ((-1, False), (0, False), (1, False), (-1, True)):
            ids.append(self.seed('communication', next_date=(today + timedelta(days=offset)).isoformat(),
                                 retired=retired))
        for id, expected in zip(ids, (True, False, False, False)):
            for role in RoleEnum:
                result = await self.call(mcp_actor(role), 'qms_get', {'module': 'communication', 'id': id})
                self.assertFalse(result.is_error)
                self.assertIs(expected, result.structured_content['overdue'])
        for filters, expected in (({}, 3), ({'overdue': True}, 1),
                                  ({'overdue': False}, 2), ({'retired': True}, 1)):
            result = await self.call(mcp_actor(), 'qms_list', {'module': 'communication', 'filters': filters})
            self.assertEqual(expected, result.structured_content['total'])
        for actor in (mcp_actor(RoleEnum.OPERATIVO), mcp_actor(scopes=('read',))):
            for tool, args in (('qms_create', {'data': FORM | {'owner_id': 1}}),
                               ('qms_update', {'id': ids[0], 'data': {'retired': True}})):
                before = hashes()
                result = await self.call(actor, tool, {'module': 'communication'} | args)
                self.assertTrue(result.is_error)
                self.assertEqual(f'Error executing tool {tool}: No tienes permiso para realizar esta acción.',
                                 result.content[0].text)
                self.assertEqual(before, hashes())
        for tool in ('qms_get', 'qms_list'):
            result = await self.call(mcp_actor(scopes=('write',)), tool,
                                     {'module': 'communication'} | ({'id': ids[0]} if tool == 'qms_get' else {}))
            self.assertTrue(result.is_error)
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})


if __name__ == '__main__':
    unittest.main()
