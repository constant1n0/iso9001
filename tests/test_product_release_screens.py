"""Release rendered forms and MCP boundary, including no-op and refusal hashes."""
from io import BytesIO
from test_product_release import ReleaseCase, CHECK, TODAY
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum


class ReleaseAvailability(ReleaseCase):
    def test_list_is_available(self):
        self.login()
        self.assertEqual(200, self.client.get('/liberacion/').status_code)


class ReleaseScreens(ReleaseCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        from flask import g
        g.pop('_login_user', None)

    def test_rendered_roundtrips_and_strict_payloads(self):
        row = self.release(notes='Keep')
        check = self.verification(row)
        self.login()
        for path in (f'/liberacion/{row.id}/editar',
                     f'/liberacion/{row.id}/verificaciones/{check.id}/editar'):
            parser = BrowserForm()
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            parser.feed(response.get_data(as_text=True))
            form, before = parser.values, hashes()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
            for key in form:
                if key == 'csrf_token':
                    continue
                bad = form.copy()
                bad.pop(key)
                self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
                self.assertEqual(before, hashes())
            for data in ({'id': '2'}, {'release_id': '2'}, {'status': 'released'},
                         {'notes': (BytesIO(b'x'), 'x.txt')}):
                self.assertEqual(400, self.client.post(path, data=dict(form) | data).status_code)
                self.assertEqual(before, hashes())
        for path in ('/liberacion/nueva', '/liberacion/1/editar', '/liberacion/1/eliminar',
                     '/liberacion/1/verificaciones/nueva', '/liberacion/1/verificaciones/1/editar',
                     '/liberacion/1/verificaciones/1/eliminar'):
            before = hashes()
            self.assertEqual(400, self.client.post(path, json={'id': 2}).status_code)
            self.assertEqual(before, hashes())
        self.assertIn('/liberacion/1', self.client.get('/produccion/1').get_data(as_text=True))

    def test_roles_filters_foreign_child_csrf_and_decided_pages(self):
        row = self.release(self.finished(customer_id=1), notes='First')
        check = self.verification(row)
        other = self.release(notes='Other')
        for role in RoleEnum:
            self.login(role)
            for path in ('/liberacion/', '/liberacion/1'):
                self.assertEqual(200, self.client.get(path).status_code)
            self.assertEqual(302 if role is RoleEnum.OPERATIVO else 200,
                self.client.get('/liberacion/nueva').status_code)
            if role is not RoleEnum.ADMINISTRADOR:
                before = hashes()
                self.client.post('/liberacion/1/eliminar')
                self.assertEqual(before, hashes())
        self.login()
        for query in ('order_id=1', 'customer_id=1', 'status=draft&order_id=1'):
            text = self.client.get('/liberacion/?' + query).get_data(as_text=True)
            self.assertIn('First', text)
            self.assertNotIn('Other', text)
        self.assertIn('Other', self.client.get('/liberacion/?status=bad&order_id=999&customer_id=x').get_data(as_text=True))
        before = hashes()
        self.client.post(f'/liberacion/{other.id}/verificaciones/{check.id}/eliminar')
        self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(400, self.client.post('/liberacion/1/eliminar').status_code)
        self.assertEqual(before, hashes())
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.decided(row)
        self.assertEqual(302, self.client.get('/liberacion/1/editar').status_code)
        self.assertEqual(302, self.client.get('/liberacion/1/verificaciones/nueva').status_code)
        self.assertIn('Report R-1', self.client.get('/liberacion/1').get_data(as_text=True))
        self.assertEqual(405, self.client.get('/liberacion/1/eliminar').status_code)


    def test_web_create_edit_delete_and_operative_refusals(self):
        self.finished()
        self.login()
        self.assertEqual(302, self.client.post('/liberacion/nueva', data={'order_id': '1', 'notes': 'Draft'}).status_code)
        data = {k: v.isoformat() if hasattr(v, 'isoformat') else str(v) for k, v in CHECK.items()}
        self.assertEqual(302, self.client.post('/liberacion/1/verificaciones/nueva', data=data).status_code)
        self.login(RoleEnum.OPERATIVO)
        before = hashes()
        for path in ('/liberacion/nueva', '/liberacion/1/editar', '/liberacion/1/verificaciones/nueva',
                     '/liberacion/1/verificaciones/1/editar', '/liberacion/1/verificaciones/1/eliminar'):
            response = self.client.post(path, data=data)
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.location.endswith('/dashboard/'))
            self.assertEqual(before, hashes())
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.client.post('/liberacion/1/verificaciones/1/editar', data=data | {'evidence': 'R-2'}).status_code)
        self.assertEqual(302, self.client.post('/liberacion/1/verificaciones/1/eliminar').status_code)
        self.login()
        self.assertEqual(302, self.client.post('/liberacion/1/eliminar').status_code)
        from app.services import product_release
        self.assertEqual([], product_release.releases.list_(db.session, self.who))

    def test_additive_migration_preserves_85_rows_and_checks(self):
        import importlib.util
        from pathlib import Path
        from unittest.mock import patch
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        from sqlalchemy.exc import IntegrityError
        from app.models import ProductRelease, ReleaseVerification
        self.finished()
        self.item(order_ids=[1])
        self.incident()
        from app.services import production_post_delivery
        production_post_delivery.activities.create(db.session, self.who, dict(
            description='Warranty', kind='warranty', responsible_id=1, due_date=TODAY), today=TODAY)
        db.session.commit()
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/f1b3d5e7a9c2_add_product_release.py'
        spec = importlib.util.spec_from_file_location('release_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('e0a2c4d6f8b1', migration.down_revision)
        db.metadata.drop_all(db.engine, tables=[ReleaseVerification.__table__, ProductRelease.__table__])
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return [(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all()) for t in legacy]
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                for table in ('product_releases', 'release_verifications'):
                    self.assertTrue(inspect(connection).get_pk_constraint(table)['name'])
                    for fk in inspect(connection).get_foreign_keys(table):
                        self.assertTrue(fk['name'])
                        self.assertEqual('SET NULL' if fk['constrained_columns'][0] in
                            ('created_by_id', 'updated_by_id') else 'RESTRICT', fk['options']['ondelete'])
                connection.execute(ProductRelease.__table__.insert().values(order_id=1))
                for values in ({'status': 'released'}, {'status': 'rejected'}, {'decision_note': 'Extra'},
                    {'decision_date': TODAY}, {'concession': False}, {'concession_reason': 'Extra'},
                    {'customer_approval_means': 'Email'}, {'status': 'other'}):
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(ProductRelease.__table__.update().values(**values))
                with self.assertRaises(IntegrityError), connection.begin_nested():
                    connection.execute(ProductRelease.__table__.insert().values(order_id=1))
                migration.downgrade()
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())


