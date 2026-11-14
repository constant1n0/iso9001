"""Independent PS5 probes: S4 post-delivery and S3 non-release."""
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from app.extensions import db
from app.models import RoleEnum
from app.services import audit, people
from app.services.errors import PermissionDenied
from test_design import actor
from test_production_post_delivery import PostDeliveryCase, TODAY, RESULT
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor


class IndependentPostDelivery(PostDeliveryCase):
    def form(self, path):
        parser = BrowserForm()
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_s4_every_post_payload_boundary_with_real_csrf(self):
        row = self.activity()
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        base = '/produccion/posventa/'
        edit = base + f'{row.id}/editar'
        form = self.form(edit)
        token = form['csrf_token']
        actions = [('nueva', form), (f'{row.id}/editar', form),
            (f'{row.id}/realizar', MultiDict(dict(result_date=TODAY.isoformat(), result='OK', evidence='R'))),
            (f'{row.id}/anular', MultiDict(dict(result_date=TODAY.isoformat(), reason='Stop'))),
            (f'{row.id}/eliminar', MultiDict())]
        for suffix, valid in actions:
            valid = valid.copy()
            valid['csrf_token'] = token
            bad = valid.copy()
            bad.add(next(k for k in valid), 'different')
            cases = [dict(json={'id': 999}, headers={'X-CSRFToken': token}), dict(data=bad)]
            for key in valid:
                dropped = valid.copy()
                dropped.pop(key)
                cases.append(dict(data=dropped))
            for key in ('foreign_id', 'activity_id', 'status', 'project_id'):
                cases.append(dict(data=valid.to_dict() | {key: '999'}))
            cases.append(dict(data=valid.to_dict() | {'evidence': (BytesIO(b'x'), 'x.txt')}))
            for values in cases:
                with self.subTest(route=suffix, payload=values):
                    before = hashes()
                    self.assertEqual(400, self.client.post(base + suffix, **values).status_code)
                    self.assertEqual(before, hashes())

    def test_s4_browser_noops_all_kinds_retained_links(self):
        order = self.create()
        self.login(RoleEnum.ADMINISTRADOR)
        for kind in ('warranty', 'maintenance', 'technical_support', 'recovery', 'complementary', 'other'):
            row = self.activity(kind=kind, description='Revisión <A> & B\nSegunda línea',
                                source='Contrato\nAnexo', order_ids=[order.id])
            path = f'/produccion/posventa/{row.id}/editar'
            form = self.form(path)
            self.assertEqual(kind, form['kind'])
            before = hashes()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
        people.update(db.session, self.who, 1, {'activo': False})
        self.service.transition(db.session, self.who, order.id, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        form = self.form(path)
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())
        form.setlist('order_ids', ['', str(order.id)])
        response = self.client.post(path, data=form)
        self.assertEqual(400, response.status_code)
        self.assertIn('Elige «Sin órdenes» o una o varias órdenes, no ambas cosas.', response.text)
        self.assertEqual(before, hashes())

    def test_s3_s4_terminals_and_realistic_date_boundary(self):
        order = self.create()
        snapshot = audit.snapshot(order)
        for target, values in [('completed', RESULT), ('cancelled', {'result_date': TODAY, 'reason': 'Stop'})]:
            row = self.activity(order_ids=[order.id])
            self.reject(lambda: self.finish(row, target, **(values | {'result_date': TODAY + timedelta(days=1)})),
                        'Elige realizada o anulada con fecha no futura.')
            self.finish(row, target, **values)
            self.reject(lambda: self.change(row), self.post.READ_ONLY)
            self.reject(lambda: self.post.activities.update(db.session, actor(channel='mcp'), row.id, {}, today=TODAY), self.post.READ_ONLY)
            self.reject(lambda: self.post.activities.delete(db.session, self.who, row.id), self.post.READ_ONLY)
            self.reject(lambda: self.finish(row, target, **values), self.post.READ_ONLY)
            self.assertEqual(snapshot, audit.snapshot(order))
        self.login(RoleEnum.OPERATIVO)
        for suffix in ('nueva', '1/editar', '1/realizar', '1/anular', '1/eliminar'):
            before = hashes()
            self.assertEqual(302, self.client.post('/produccion/posventa/' + suffix).status_code)
            self.assertEqual(before, hashes())
        self.reject(lambda: self.post.activities.create(db.session, actor(RoleEnum.OPERATIVO), {}, today=TODAY), error=PermissionDenied)


class IndependentPostDeliveryMcp(McpDbCase):
    async def test_s4_exact_overdue_sets_roles_and_terminal_history(self):
        from app.services import production_post_delivery as service
        self.seed('personas')
        ids = [self.seed('post_delivery_activities', description=f'Visit {n}',
                        due_date=(TODAY + timedelta(days=delta)).isoformat())
               for n, delta in enumerate((-1, 0, 1, -1, -1))]
        for rid, target in zip(ids[3:], ('completed', 'cancelled')):
            service.transition(db.session, actor(), rid, target, today=TODAY,
                **(RESULT if target == 'completed' else {'result_date': TODAY, 'reason': 'Stop'}))
        db.session.commit()
        with patch('app.mcp_server.registry.local_today', return_value=TODAY):
            for role in RoleEnum:
                for rid in ids:
                    result = await self.call(mcp_actor(role), 'qms_get', {'module': 'post_delivery_activities', 'id': rid})
                    self.assertFalse(result.is_error, result.content)
                    self.assertIs(rid == ids[0], result.structured_content['overdue'])
                for overdue, expected in ((True, ids[:1]), (False, ids[1:])):
                    result = await self.call(mcp_actor(role), 'qms_list',
                        {'module': 'post_delivery_activities', 'filters': {'overdue': overdue}})
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(sorted(expected), sorted(r['id'] for r in result.structured_content['items']))
        before = hashes()
        for rid, data in [(ids[0], {'source': 'Forbidden'}), (ids[0], {'status': 'completed'}), (ids[3], {})]:
            result = await self.call(mcp_actor(RoleEnum.OPERATIVO if 'source' in data else RoleEnum.ADMINISTRADOR),
                'qms_update', {'module': 'post_delivery_activities', 'id': rid, 'data': data})
            self.assertTrue(result.is_error)
            self.assertEqual(before, hashes())
        case = IndependentPostDelivery()
        case.app = self.app
        case.client = self.app.test_client()
        from app.models import User
        user = User(username='browser-admin', password='unused', role=RoleEnum.ADMINISTRADOR)
        db.session.add(user)
        db.session.commit()
        case.ids = {RoleEnum.ADMINISTRADOR: user.id}
        case.login(RoleEnum.ADMINISTRADOR)
        with patch('app.routes.production_routes.local_today', return_value=TODAY):
            text = case.client.get('/produccion/posventa/?overdue=true').text
        self.assertIn('Visit 0', text)
        for n in range(1, 5):
            self.assertNotIn(f'Visit {n}', text)
