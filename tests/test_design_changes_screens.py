"""DD4 browser and MCP contracts, including refused dropped multipart fields."""
import io

from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import BrowserForm, hashes
from test_design import actor, TODAY
from test_design_changes import CHANGE
from app.extensions import db
from app.models import RoleEnum
from app.services import design, design_io, people
from app.services.actor import Actor

BASE = '/diseno/1'


class DesignChangeScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.seed_with(people, {'nombre': 'Approver',
                              'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        self.login(RoleEnum.ADMINISTRADOR)
        self.client.post('/diseno/nuevo', data=dict(title='Design', description='Product',
                                                   owner_id=1, starter_stages='y'))
        self.client.post(BASE + '/salidas/nuevo', data=dict(
            kind='specification', text='Drawing <A>', acceptance_criteria='Works'))

    def state(self):
        with self.app.app_context():
            return hashes()

    def propose(self):
        self.client.post(BASE + '/salidas/1/aprobar')
        return self.client.post(BASE + '/cambios/nuevo', data=CHANGE | {'output_ids': '1'})

    def test_approval_history_changes_completion_and_readonly_rendering(self):
        self.assertIn('Aprobar', self.client.get(BASE).text)
        self.assertEqual(302, self.propose().status_code)
        page = self.client.get(BASE).text
        for text in ('Cambios del diseño', 'Versión 1', 'aprobada', 'New requirement', 'Autorizar', 'Rechazar'):
            self.assertIn(text, page)
        self.assertNotIn('/salidas/1/editar', page)
        self.assertEqual(302, self.client.post(BASE + '/cambios/1/autorizar').status_code)
        self.assertEqual(302, self.client.post(BASE + '/cambios/1/aplicar').status_code)
        page = self.client.get(BASE).text
        self.assertIn('Historial de versiones', page)
        self.assertIn('Versión 2', page)
        self.assertIn('Drawing &lt;A&gt;', page)
        self.assertIn('/salidas/2/editar', page)
        self.client.post(BASE + '/transicion', data={'target': 'planificado'})
        self.client.post(BASE + '/transicion', data={'target': 'en_curso'})
        page = self.client.get(BASE).text
        self.assertIn('Completar proyecto', page)
        self.assertIn('Aprueba todas las versiones actuales', page)
        self.client.post(BASE + '/salidas/2/aprobar')
        self.assertEqual(302, self.client.post(BASE + '/completar').status_code)
        page = self.client.get(BASE).text
        self.assertIn('completado', page)
        self.assertIn('Aprobación final:', page)
        for text in ('/salidas/nuevo', '/cambios/nuevo', '/etapas/nueva', '/completar'):
            self.assertNotIn('action="' + BASE + text + '"', page)
            self.assertNotIn('href="' + BASE + text + '"', page)

    def test_rendered_proposal_and_draft_noop_roundtrips(self):
        self.client.post(BASE + '/salidas/1/aprobar')
        form = BrowserForm()
        response = self.client.get(BASE + '/cambios/nuevo')
        self.assertEqual(200, response.status_code)
        form.feed(response.text)
        self.assertIn('name="output_ids"', response.text)
        self.assertEqual([], form.values.getlist('output_ids'))
        self.client.post(BASE + '/cambios/nuevo', data=CHANGE | {'output_ids': '1'})
        form = BrowserForm()
        response = self.client.get(BASE + '/cambios/1/editar')
        form.feed(response.text)
        for key, value in CHANGE.items():
            self.assertEqual([value], form.values.getlist(key))
        self.assertEqual(['1'], form.values.getlist('output_ids'))
        before = self.state()
        self.assertEqual(302, self.client.post(BASE + '/cambios/1/editar', data=form.values).status_code)
        self.assertEqual(before, self.state())

    def test_unknown_foreign_duplicate_and_file_fields_refused_without_writes(self):
        self.propose()
        from werkzeug.datastructures import MultiDict
        paths = [('/salidas/1/aprobar', {}), ('/cambios/nuevo', CHANGE | {'output_ids': '1'}),
                 ('/cambios/1/editar', CHANGE | {'output_ids': '1'}),
                 ('/cambios/1/autorizar', {}), ('/cambios/1/rechazar', {'reason': 'No'}),
                 ('/cambios/1/aplicar', {}), ('/completar', {})]
        for suffix, values in paths:
            for extra in ({'project_id': '2'}, {'state': 'authorized'},
                          {'attachment': (io.BytesIO(b'evidence'), 'evidence.txt')}):
                before = self.state()
                response = self.client.post(BASE + suffix, data=values | extra)
                self.assertEqual(400, response.status_code, suffix)
                self.assertEqual(before, self.state())
            data = MultiDict(values)
            data.add('project_id', '1')
            data.add('project_id', '1')
            before = self.state()
            self.assertEqual(400, self.client.post(BASE + suffix, data=data).status_code)
            self.assertEqual(before, self.state())

    def test_multiple_affected_outputs_and_known_named_files_are_not_dropped(self):
        self.client.post(BASE + '/salidas/nuevo', data=dict(
            kind='software', text='Code', acceptance_criteria='Tests pass'))
        for oid in (1, 2):
            self.client.post(BASE + f'/salidas/{oid}/aprobar')
        response = self.client.post(BASE + '/cambios/nuevo',
            data=CHANGE | {'output_ids': ['1', '2']})
        self.assertEqual(302, response.status_code)
        form = BrowserForm()
        form.feed(self.client.get(BASE + '/cambios/1/editar').text)
        self.assertEqual(['1', '2'], form.values.getlist('output_ids'))
        before = self.state()
        self.assertEqual(302, self.client.post(BASE + '/cambios/1/editar', data=form.values).status_code)
        self.assertEqual(before, self.state())
        for suffix, data, key in (('/cambios/nuevo', CHANGE | {'output_ids': ['1', '2']}, 'review'),
                                   ('/salidas/nuevo', dict(kind='other', text='Output', acceptance_criteria='Works'), 'text'),
                                   ('/etapas/nueva', dict(name='Step', owner_id='1'), 'name'),
                                   ('/editar', dict(title='Design', description='Product', owner_id='1'), 'title')):
            data[key] = (io.BytesIO(b'not a text control'), 'evidence.txt')
            before = self.state()
            self.assertEqual(400, self.client.post(BASE + suffix, data=data).status_code)
            self.assertEqual(before, self.state())

    def test_action_forms_csrf_and_nonadministrator_visibility(self):
        self.propose()
        for role in (RoleEnum.AUDITOR, RoleEnum.OPERATIVO):
            self.login(role)
            page = self.client.get(BASE).text
            for label in ('>Aprobar<', '>Autorizar<', '>Rechazar<'):
                self.assertNotIn(label, page)
            for suffix in ('/salidas/1/aprobar', '/cambios/1/autorizar',
                           '/cambios/1/rechazar', '/completar'):
                before = self.state()
                self.client.post(BASE + suffix, data={'reason': 'No'})
                self.assertEqual(before, self.state())
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for suffix, values in (('/salidas/1/aprobar', {}),
                               ('/cambios/nuevo', CHANGE | {'output_ids': '1'}),
                               ('/cambios/1/autorizar', {}), ('/cambios/1/rechazar', {'reason': 'No'}),
                               ('/cambios/1/aplicar', {}), ('/completar', {})):
            before = self.state()
            self.assertEqual(400, self.client.post(BASE + suffix, data=values).status_code)
            self.assertEqual(before, self.state())


class DesignChangeMcp(McpDbCase):
    async def test_propose_read_only_actions_and_output_approval_metadata(self):
        from app.services import design_changes
        self.seed('design_projects')
        oid = self.seed('design_outputs')
        # Link a real administrator for the web-only approval.
        from app.models import User
        user = User(username='approver', password='unused', role=RoleEnum.ADMINISTRADOR)
        db.session.add(user)
        db.session.commit()
        people.create(db.session, self.cli(), {'nombre': 'Approver', 'user_id': user.id})
        admin = Actor(user.id, 'Admin', RoleEnum.ADMINISTRADOR, 'web')
        design_changes.approve_output(db.session, admin, oid, today=TODAY)
        db.session.commit()
        read = await self.call(mcp_actor(), 'qms_get', {'module': 'design_outputs', 'id': oid})
        self.assertEqual((1, 'aprobada'), (read.structured_content['version'],
                                         read.structured_content['approval_state']))
        for data in ({'text': 'Overwrite'}, {}, {'approval_state': 'draft'}):
            before = hashes()
            refused = await self.call(mcp_actor(), 'qms_update', {
                'module': 'design_outputs', 'id': oid, 'data': data})
            self.assertTrue(refused.is_error)
            self.assertEqual(before, hashes())
        payload = CHANGE | {'project_id': 1, 'output_ids': [oid]}
        for role in RoleEnum:
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {
                'module': 'design_changes', 'data': payload})
            if role is RoleEnum.OPERATIVO:
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
                continue
            self.assertFalse(result.is_error, result.content)
            row = result.structured_content
            self.assertEqual(('propuesto', [oid]), (row['state'], row['output_ids']))
            for data in ({}, {'review': 'Edit'}, {'state': 'authorized'},
                         {'authorized_by_id': 2}, {'apply': True}):
                before = hashes()
                refused = await self.call(mcp_actor(role), 'qms_update', {
                    'module': 'design_changes', 'id': row['id'], 'data': data})
                self.assertTrue(refused.is_error)
                self.assertEqual(before, hashes())
            listed = await self.call(mcp_actor(role), 'qms_list', {
                'module': 'design_changes', 'filters': {'project_id': 1, 'state': 'proposed'}})
            self.assertIn(row['id'], [item['id'] for item in listed.structured_content['items']])
        for module, data in (('design_projects', {'state': 'completado'}),
                             ('design_changes', payload | {'state': 'authorized'})):
            before = hashes()
            name = 'qms_update' if module == 'design_projects' else 'qms_create'
            args = {'module': module, 'data': data}
            if name == 'qms_update':
                args['id'] = 1
            self.assertTrue((await self.call(mcp_actor(), name, args)).is_error)
            self.assertEqual(before, hashes())
