"""Independent T1 probes: S1, S2 and S3 register; no decision implementation."""
from io import BytesIO
from unittest.mock import patch
from sqlalchemy import event
from werkzeug.datastructures import MultiDict
from flask import g
from test_product_release import ReleaseCase, DECISION
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import ProductRelease, ProductionOrder, RoleEnum
from app.services import product_release as service
from app.services.errors import Conflict


class IndependentRelease(ReleaseCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        g.pop('_login_user', None)

    def test_s2_lock_precedes_duplicate_query_and_refreshes_order(self):
        order = self.finished(kind='service', customer_id=1, lot='LOT-8')
        seen = []
        def capture(state):
            if state.is_select:
                seen.append((str(state.statement),
                    state.statement._for_update_arg is not None,
                    state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            row = self.release(order)
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        lock = next(i for i, (sql, locked, fresh) in enumerate(seen)
                    if 'FROM production_orders' in sql and locked and fresh)
        duplicate = next(i for i, (sql, _, _) in enumerate(seen)
                         if 'FROM product_releases' in sql)
        self.assertLess(lock, duplicate)
        self.login()
        text = self.client.get(f'/liberacion/{row.id}').get_data(as_text=True)
        for value in ('LOT-8', 'Customer', order.code):
            self.assertIn(value, text)
        self.reject(lambda: self.release(order), service.SPEC.conflict, Conflict)
        self.decided(row, 'rejected')
        self.release(order)
        other = self.finished()
        for state in ('planned', 'in_progress', 'cancelled'):
            db.session.execute(ProductionOrder.__table__.update().where(
                ProductionOrder.id == other.id).values(state=state,
                cancellation_reason='Stop' if state == 'cancelled' else None)
                .execution_options(synchronize_session=False))
            db.session.commit()
            self.reject(lambda: self.release(other), 'Elige una orden terminada.')

    def test_s1_s3_every_post_refuses_discarded_fields_and_csrf(self):
        row = self.release(notes='Keep <&>')
        check = self.verification(row, evidence='Report <&>')
        self.finished()
        self.login()
        paths = ['/liberacion/nueva', f'/liberacion/{row.id}/editar',
                 f'/liberacion/{row.id}/verificaciones/nueva',
                 f'/liberacion/{row.id}/verificaciones/{check.id}/editar']
        forms = []
        for path in paths:
            parser = BrowserForm()
            parser.feed(self.client.get(path).get_data(as_text=True))
            forms.append(parser.values)
        forms[0]['order_id'] = '2'
        paths += [f'/liberacion/{row.id}/eliminar',
                  f'/liberacion/{row.id}/verificaciones/{check.id}/eliminar']
        forms += [MultiDict(), MultiDict()]
        for path, form in zip(paths, forms):
            before = hashes()
            for field in ('status', 'decision_date', 'decided_by_id', 'decided_by_user_id',
                          'decision_note', 'concession', 'concession_reason',
                          'concession_approver_id', 'customer_approval_date',
                          'customer_approval_means', 'unknown', 'release_id', 'project_id'):
                bad = form.copy()
                bad[field] = '1'
                self.assertEqual(400, self.client.post(path, data=bad).status_code, (path, field))
                self.assertEqual(before, hashes())
            for field in form:
                bad = form.copy()
                bad.add(field, 'unwanted second value')
                self.assertEqual(400, self.client.post(path, data=bad).status_code)
                bad = form.copy()
                bad.pop(field)
                self.assertEqual(400, self.client.post(path, data=bad).status_code)
            bad = form.copy()
            bad['notes'] = (BytesIO(b'file'), 'evidence.txt')
            self.assertEqual(400, self.client.post(path, data=bad).status_code)
            self.assertEqual(400, self.client.post(path, json={'notes': 'ignored'}).status_code)
            self.assertEqual(before, hashes())
            self.app.config['WTF_CSRF_ENABLED'] = True
            self.assertEqual(400, self.client.post(path, data=form).status_code)
            self.app.config['WTF_CSRF_ENABLED'] = False
            self.assertEqual(before, hashes())
        for path, form in zip(paths[1:4:2], forms[1:4:2]):
            before = hashes()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
        self.login(RoleEnum.OPERATIVO)
        for path in ('/liberacion/', f'/liberacion/{row.id}', '/produccion/1'):
            text = self.client.get(path).get_data(as_text=True)
            self.assertNotIn('Nueva liberación', text)
            self.assertNotIn('Editar borrador', text)
            self.assertNotIn('Añadir verificación', text)
            self.assertNotIn('/liberacion/1/eliminar', text)

    def test_s2_s3_cited_deletes_have_messages_and_preserve_bytes(self):
        from app.services import people
        row = self.release()
        self.verification(row)
        self.login()
        for path, message in (('/personas/1/eliminar', people.STILL_REFERENCED),
                ('/produccion/1/eliminar', 'Solo puedes eliminar órdenes planificadas.')):
            before = hashes()
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertIn(('danger', message), self.flashes())
            self.assertEqual(before, hashes())

    def test_s3_warm_parent_change_refuses_child_create(self):
        row = self.release()
        original = service._lock
        def decide(session, record_id):
            session.execute(ProductRelease.__table__.update().where(
                ProductRelease.id == record_id).values(status='released', **DECISION)
                .execution_options(synchronize_session=False))
            return original(session, record_id)
        with patch.object(service, '_lock', side_effect=decide):
            self.reject(lambda: self.verification(row), service.READ_ONLY)


class IndependentReleaseMcp(McpDbCase):
    async def test_s3_decision_fields_refused_on_create_and_update(self):
        rid = self.seed('product_releases')
        for tool in ('qms_create', 'qms_update'):
            for key in ('status', *DECISION, 'concession_reason', 'concession_approver_id',
                        'customer_approval_date', 'customer_approval_means'):
                before = hashes()
                args = {'module': 'product_releases', 'data': {'order_id': 1, key: 'x'}}
                if tool == 'qms_update':
                    args['id'] = rid
                result = await self.call(mcp_actor(), tool, args)
                self.assertTrue(result.is_error, (tool, key, result.content))
                self.assertEqual(f'Error executing tool {tool}: Campos no permitidos: {key}.',
                                 result.content[0].text)
                self.assertEqual(before, hashes())
