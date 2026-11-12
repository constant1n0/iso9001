"""DD2 rendered forms, revision privacy and actual MCP tools (S3, S5)."""
import json
from mcp_support import McpDbCase, mcp_actor
from register_routes import RegisterRoutesBase
import test_design_screens as screens

FORM = screens.FORM
from test_design_io import INPUT, OUTPUT, INPUT_TYPES, OUTPUT_TYPES, approved_revision
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import design
from test_design import actor


class DesignIOScreens(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        from app.services import people
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)

    state = screens.DesignScreens.state
    browser = screens.DesignScreens.browser

    def project(self):
        self.assertEqual(302, self.client.post('/diseno/nuevo', data=FORM).status_code)

    def test_every_type_and_revision_select_preselected_rendered_noop(self):
        self.project()
        with self.app.app_context():
            _, rid = approved_revision(actor(), publish=True)
        for section, values, types in (('entradas', INPUT, INPUT_TYPES),
                                        ('salidas', OUTPUT, OUTPUT_TYPES)):
            for item_id, kind in enumerate(types, 1):
                data = values | {'kind': kind, 'revision_id': str(rid),
                                 'text': '\nText <A> & B\n'}
                if section == 'salidas':
                    data['acceptance_criteria'] = '\nLimit <1 & >0\n'
                self.assertEqual(302, self.client.post(
                    f'/diseno/1/{section}/nuevo', data=data).status_code)
                path = f'/diseno/1/{section}/{item_id}/editar'
                form = self.browser(path)
                for key, value in data.items():
                    self.assertEqual([value], form.getlist(key), (section, kind, key))
                before = self.state()
                self.assertEqual(302, self.client.post(path, data=form).status_code)
                self.assertEqual(before, self.state())
        page = self.client.get('/diseno/1').text
        self.assertIn(FORM['title'], page)
        self.assertIn('Etapas ordenadas', page)
        filtered = self.client.get('/diseno/1?salidas_tipo=software').text
        self.assertIn('<h3>software</h3>', filtered)
        self.assertNotIn('<h3>especificación</h3>', filtered)
        self.assertIn('Entradas del diseño', page)
        self.assertIn('Salidas del diseño', page)
        self.assertIn('Criterios de aceptación', page)
        self.assertIn('SECRET-42', page)

    def test_web_role_matrix_rejections_csrf_and_parent_mismatch(self):
        self.project()
        for section, data in (('entradas', INPUT), ('salidas', OUTPUT)):
            for role in RoleEnum:
                self.login(role)
                before = self.state()
                response = self.client.post(f'/diseno/1/{section}/nuevo', data=data)
                self.assertEqual(302, response.status_code)
                if role is RoleEnum.OPERATIVO:
                    self.assertEqual(before, self.state())
                    for suffix in ('editar', 'eliminar'):
                        before = self.state()
                        self.assertEqual(302, self.client.post(
                            f'/diseno/1/{section}/1/{suffix}', data=data).status_code)
                        self.assertEqual(before, self.state())
                else:
                    response = self.client.post(f'/diseno/1/{section}/1/editar',
                                                data=data | {'text': role.name})
                    self.assertEqual(302, response.status_code)
            self.login(RoleEnum.ADMINISTRADOR)
            before = self.state()
            self.assertEqual(200, self.client.post(f'/diseno/1/{section}/1/editar',
                data=data | {'kind': 'invalid', 'text': 'Rejected'}).status_code)
            self.assertEqual(before, self.state())
            self.assertEqual(404, self.client.post(
                f'/diseno/2/{section}/1/eliminar').status_code)
            self.assertEqual(before, self.state())
            self.app.config['WTF_CSRF_ENABLED'] = True
            for suffix in ('nuevo', '1/editar', '1/eliminar'):
                self.assertEqual(400, self.client.post(
                    f'/diseno/1/{section}/{suffix}', data=data).status_code)
                self.assertEqual(before, self.state())
            self.app.config['WTF_CSRF_ENABLED'] = False
            self.login(RoleEnum.AUDITOR)
            self.assertEqual(302, self.client.post(
                f'/diseno/1/{section}/1/eliminar').status_code)

    def test_restricted_revision_placeholder_retained_obsolete_and_cancelled(self):
        self.project()
        with self.app.app_context():
            doc_id, rid = approved_revision(actor())
        for section, data in (('entradas', INPUT), ('salidas', OUTPUT)):
            self.assertEqual(302, self.client.post(f'/diseno/1/{section}/nuevo',
                data=data | {'revision_id': rid}).status_code)
        revision_page = self.client.get(f'/diseno/revisiones/{rid}')
        self.assertEqual(200, revision_page.status_code)
        self.assertIn('Restricted revision content', revision_page.text)
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(404, self.client.get(f'/diseno/revisiones/{rid}').status_code)
        page = self.client.get('/diseno/1').text
        self.assertIn('Revisión vinculada', page)
        for hidden in ('SECRET-42', 'Restricted title', 'Restricted revision content',
                       f'href="/documents/{doc_id}', '/entradas/nuevo', '/salidas/nuevo'):
            self.assertNotIn(hidden, page)
        self.login(RoleEnum.ADMINISTRADOR)
        with self.app.app_context():
            from app.services import document_revisions
            from test_design import TODAY
            document_revisions.publish(db.session, actor(), rid, today=TODAY)
            document_revisions.withdraw(db.session, actor(), doc_id, 'Stopped', today=TODAY)
            db.session.commit()
        for section in ('entradas', 'salidas'):
            path = f'/diseno/1/{section}/1/editar'
            form = self.browser(path)
            self.assertEqual([str(rid)], form.getlist('revision_id'))
            before = self.state()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, self.state())
        self.client.post('/diseno/1/transicion', data={
            'target': 'cancelado', 'cancellation_reason': 'Stopped'})
        for section, data in (('entradas', INPUT), ('salidas', OUTPUT)):
            for suffix, payload in (('nuevo', data), ('1/editar', data | {'text': 'Changed'}),
                                     ('1/eliminar', {})):
                before = self.state()
                response = self.client.post(f'/diseno/1/{section}/{suffix}', data=payload)
                self.assertIn(response.status_code, (200, 302))
                self.assertEqual(before, self.state())
        page = self.client.get('/diseno/1').text
        self.assertNotIn('/entradas/nuevo', page)
        self.assertNotIn('/salidas/1/editar', page)


