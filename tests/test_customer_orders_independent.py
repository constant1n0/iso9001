"""Independent T1 probes: S2 register, S3 acceptance, S4 immutable revisions.

List screens ignore filter values that name nothing (repository convention),
so the verifier's invalid-filter probe is intentionally not included.
"""
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from flask import g
from test_customer_orders import OrderCase, TODAY, REVIEW
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people

class IndependentOrders(OrderCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        g.pop('_login_user', None)

    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_s2_kinds_filters_and_code_boundary(self):
        for n, kind in enumerate(('order', 'contract', 'accepted_offer')):
            row = self.create(code=str(n) * 50, kind=kind)
            self.assertEqual([row.id], [r.id for r in self.service.orders.list_(
                db.session, self.who, kind=kind, status='pending', customer_id=1)])
        self.review(self.latest(row))
        for status in ('accepted', 'pending', 'rejected', 'cancelled'):
            self.assertEqual([row.id] if status != 'pending' else [row.id, 2, 1],
                [r.id for r in self.service.orders.list_(db.session, self.who, status=status)])
            if status == 'accepted':
                self.revision(row)
            elif status == 'pending':
                self.review(self.latest(row), result='rejected', check_b='nonconforming')
            elif status == 'rejected':
                self.service.cancel(db.session, self.who, row.id, reason='Customer withdrew')
                db.session.commit()
        self.refuse(lambda: self.create(code='X' * 51),
                    'El campo «code» admite como máximo 50 caracteres.')
        self.refuse(lambda: self.create(received_date=TODAY + timedelta(days=1)),
                    'La fecha de recepción no puede ser futura.')

    def test_s3_each_check_and_differences_gate_exact_message(self):
        revision = self.latest(self.create())
        message = ('Para aceptar, todas las comprobaciones deben cumplir o no '
                   'aplicar y las diferencias estar resueltas.')
        for values in [{f'check_{c}': 'nonconforming'} for c in 'abcde'] + [
                {'differences_resolved': False}]:
            self.refuse(lambda: self.review(revision, **values), message)
        self.refuse(lambda: self.review(revision, undocumented=True),
                    'Registra fecha no futura y medio de confirmación del cliente sin requisitos documentados.')
        self.refuse(lambda: self.review(revision, confirmation_means='Email'),
                    'La confirmación solo corresponde a requisitos no documentados.')
        self.review(revision, **{f'check_{c}': 'not_applicable' for c in 'abcde'})
        self.assertEqual('accepted', self.service.orders.get(db.session, self.who, 1).status.name)

    def test_s3_web_clock_and_inactive_reviewer(self):
        self.create()
        self.login()
        path = '/pedidos/1/revisiones/1/revisar'
        form = self.form(path)
        for key, value in (REVIEW | {'differences_resolved': 'true', 'undocumented': 'false'}).items():
            form[key] = value
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        self.assertEqual(200, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())
        people.update(db.session, self.who, 1, {'activo': True})
        db.session.commit()
        with patch('app.routes.customer_order_routes.local_today', return_value=TODAY):
            self.assertEqual(302, self.client.post(path, data=form).status_code)
        revision = self.service.latest(db.session, 1)
        self.assertEqual((1, TODAY), (revision.reviewer_id, revision.review_date))

    def test_s3_cited_web_deletes(self):
        self.review(self.latest(self.create()))
        self.login()
        for path in ('/clientes/1/eliminar', '/personas/1/eliminar'):
            before = hashes()
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertEqual(before, hashes())
            self.assertIn('No se puede eliminar', repr(self.flashes()))

    def test_s3_s4_all_post_boundaries_and_noop(self):
        order = self.create()
        self.login()
        paths = ['/pedidos/nuevo', '/pedidos/1/editar',
                 '/pedidos/1/revisiones/1/editar', '/pedidos/1/revisiones/nueva',
                 '/pedidos/1/revisiones/1/revisar', '/pedidos/1/cancelar', '/pedidos/1/eliminar']
        forms = {path: self.form(path) for path in paths[:3] + [paths[4]]}
        forms[paths[3]] = forms[paths[2]].copy()
        forms[paths[5]], forms[paths[6]] = {'reason': 'Stop'}, {}
        for path in paths:
            for payload in ({'json': {'status': 'accepted'}},
                            {'data': dict(forms[path]) | {'reviewer_id': '1'}},
                            {'data': dict(forms[path]) | {'requirements': (BytesIO(b'x'), 'x.txt')}}):
                before = hashes()
                self.assertEqual(400, self.client.post(path, **payload).status_code, path)
                self.assertEqual(before, hashes())
        for path in paths[1:3]:
            form, before = self.form(path), hashes()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
        first = self.latest(order)
        self.review(first)
        self.revision(order)
        self.assertEqual('pending', self.service.orders.get(db.session, self.who, 1).status.name)
        self.assertEqual([2, 1], [r.number for r in self.service.revisions.list_(db.session, self.who)])
        self.refuse(lambda: self.service.revisions.update(db.session, self.who, first.id,
                    {'requirements': 'Rewritten'}), self.service.READ_ONLY)
        self.refuse(lambda: self.service.orders.delete(db.session, self.who, 1),
                    'Solo puedes eliminar un pedido con una única revisión sin revisar.')


class IndependentOrderMcp(McpDbCase):
    async def test_s3_s4_every_decision_field_refused_on_both_modules(self):
        order = self.seed('customer_orders')
        for module in ('customer_orders', 'customer_order_revisions'):
            for key in ('status', 'cancelled', 'result', 'reviewer_id', 'review_date',
                        'number', 'check_a', 'differences_resolved', 'undocumented'):
                before = hashes()
                result = await self.call(mcp_actor(), 'qms_update', {
                    'module': module, 'id': order, 'data': {key: 'accepted'}})
                self.assertTrue(result.is_error)
                self.assertEqual('Error executing tool qms_update: Campos no permitidos: '
                                 + key + '.', result.content[0].text)
                self.assertEqual(before, hashes())
        self.prepare_customer_revision()
        before = hashes()
        result = await self.call(mcp_actor(), 'qms_update', {
            'module': 'customer_order_revisions', 'id': 1, 'data': {'requirements': 'Rewrite'}})
        self.assertTrue(result.is_error)
        self.assertEqual('Error executing tool qms_update: Solo puedes editar la última '
                         'revisión sin revisar de un pedido no cancelado.', result.content[0].text)
        self.assertEqual(before, hashes())