class ReleaseMcp(McpDbCase):
    async def test_modules_available(self):
        result = await self.call(mcp_actor(), 'qms_modules')
        self.assertFalse(result.is_error)
        self.assertIn('product_releases', {m['slug'] for m in result.structured_content['modules']})

    async def test_roles_draft_roundtrip_and_no_child_update_delete_decision(self):
        from app.services import production
        from test_design import actor
        self.seed('production_orders')
        self.seed('personas')
        production.transition(db.session, actor(), 1, 'in_progress')
        production.transition(db.session, actor(), 1, 'finished')
        db.session.commit()
        data = CHECK | {'release_id': 1, 'date': TODAY.isoformat()}
        for role in RoleEnum:
            before = hashes()
            result = await self.call(mcp_actor(role), 'qms_create', {
                'module': 'product_releases', 'data': {'order_id': 1}})
            self.assertEqual(role is RoleEnum.OPERATIVO, result.is_error, result.content)
            if result.is_error:
                self.assertEqual(before, hashes())
                continue
            rid = result.structured_content['id']
            self.assertFalse((await self.call(mcp_actor(role), 'qms_update', {
                'module': 'product_releases', 'id': rid, 'data': {'notes': 'Keep'}})).is_error)
            result = await self.call(mcp_actor(role), 'qms_create', {'module': 'release_verifications', 'data': data})
            self.assertFalse(result.is_error, result.content)
            cid = result.structured_content['id']
            for reader in RoleEnum:
                for slug, record_id in (('product_releases', rid), ('release_verifications', cid)):
                    self.assertFalse((await self.call(mcp_actor(reader), 'qms_get', {'module': slug, 'id': record_id})).is_error)
                    self.assertFalse((await self.call(mcp_actor(reader), 'qms_list', {'module': slug})).is_error)
            for slug, record_id, values in (('release_verifications', cid, {}),
                ('product_releases', rid, {'status': 'released'}),
                ('product_releases', rid, {'decision_note': 'Wrong'})):
                before = hashes()
                self.assertTrue((await self.call(mcp_actor(role), 'qms_update', {
                    'module': slug, 'id': record_id, 'data': values})).is_error)
                self.assertEqual(before, hashes())
            from app.services import product_release
            product_release.releases.delete(db.session, actor(), rid)
            db.session.commit()
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})