class DesignIOMcp(McpDbCase):
    async def test_actual_tools_role_matrix_filters_and_revision_privacy(self):
        self.seed('design_projects')
        _, rid = approved_revision(self.cli())
        tools = {t.name for t in await self.list_tools()}
        self.assertNotIn('qms_delete', tools)
        for name, data, types in (('design_inputs', INPUT, INPUT_TYPES),
                                  ('design_outputs', OUTPUT, OUTPUT_TYPES)):
            for role in RoleEnum:
                before = hashes()
                result = await self.call(mcp_actor(role), 'qms_create', {
                    'module': name, 'data': data | {'project_id': 1, 'revision_id': rid}})
                if role is RoleEnum.OPERATIVO:
                    self.assertTrue(result.is_error)
                    self.assertEqual(before, hashes())
                    continue
                self.assertFalse(result.is_error, result.content)
                item_id = result.structured_content['id']
                result = await self.call(mcp_actor(role), 'qms_update', {
                    'module': name, 'id': item_id, 'data': {'text': 'Edited'}})
                self.assertFalse(result.is_error, result.content)
                for reader in RoleEnum:
                    result = await self.call(mcp_actor(reader), 'qms_get',
                                              {'module': name, 'id': item_id})
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(rid, result.structured_content['revision_id'])
                    # Match knowledge MCP: column/link ids only, not related metadata.
                    serialized = json.dumps(result.structured_content)
                    for hidden in ('SECRET-42', 'Restricted title', 'Restricted revision content'):
                        self.assertNotIn(hidden, serialized)
                for kind in types:
                    result = await self.call(mcp_actor(role), 'qms_create', {
                        'module': name, 'data': data | {'kind': kind, 'project_id': 1}})
                    self.assertFalse(result.is_error, result.content)
                    result = await self.call(mcp_actor(role), 'qms_list', {
                        'module': name, 'filters': {'project_id': 1, 'kind': kind}})
                    self.assertGreaterEqual(result.structured_content['total'], 1)
                    self.assertTrue(all(row['kind'] == types[kind]
                                        for row in result.structured_content['items']))
            # Write-only scopes maintain ordinary fields but cannot make a new
            # citation without the document read grant. Existing citations stay.
            result = await self.call(mcp_actor(scopes=('write',)), 'qms_update', {
                'module': name, 'id': item_id, 'data': {'text': 'Write-only edit'}})
            self.assertFalse(result.is_error, result.content)
            before = hashes()
            result = await self.call(mcp_actor(scopes=('write',)), 'qms_create', {
                'module': name, 'data': data | {'project_id': 1, 'revision_id': rid}})
            self.assertTrue(result.is_error)
            self.assertEqual(before, hashes())
            for who, payload in ((mcp_actor(RoleEnum.OPERATIVO), {'text': 'Denied'}),
                                 (mcp_actor(), {'kind': 'invalid'}),
                                 (mcp_actor(), {'revision_id': 99999}),
                                 (mcp_actor(scopes=('read',)), {'text': 'Denied'})):
                before = hashes()
                result = await self.call(who, 'qms_update', {
                    'module': name, 'id': item_id, 'data': payload})
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
        design.transition(db.session, actor(), 1, 'cancelado', cancellation_reason='Stopped')
        db.session.commit()
        for name, data in (('design_inputs', INPUT), ('design_outputs', OUTPUT)):
            for tool, arguments in (('qms_create', {'data': data | {'project_id': 1}}),
                                     ('qms_update', {'id': 1, 'data': {'text': 'Changed'}})):
                before = hashes()
                result = await self.call(mcp_actor(), tool, {'module': name} | arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
