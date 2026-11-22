"""Rendered forms, strict payloads and MCP draft-only surface."""
from io import BytesIO
from test_customer_orders import OrderCase, ORDER, REVIEW, TODAY
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum


class OrderScreens(OrderCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        from flask import g
        g.pop('_login_user', None)

    def test_register_available(self):
        self.login()
        self.assertEqual(200, self.client.get('/pedidos/').status_code)

    def test_roundtrips_strict_fields_and_nonform_refusals(self):
        row = self.create()
        self.login()
        paths = [f'/pedidos/{row.id}/editar', f'/pedidos/{row.id}/revisiones/1/editar']
        for path in paths:
            parser = BrowserForm()
            parser.feed(self.client.get(path).get_data(as_text=True))
            before, form = hashes(), parser.values
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
            for key in form:
                if key == 'csrf_token':
                    continue
                bad = form.copy()
                bad.pop(key)
                self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
                self.assertEqual(before, hashes())
            for data in ({'id': '2'}, {'order_id': '2'}, {'result': 'accepted'},
                         {'requirements': (BytesIO(b'x'), 'x.txt')}):
                self.assertEqual(400, self.client.post(path, data=dict(form) | data).status_code)
                self.assertEqual(before, hashes())
        for path in paths + ['/pedidos/nuevo', '/pedidos/1/eliminar', '/pedidos/1/cancelar',
                             '/pedidos/1/revisiones/nueva', '/pedidos/1/revisiones/1/revisar']:
            before = hashes()
            self.assertEqual(400, self.client.post(path, json={'id': 2}).status_code)
            self.assertEqual(before, hashes())

    def test_web_review_roles_foreign_revision_filters_and_csrf(self):
        row = self.create()
        other = self.create(code='C-2', kind='contract')
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(200, self.client.get('/pedidos/1').status_code)
        before = hashes()
        self.assertEqual(302, self.client.post('/pedidos/1/editar', data={}).status_code)
        self.assertEqual(before, hashes())
        self.login(RoleEnum.AUDITOR)
        self.client.post('/pedidos/1/revisiones/1/revisar', data=REVIEW)
        self.assertEqual(before, hashes())
        self.login()
        self.assertEqual(404, self.client.get('/pedidos/2/revisiones/1/editar').status_code)
        text = self.client.get('/pedidos/?kind=contract').get_data(as_text=True)
        self.assertIn(other.code, text)
        self.assertNotIn('>C-1<', text)
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(400, self.client.post('/pedidos/1/eliminar').status_code)
        self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = False
        form = REVIEW | {'differences_resolved': 'true', 'undocumented': 'false',
            'confirmation_date': '', 'confirmation_means': '', **{f'note_{c}': '' for c in 'abcde'}}
        self.assertEqual(302, self.client.post('/pedidos/1/revisiones/1/revisar', data=form).status_code)
        text = self.client.get('/pedidos/1').get_data(as_text=True)
        self.assertIn('comprometido', text)
        self.assertIn('Reviewer', text)
        self.assertEqual(302, self.client.get('/pedidos/1/revisiones/1/editar').status_code)
        self.assertEqual(405, self.client.get('/pedidos/1/eliminar').status_code)


class OrderMcp(McpDbCase):
    async def test_modules_and_draft_only_rules(self):
        result = await self.call(mcp_actor(), 'qms_modules')
        modules = {m['slug']: m for m in result.structured_content['modules']}
        self.assertIn('customer_orders', modules)
        self.assertIn('customer_order_revisions', modules)
        self.seed('clientes')
        values = ORDER | {'received_date': TODAY.isoformat()}
        for role in RoleEnum:
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'customer_orders',
                'data': values | {'code': role.name}})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                self.assertEqual(before, hashes())
                continue
            rid = result.structured_content['id']
            revisions = await self.call(mcp_actor(role), 'qms_list', {
                'module': 'customer_order_revisions', 'filters': {'order_id': rid}})
            cid = revisions.structured_content['items'][0]['id']
            for slug, record_id in (('customer_orders', rid), ('customer_order_revisions', cid)):
                result = await self.call(mcp_actor(role), 'qms_update', {'module': slug,
                    'id': record_id, 'data': {'requirements': 'Edited'} if slug.endswith('revisions') else {}})
                self.assertFalse(result.is_error, result.content)
                for forbidden in ('result', 'reviewer_id', 'cancelled', 'status'):
                    before = hashes()
                    denied = await self.call(mcp_actor(role), 'qms_update', {'module': slug,
                        'id': record_id, 'data': {forbidden: 'accepted'}})
                    self.assertTrue(denied.is_error)
                    self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})
