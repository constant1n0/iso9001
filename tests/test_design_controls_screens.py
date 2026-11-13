"""DD3 browser and MCP probes through rendered forms and public tools."""
from itertools import product

from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import BrowserForm, hashes
from test_design import actor, TODAY
from app.extensions import db
from app.models import RoleEnum
from app.services import design, people

PLAN = dict(kind='validation', description='Trial <A> & B', responsible_id='1',
            stage_id='1', planned_date='2026-12-01')
RECORD = dict(kind='validation', date='2020-01-01', responsible_id='1',
              result='conforming_with_observations', evidence='Report <A> & B',
              actions='Monitor', stage_id='1', planned_control_id='1')
BASE = '/diseno/1/controles'


class ControlScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)
        self.client.post('/diseno/nuevo', data=dict(title='Design', description='Product',
                                                   owner_id=1, starter_stages='y'))
        self.client.post('/diseno/nuevo', data=dict(title='Other', description='Product', owner_id=1))

    def state(self):
        with self.app.app_context():
            return hashes()

    def browser(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        form = BrowserForm()
        form.feed(response.text)
        return form.values

    def plan(self):
        response = self.client.post(BASE + '/planes/nuevo', data=PLAN)
        self.assertEqual(302, response.status_code)
        self.assertEqual('/diseno/1', response.location)

    def planned(self):
        self.client.post('/diseno/1/transicion', data={'target': 'planificado'})

    def test_rendered_selects_and_noop_roundtrip(self):
        form = self.browser(BASE + '/planes/nuevo')
        self.assertEqual(['review'], form.getlist('kind'))
        self.assertEqual([''], form.getlist('stage_id'))
        self.assertEqual([''], form.getlist('responsible_id'))
        self.plan()
        path = BASE + '/planes/1/editar'
        form = self.browser(path)
        for key, value in PLAN.items():
            self.assertEqual([value], form.getlist(key))
        before = self.state()
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, self.state())
        self.planned()
        form = self.browser(BASE + '/registro/nuevo')
        for key, value in (('kind', 'review'), ('result', 'conforming'),
                           ('planned_control_id', ''), ('stage_id', '')):
            self.assertEqual([value], form.getlist(key))

    def test_records_history_resolution_withdrawal_and_no_mutation_routes(self):
        self.plan()
        self.planned()
        response = self.client.post(BASE + '/registro/nuevo', data=RECORD)
        self.assertEqual(302, response.status_code)
        page = self.client.get('/diseno/1').text
        for text in ('Controles del diseño', 'Resuelto', 'Report &lt;A&gt; &amp; B',
                     'conforme con observaciones', 'Monitor'):
            self.assertIn(text, page)
        for suffix in ('editar', 'eliminar'):
            before = self.state()
            self.assertEqual(404, self.client.post(BASE + '/registro/1/' + suffix,
                                                  data=RECORD).status_code)
            self.assertEqual(before, self.state())
        self.plan()
        before = self.state()
        self.assertEqual(200, self.client.post(BASE + '/planes/2/retirar',
                                              data={'withdrawal_reason': ' '}).status_code)
        self.assertEqual(before, self.state())
        self.assertEqual(302, self.client.post(BASE + '/planes/2/retirar',
            data={'withdrawal_reason': 'Not required'}).status_code)
        page = self.client.get('/diseno/1').text
        self.assertIn('Retirado', page)
        self.assertIn('Not required', page)
        self.assertEqual(405, self.client.get(BASE + '/planes/2/retirar').status_code)
        self.assertEqual(404, self.client.post(BASE + '/planes/2/eliminar').status_code)

    def test_dropped_explicit_fields_and_foreign_parent_are_refused_atomically(self):
        self.plan()
        self.planned()
        paths = ((BASE + '/planes/nuevo', PLAN),
                 (BASE + '/planes/1/editar', PLAN),
                 (BASE + '/registro/nuevo', RECORD),
                 (BASE + '/planes/1/retirar', {'withdrawal_reason': 'Stop'}))
        for path, data in paths:
            for extra in ({'project_id': '2'}, {'result': 'nonconforming'}
                          if '/planes/' in path else {'withdrawn': 'true'}):
                with self.subTest(path=path, extra=extra):
                    self.client.get('/diseno/1')
                    before = self.state()
                    response = self.client.post(path, data=data | extra)
                    self.assertEqual(400, response.status_code)
                    self.assertEqual(before, self.state())
                    self.assertFalse(any(level == 'success' for level, _ in self.flashes()))

    def test_web_permissions_csrf_cancelled_and_deletion_messages(self):
        self.plan()
        self.planned()
        for role in RoleEnum:
            self.login(role)
            response = self.client.get('/diseno/1')
            self.assertEqual(200, response.status_code)
            if role is RoleEnum.OPERATIVO:
                self.assertNotIn('href="' + BASE + '/registro/nuevo"', response.text)
                for path, data in ((BASE + '/planes/nuevo', PLAN),
                                   (BASE + '/planes/1/editar', PLAN),
                                   (BASE + '/planes/1/retirar', {'withdrawal_reason': 'Stop'}),
                                   (BASE + '/registro/nuevo', RECORD)):
                    before = self.state()
                    self.assertEqual(302, self.client.post(path, data=data).status_code)
                    self.assertEqual(before, self.state())
            else:
                self.assertEqual(302, self.client.post(BASE + '/registro/nuevo', data=RECORD).status_code)
        self.login(RoleEnum.ADMINISTRADOR)
        for path, message in (('/diseno/1/etapas/1/eliminar',
                              'No se puede eliminar una etapa citada por controles del diseño.'),
                             ('/personas/1/eliminar', people.STILL_REFERENCED)):
            before = self.state()
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertIn(('danger', message), self.flashes())
            self.assertEqual(before, self.state())
        self.app.config['WTF_CSRF_ENABLED'] = True
        for path, data in ((BASE + '/planes/nuevo', PLAN),
                           (BASE + '/planes/1/editar', PLAN),
                           (BASE + '/planes/1/retirar', {'withdrawal_reason': 'Stop'}),
                           (BASE + '/registro/nuevo', RECORD)):
            before = self.state()
            self.assertEqual(400, self.client.post(path, data=data).status_code)
            self.assertEqual(before, self.state())
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client.post('/diseno/1/transicion', data={'target': 'cancelado', 'cancellation_reason': 'Stop'})
        before = self.state()
        self.client.post(BASE + '/registro/nuevo', data=RECORD)
        self.client.post(BASE + '/planes/nuevo', data=PLAN)
        self.assertEqual(before, self.state())
        self.assertNotIn('href="' + BASE + '/registro/nuevo"', self.client.get('/diseno/1').text)


