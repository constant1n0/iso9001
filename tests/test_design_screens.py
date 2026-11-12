"""DD1 rendered forms, CSRF, navigation, protected references and MCP tools."""
from itertools import product

from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import BrowserForm, hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import people, clientes, process_operations

FORM = dict(title='New design', description='Product <A> & service', owner_id='1',
            planned_start='2026-01-01', planned_end='2026-12-31')


class DesignScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        with self.app.app_context():
            return hashes()

    def browser(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        form = BrowserForm()
        form.feed(response.get_data(as_text=True))
        return form.values

    def test_rendered_project_and_stage_noop_roundtrip(self):
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        processes = [self.seed_with(process_operations, {'proceso': f'P{n}'}) for n in (1, 2)]
        values = FORM | {'customer_id': str(customer), 'process_ids': list(map(str, processes))}
        self.assertEqual(302, self.client.post('/diseno/nuevo', data=values).status_code)
        form = self.browser('/diseno/1/editar')
        for key, value in values.items():
            self.assertEqual(value if isinstance(value, list) else [value], form.getlist(key))
        before = self.state()
        self.assertEqual(302, self.client.post('/diseno/1/editar', data=form).status_code)
        self.assertEqual(before, self.state())
        stage = dict(name='Prototype', owner_id='1', planned_date='2026-12-01')
        self.assertEqual(302, self.client.post('/diseno/1/etapas/nueva', data=stage).status_code)
        form = self.browser('/diseno/1/etapas/1/editar')
        for key, value in stage.items():
            self.assertEqual([value], form.getlist(key))
        before = self.state()
        self.assertEqual(302, self.client.post('/diseno/1/etapas/1/editar', data=form).status_code)
        self.assertEqual(before, self.state())

    def test_web_lifecycle_roles_and_csrf(self):
        self.assertEqual(302, self.client.post('/diseno/nuevo', data=FORM | {'starter_stages': 'y'}).status_code)
        for role in RoleEnum:
            self.login(role)
            page = self.client.get('/diseno/1')
            self.assertEqual(200, page.status_code)
            self.assertIn('href="/diseno/"', page.get_data(as_text=True))
            self.assertIn('href="/cambios/"', page.get_data(as_text=True))
            if role is RoleEnum.OPERATIVO:
                self.assertNotIn('action="/diseno/1/transicion"', page.get_data(as_text=True))
                for path in ('/diseno/nuevo', '/diseno/1/editar', '/diseno/1/transicion', '/diseno/1/eliminar'):
                    before = self.state()
                    self.assertEqual(302, self.client.post(path, data=FORM | {'target': 'planificado'}).status_code)
                    self.assertEqual(before, self.state())
        self.login(RoleEnum.AUDITOR)
        for target in ('planificado', 'en_curso', 'completado'):
            before = self.state()
            self.assertEqual(302, self.client.post('/diseno/1/transicion', data={'target': target}).status_code)
            if target == 'completado':
                self.assertEqual(before, self.state())
        self.assertEqual(405, self.client.get('/diseno/1/transicion').status_code)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for path in ('/diseno/1/transicion', '/diseno/1/editar', '/diseno/1/etapas/nueva',
                     '/diseno/1/etapas/1/eliminar', '/diseno/1/eliminar'):
            before = self.state()
            self.assertEqual(400, self.client.post(path, data={'target': 'cancelado'}).status_code)
            self.assertEqual(before, self.state())

    def test_people_customer_process_delete_refused_not_500(self):
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        process = self.seed_with(process_operations, {'proceso': 'Linked'})
        stage_owner = self.seed_with(people, {'nombre': 'Stage owner'})
        with self.app.app_context():
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        self.assertEqual(302, self.client.post('/diseno/nuevo', data=FORM | {
            'customer_id': customer, 'process_ids': [process]}).status_code)
        self.assertEqual(302, self.client.post('/diseno/1/etapas/nueva', data={
            'name': 'Stage', 'owner_id': stage_owner}).status_code)
        for path in ('/personas/1/eliminar', f'/personas/{stage_owner}/eliminar',
                     f'/clientes/{customer}/eliminar', f'/procesos/{process}/eliminar'):
            before = self.state()
            response = self.client.post(path)
            self.assertEqual(302, response.status_code, path)
            self.assertTrue(any(level == 'danger' for level, _ in self.flashes()), path)
            self.assertEqual(before, self.state())
        # Existing people behavior remains: uncited people can still be deleted.
        unused = self.seed_with(people, {'nombre': 'Unused'})
        self.assertEqual(302, self.client.post(f'/personas/{unused}/eliminar').status_code)

    def test_stage_parent_mismatch_is_not_found(self):
        for _ in (1, 2):
            self.assertEqual(302, self.client.post('/diseno/nuevo', data=FORM).status_code)
        self.assertEqual(302, self.client.post('/diseno/1/etapas/nueva', data={
            'name': 'Stage', 'owner_id': 1}).status_code)
        before = self.state()
        self.assertEqual(404, self.client.post('/diseno/2/etapas/1/eliminar').status_code)
        self.assertEqual(before, self.state())


class DesignMcp(McpDbCase):
    async def test_mcp_fields_roles_no_transition_or_delete(self):
        self.seed('personas')
        tools = {tool.name for tool in await self.list_tools()}
        self.assertNotIn('qms_delete', tools)
        self.assertNotIn('qms_transition', tools)
        for role in RoleEnum:
            result = await self.call(mcp_actor(role), 'qms_create', {
                'module': 'design_projects', 'data': {k: v for k, v in FORM.items()} | {'owner_id': 1}})
            if role is RoleEnum.OPERATIVO:
                self.assertTrue(result.is_error)
                continue
            self.assertFalse(result.is_error, result.content)
            project_id = result.structured_content['id']
            for data in ({'state': 'completado'}, {'state': 'cancelado'}, {'target': 'planificado'}):
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_update', {
                    'module': 'design_projects', 'id': project_id, 'data': data})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'design_stages',
                'data': {'project_id': project_id, 'name': 'Stage', 'owner_id': 1}})
            self.assertFalse(result.is_error, result.content)
            for reader in RoleEnum:
                result = await self.call(mcp_actor(reader), 'qms_get', {'module': 'design_projects', 'id': project_id})
                self.assertFalse(result.is_error, result.content)
                self.assertEqual('borrador', result.structured_content['state'])
                result = await self.call(mcp_actor(reader), 'qms_list', {'module': 'design_stages',
                    'filters': {'project_id': project_id}})
                self.assertEqual(1, result.structured_content['total'])
