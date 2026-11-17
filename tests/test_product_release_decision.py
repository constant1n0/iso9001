"""T2 decision rules through services, rendered web forms and MCP."""
from datetime import timedelta
from io import BytesIO
from itertools import product
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict
from test_product_release import ReleaseCase, CHECK, TODAY
from test_communication_independent import BrowserForm, hashes
from test_design import actor
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import AuditLog, Person, ProductRelease, RoleEnum, User
from app.services import audit, people, product_release as service
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied


class DecisionCase(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.decider = people.create(db.session, self.who, {
            'nombre': 'Release authority', 'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        db.session.commit()
        self.authority = Actor(self.ids[RoleEnum.ADMINISTRADOR], 'Admin',
                               RoleEnum.ADMINISTRADOR, 'web')

    def authorize(self, row, **values):
        self.assertTrue(callable(getattr(service, 'authorize', None)),
                        'Release authorization service is missing')
        result = service.authorize(db.session, self.authority, row.id, today=TODAY, **values)
        db.session.commit()
        return result

    def deny(self, row, reason='Rejected'):
        self.assertTrue(callable(getattr(service, 'reject', None)),
                        'Release rejection service is missing')
        result = service.reject(db.session, self.authority, row.id, reason=reason, today=TODAY)
        db.session.commit()
        return result

    def checked(self, **values):
        row = self.release(self.finished(owner_id=1))
        self.verification(row, **values)
        return row

    def concession(self):
        return dict(concession=True, concession_reason='Accepted deviation',
                    concession_approver_id=self.decider.id)


class DecisionRules(DecisionCase):
    def test_all_conforming_authorization_and_order_unchanged(self):
        row = self.checked()
        before = audit.snapshot(row.order)
        logs = AuditLog.query.filter_by(entity_type='production_orders').count()
        self.authorize(row)
        row = service.releases.get(db.session, self.who, row.id)
        self.assertEqual(('released', TODAY, self.decider.id, self.authority.user_id, False),
            (row.status.name, row.decision_date, row.decided_by_id,
             row.decided_by_user_id, row.concession))
        self.assertIsNone(row.concession_reason)
        self.assertEqual(before, audit.snapshot(row.order))
        self.assertEqual(logs, AuditLog.query.filter_by(entity_type='production_orders').count())
        log = AuditLog.query.filter_by(entity_type='product_releases').order_by(AuditLog.id.desc()).first()
        self.assertEqual('borrador', log.before['status'])
        self.assertEqual(self.decider.id, log.after['decided_by_id'])
        self.assertEqual(self.authority.user_id, log.actor_user_id)

    def test_nonconforming_requires_explicit_concession(self):
        row = self.checked(result='nonconforming')
        self.reject(lambda: self.authorize(row),
            'Hay verificaciones no conformes; registra una concesión para autorizar.')
        self.authorize(row, **self.concession())
        self.assertTrue(row.concession)
        self.assertEqual('Accepted deviation', row.concession_reason)
        self.assertEqual(self.decider.id, row.concession_approver_id)

    def test_concession_reason_and_active_approver_even_if_all_conforming(self):
        row = self.checked()
        inactive = people.create(db.session, self.who, {'nombre': 'Inactive', 'activo': False})
        db.session.commit()
        for values in ({'concession_reason': None}, {'concession_reason': ' '},
            {'concession_approver_id': None}, {'concession_approver_id': inactive.id},
            {'concession_approver_id': 999}, {'concession_approver_id': True}):
            self.reject(lambda: self.authorize(row, **(self.concession() | values)))
        self.reject(lambda: self.authorize(row, concession_reason='Ignored'))
        self.authorize(row, **self.concession())
        self.assertTrue(row.concession)

    def test_customer_approval_pair_and_not_future(self):
        row = self.checked()
        for values in ({'customer_approval_date': TODAY}, {'customer_approval_means': 'Email'},
            {'customer_approval_date': TODAY, 'customer_approval_means': ' '},
            {'customer_approval_date': TODAY + timedelta(days=1), 'customer_approval_means': 'Email'}):
            self.reject(lambda: self.authorize(row, **(self.concession() | values)))
        self.authorize(row, **self.concession(), customer_approval_date=TODAY,
                       customer_approval_means='Email E-1')
        self.assertEqual((TODAY, 'Email E-1'), (row.customer_approval_date, row.customer_approval_means))

    def test_at_least_one_verification(self):
        row = self.release()
        self.reject(lambda: self.authorize(row), 'Añade al menos una verificación antes de autorizar.')
        self.reject(lambda: self.authorize(row, **self.concession()),
                    'Añade al menos una verificación antes de autorizar.')

    def test_rejection_reason_trace_and_replacement_order_unchanged(self):
        row = self.release(self.finished(owner_id=1))
        before = audit.snapshot(row.order)
        self.reject(lambda: self.deny(row, ' '), 'Indica el motivo del rechazo.')
        self.deny(row, 'Wrong evidence')
        self.assertEqual(('rejected', 'Wrong evidence', TODAY, self.decider.id,
                          self.authority.user_id, False),
            (row.status.name, row.decision_note, row.decision_date, row.decided_by_id,
             row.decided_by_user_id, row.concession))
        self.assertEqual(before, audit.snapshot(row.order))
        self.assertNotEqual(row.id, self.release(row.order).id)

    def test_only_draft_and_parent_child_immutability_every_channel(self):
        for rejecting in (False, True):
            row = self.checked()
            child = service.verifications.list_(db.session, self.who, release_id=row.id)[0]
            self.deny(row) if rejecting else self.authorize(row)
            for operation in (lambda: self.authorize(row), lambda: self.deny(row)):
                self.reject(operation, service.READ_ONLY)
            for channel in ('web', 'mcp'):
                who = actor(channel=channel)
                for operation in (
                    lambda: service.releases.update(db.session, who, row.id, {}),
                    lambda: service.verifications.create(db.session, who,
                        CHECK | {'release_id': row.id}, today=TODAY)):
                    self.reject(operation, service.READ_ONLY)
                self.reject(lambda: service.releases.delete(db.session, who, row.id),
                    error=PermissionDenied if channel == 'mcp' else service.ValidationError)
                for operation in (
                    lambda: service.verifications.update(db.session, who, child.id, {}, today=TODAY),
                    lambda: service.verifications.delete(db.session, who, child.id)):
                    self.reject(operation, error=PermissionDenied if channel == 'mcp' else service.ValidationError)

    def test_separation_active_link_role_and_channel_matrix(self):
        row = self.checked()
        self.assertTrue(callable(getattr(service, 'authorize', None)))
        self.assertTrue(callable(getattr(service, 'reject', None)))
        for role, channel in product(RoleEnum, ('web', 'mcp', 'system')):
            if role is RoleEnum.ADMINISTRADOR and channel == 'web':
                continue
            who = Actor(self.authority.user_id, 'Actor', role, channel)
            for operation in (
                lambda: service.authorize(db.session, who, row.id, today=TODAY),
                lambda: service.reject(db.session, who, row.id, reason='No', today=TODAY)):
                self.reject(operation, error=PermissionDenied)
        for values in ({'user_id': None}, {'activo': False}):
            people.update(db.session, self.who, self.decider.id, values)
            db.session.commit()
            self.reject(lambda: self.authorize(row))
            self.reject(lambda: self.deny(row))
            people.update(db.session, self.who, self.decider.id,
                          {'user_id': self.authority.user_id, 'activo': True})
            db.session.commit()
        own = self.release(self.finished(owner_id=self.decider.id))
        self.verification(own)
        self.reject(lambda: self.authorize(own),
                    'La decisión requiere una persona distinta del responsable de la orden.')
        self.reject(lambda: self.deny(own))

    def test_decider_approver_and_user_deletion_protected(self):
        approver = people.create(db.session, self.who, {'nombre': 'Concession authority'})
        db.session.commit()
        row = self.checked()
        self.authorize(row, **(self.concession() | {'concession_approver_id': approver.id}))
        for pid in (self.decider.id, approver.id):
            self.reject(lambda: people.delete(db.session, self.who, pid), people.STILL_REFERENCED, Conflict)
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        for model, record_id in ((Person, self.decider.id), (Person, approver.id),
                                 (User, self.authority.user_id)):
            self.reject(lambda: db.session.execute(model.__table__.delete().where(
                model.id == record_id)), error=IntegrityError)

    def test_release_then_order_locks_refresh_and_postlock_validation(self):
        row = self.checked()
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            self.authorize(row)
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        self.assertIn('FROM product_releases', seen[0][0])
        self.assertIn('FROM production_orders', seen[1][0])
        self.assertTrue(all(refresh and 'FOR ' in sql for sql, refresh in seen))
        row = self.checked()
        original = self.service._lock
        def change_owner(session, record_id):
            from app.models import ProductionOrder
            session.execute(ProductionOrder.__table__.update().where(
                ProductionOrder.id == record_id).values(owner_id=self.decider.id)
                .execution_options(synchronize_session=False))
            return original(session, record_id)
        with patch.object(self.service, '_lock', side_effect=change_owner):
            self.reject(lambda: self.authorize(row))
        original_release = service._lock
        def decide(session, record_id):
            session.execute(ProductRelease.__table__.update().where(ProductRelease.id == record_id)
                .values(status='rejected', decision_note='Concurrent', decision_date=TODAY,
                    decided_by_id=self.decider.id, decided_by_user_id=self.authority.user_id,
                    concession=False).execution_options(synchronize_session=False))
            return original_release(session, record_id)
        with patch.object(service, '_lock', side_effect=decide):
            self.reject(lambda: self.authorize(row), service.READ_ONLY)
        def deactivate(session, record_id):
            locked = original_release(session, record_id)
            session.execute(Person.__table__.update().where(Person.id == self.decider.id)
                .values(activo=False).execution_options(synchronize_session=False))
            return locked
        with patch.object(service, '_lock', side_effect=deactivate):
            self.reject(lambda: self.authorize(row))


class DecisionScreens(DecisionCase):
    def login(self, role=RoleEnum.ADMINISTRADOR):
        super().login(role)
        from flask import g
        g.pop('_login_user', None)

    def browser(self, row, action):
        response = self.client.get(f'/liberacion/{row.id}')
        text = response.get_data(as_text=True)
        self.assertIn(f'/liberacion/{row.id}/{action}', text)
        from html.parser import HTMLParser
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.active = False
                self.parser = BrowserForm()
            def handle_starttag(self, tag, attrs):
                if tag == 'form':
                    self.active = dict(attrs).get('action', '').endswith('/' + action)
                elif self.active:
                    self.parser.handle_starttag(tag, attrs)
            def handle_endtag(self, tag):
                if tag == 'form':
                    self.active = False
                elif self.active:
                    self.parser.handle_endtag(tag)
            def handle_data(self, data):
                if self.active:
                    self.parser.handle_data(data)
        forms = Forms()
        forms.feed(text)
        return forms.parser.values

    def test_concession_filter_and_decision_on_release_and_order(self):
        self.login()
        row = self.checked()
        self.authorize(row, **self.concession(), customer_approval_date=TODAY,
                       customer_approval_means='Email E-1')
        other = self.release(notes='Unselected draft')
        text = self.client.get('/liberacion/?concession=true').get_data(as_text=True)
        self.assertIn('Con concesión', text)
        self.assertNotIn('Unselected draft', text)
        self.assertIn('Unselected draft', self.client.get('/liberacion/?concession=unknown').get_data(as_text=True))
        for path in (f'/liberacion/{row.id}', f'/produccion/{row.order_id}'):
            text = self.client.get(path).get_data(as_text=True)
            for value in ('liberada', 'Release authority', 'Con concesión', 'Accepted deviation', 'Email E-1', str(TODAY)):
                self.assertIn(value, text)
        self.deny(other, 'No evidence')
        for path in (f'/liberacion/{other.id}', f'/produccion/{other.order_id}'):
            self.assertIn('No evidence', self.client.get(path).get_data(as_text=True))

    def test_rendered_decision_forms_strict_refusals_csrf_roles_and_immutability(self):
        row = self.checked()
        self.login()
        for action in ('autorizar', 'rechazar'):
            path = f'/liberacion/{row.id}/{action}'
            form = self.browser(row, action)
            self.assertEqual(405, self.client.get(path).status_code)
            before = hashes()
            for key in form:
                if key == 'csrf_token':
                    continue
                bad = form.copy()
                bad.pop(key)
                self.assertEqual(400, self.client.post(path, data=bad).status_code, key)
                self.assertEqual(before, hashes())
            for data in ({'order_id': '999'}, {'status': 'released'}, {'decision_date': '2000-01-01'},
                         {'decision_note': (BytesIO(b'x'), 'x.txt')}):
                self.assertEqual(400, self.client.post(path, data=MultiDict(form) | data).status_code)
                self.assertEqual(before, hashes())
            duplicate = form.copy()
            key = next(k for k in form if k != 'csrf_token')
            duplicate.add(key, form[key])
            self.assertEqual(400, self.client.post(path, data=duplicate).status_code)
            for kwargs in ({'json': {'concession': True}}, {'data': 'raw', 'content_type': 'text/plain'}):
                self.assertEqual(400, self.client.post(path, **kwargs).status_code)
                self.assertEqual(before, hashes())
            self.app.config['WTF_CSRF_ENABLED'] = True
            self.assertEqual(400, self.client.post(path, data=form).status_code)
            self.app.config['WTF_CSRF_ENABLED'] = False
            for role in (RoleEnum.AUDITOR, RoleEnum.OPERATIVO):
                self.login(role)
                self.assertNotIn(path, self.client.get(f'/liberacion/{row.id}').get_data(as_text=True))
                self.assertEqual(302, self.client.post(path, data=form).status_code)
                self.assertEqual(before, hashes())
            self.login()
        form = self.browser(row, 'autorizar')
        self.assertEqual(302, self.client.post(f'/liberacion/{row.id}/autorizar', data=form).status_code)
        self.assertEqual('released', service.releases.get(db.session, self.who, row.id).status.name)
        before = hashes()
        for suffix in ('autorizar', 'rechazar', 'eliminar', 'editar', 'verificaciones/nueva',
                       'verificaciones/1/editar', 'verificaciones/1/eliminar'):
            self.client.post(f'/liberacion/{row.id}/{suffix}', data=form)
            self.assertEqual(before, hashes())
        other = self.release()
        form = self.browser(other, 'rechazar')
        form['reason'] = 'Wrong evidence'
        self.assertEqual(302, self.client.post(f'/liberacion/{other.id}/rechazar', data=form).status_code)
        self.assertEqual('rejected', service.releases.get(db.session, self.who, other.id).status.name)

    def test_concession_rendered_roundtrip_and_business_refusal_message(self):
        self.login()
        row = self.checked(result='nonconforming')
        path = f'/liberacion/{row.id}/autorizar'
        form = self.browser(row, 'autorizar')
        before = hashes()
        response = self.client.post(path, data=form)
        self.assertEqual(200, response.status_code)
        self.assertIn('Hay verificaciones no conformes; registra una concesión para autorizar.',
                      response.get_data(as_text=True))
        self.assertEqual(before, hashes())
        for key, value in dict(concession='y', concession_reason='Accepted deviation',
                concession_approver_id=str(self.decider.id),
                customer_approval_date=TODAY.isoformat(), customer_approval_means='Email').items():
            form[key] = value
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertTrue(service.releases.get(db.session, self.who, row.id).concession)


class DecisionMcp(McpDbCase):
    async def test_decided_reads_and_writes_refused_without_transition_tool(self):
        self.seed('release_verifications')
        user = User(username='authority', email='authority@example.test',
                    password='unused', role=RoleEnum.ADMINISTRADOR)
        db.session.add(user)
        db.session.commit()
        who = Actor(user.id, 'Authority', RoleEnum.ADMINISTRADOR, 'web')
        decider = people.create(db.session, who, {'nombre': 'Authority', 'user_id': user.id})
        db.session.commit()
        service.authorize(db.session, who, 1, today=TODAY, concession=True,
            concession_reason='Accepted deviation', concession_approver_id=decider.id,
            customer_approval_date=TODAY, customer_approval_means='Email')
        db.session.commit()
        expected = dict(status='liberada', decision_date=TODAY.isoformat(),
            decided_by_id=decider.id, decided_by_user_id=user.id, decision_note=None,
            concession=True, concession_reason='Accepted deviation',
            concession_approver_id=decider.id, customer_approval_date=TODAY.isoformat(),
            customer_approval_means='Email')
        for role in RoleEnum:
            result = await self.call(mcp_actor(role), 'qms_get', {'module': 'product_releases', 'id': 1})
            self.assertFalse(result.is_error, result.content)
            for key, value in expected.items():
                self.assertEqual(value, result.structured_content[key], key)
            for name, arguments in (
                ('qms_update', {'module': 'product_releases', 'id': 1, 'data': {'notes': 'Changed'}}),
                ('qms_create', {'module': 'release_verifications', 'data':
                    CHECK | {'date': TODAY.isoformat(), 'release_id': 1}})):
                before = hashes()
                result = await self.call(mcp_actor(role), name, arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(before, hashes())
        tools = {t.name for t in await self.list_tools()}
        self.assertFalse(any('authoriz' in name or 'reject' in name or 'transition' in name for name in tools))