class ControlMcp(McpDbCase):
    async def test_tools_permissions_filters_resolution_and_immutable_evidence(self):
        self.seed('design_projects', starter_stages=True)
        design.transition(db.session, actor(), 1, 'planificado')
        db.session.commit()
        plan_data = dict(project_id=1, kind='review', description='Review', responsible_id=1)
        record_data = dict(project_id=1, kind='review', date='2020-01-01',
                           responsible_id=1, result='conforming', evidence='Minutes')
        for role in RoleEnum:
            for module, data in (('design_planned_controls', plan_data),
                                 ('design_control_records', record_data)):
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_create', {'module': module, 'data': data})
                if role is RoleEnum.OPERATIVO:
                    self.assertTrue(result.is_error)
                    self.assertEqual(before, hashes())
                    continue
                self.assertFalse(result.is_error, result.content)
                row = result.structured_content
                for reader in RoleEnum:
                    read = await self.call(mcp_actor(reader), 'qms_get', {'module': module, 'id': row['id']})
                    self.assertFalse(read.is_error)
                filters = {'project_id': 1, 'kind': 'review'}
                if module == 'design_control_records':
                    filters['result'] = 'conforming'
                    before = hashes()
                    refused = await self.call(mcp_actor(role), 'qms_update', {
                        'module': module, 'id': row['id'], 'data': {}})
                    self.assertTrue(refused.is_error)
                    self.assertIn('no se modifican', refused.content[0].text)
                    self.assertEqual(before, hashes())
                else:
                    self.assertFalse(row['resolved'])
                    withdrawn = await self.call(mcp_actor(role), 'qms_update', {
                        'module': module, 'id': row['id'], 'data': {
                            'withdrawn': True, 'withdrawal_reason': 'Not applicable'}})
                    self.assertFalse(withdrawn.is_error, withdrawn.content)
                    self.assertTrue(withdrawn.structured_content['resolved'])
                listed = await self.call(mcp_actor(role), 'qms_list', {'module': module, 'filters': filters})
                self.assertIn(row['id'], [r['id'] for r in listed.structured_content['items']])
        for module, data in (('design_planned_controls', plan_data),
                             ('design_control_records', record_data)):
            before = hashes()
            refused = await self.call(mcp_actor(scopes=('read',)), 'qms_create',
                                      {'module': module, 'data': data})
            self.assertTrue(refused.is_error)
            self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})
