"""Independent PS3 regressions: S5 authority/history/input boundary and S3."""
from io import BytesIO
from sqlalchemy import event
from werkzeug.datastructures import MultiDict
from test_production_changes import ChangeCase
from test_communication_independent import BrowserForm, hashes
from test_design_io import TODAY
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import Person, ProductionOrder, RoleEnum, User
from app.services import people, qms_changes, production_changes as pc
from app.services.actor import Actor
from app.services.errors import Conflict

class IndependentChanges(ChangeCase):
    def login_admin(self):
        self.login(RoleEnum.ADMINISTRADOR)
        from flask import g
        g.pop('_login_user', None)
    def test_s5_warm_owner_changed_after_proposal(self):
        other = self.create(owner_id=1)
        row = self.propose(order_ids=[other.id, 1], review='Checked')
        self.assertEqual([1, 1], [o.owner_id for o in row.orders])
        session = db.session()
        session.expire_on_commit = False
        session.execute(ProductionOrder.__table__.update().where(
            ProductionOrder.id == other.id).values(owner_id=2))
        session.commit()
        self.assertEqual(1, other.owner_id)
        for rejecting in (False, True):
            self.reject(lambda: self.decide(row, rejecting), pc.SEPARATION)

    def test_s5_warm_linked_person_reassigned_to_owner(self):
        row = self.propose(review='Checked')
        cached = db.session.get(Person, 2)
        session = db.session()
        session.expire_on_commit = False
        session.execute(Person.__table__.update().where(Person.id == 2).values(user_id=None))
        session.execute(Person.__table__.update().where(Person.id == 1).values(
            user_id=self.approver.user_id))
        session.commit()
        self.assertEqual(self.approver.user_id, cached.user_id)
        self.reject(lambda: self.decide(row), pc.SEPARATION)

    def test_s3_decisions_never_transition_or_release_orders(self):
        for rejecting in (False, True):
            row = self.propose(review='Checked')
            self.decide(row, rejecting)
            self.assertEqual('planned', self.order.state.name)
        for state in ('in_progress', 'finished'):
            self.service.transition(db.session, self.who, 1, state)
        db.session.commit()
        self.assertEqual('finished', self.order.state.name)
        self.assertNotIn('released', type(self.order.state).__members__)

    def test_s5_sorted_union_locks_on_replacement(self):
        second, third = self.create(), self.create()
        row = self.propose(order_ids=[second.id, 1])
        seen = []
        def capture(state):
            sql = str(state.statement)
            if state.is_select and state.statement._for_update_arg is not None:
                if 'FROM production_orders' in sql:
                    seen.append((next(iter(state.statement.compile().params.values())),
                                 state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            pc.changes.update(db.session, self.who, row.id, {'order_ids': [third.id, second.id]})
            db.session.commit()
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        self.assertEqual([(1, True), (second.id, True), (third.id, True)], seen)

    def test_s5_rendered_noop_and_duplicate_upload_refusals(self):
        row = self.propose(review='Checked', description='Tolerance <A> & B')
        self.login_admin()
        path = f'/produccion/cambios/{row.id}/editar'
        parser = BrowserForm()
        parser.feed(self.client.get(path).get_data(as_text=True))
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=parser.values).status_code)
        self.assertEqual(before, hashes())
        for key, value in (('review', 'Injected'), ('order_ids', '1')):
            form = MultiDict(parser.values)
            form.add(key, value)
            self.assertEqual(400, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())
        self.assertEqual(400, self.client.post(path, data=parser.values.to_dict() | {
            'actions': (BytesIO(b'override'), 'actions.txt')}).status_code)
        self.assertEqual(before, hashes())
        self.reject(lambda: self.service.orders.delete(db.session, self.who, 1),
                    'No puedes eliminar una orden citada por cambios.', error=Conflict)

    def test_s5_cited_qms_delete_has_message_and_no_writes(self):
        qms = qms_changes.changes.create(db.session, self.who, dict(titulo='ERP',
            descripcion='ERP', origen='otro', proposito='Trace', responsable_id=1,
            fecha_propuesta=TODAY), today=TODAY)
        db.session.commit()
        self.propose(qms_change_id=qms.id)
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        self.login_admin()
        before = hashes()
        self.assertEqual(302, self.client.post(f'/cambios/{qms.id}/eliminar').status_code)
        self.assertEqual(before, hashes())
        self.assertIn(('danger', qms_changes.DELETE_CONFLICT), self.flashes())

    def test_s5_json_foreign_fields_never_decide(self):
        row = self.propose(review='Checked')
        self.login_admin()
        self.app.config['WTF_CSRF_ENABLED'] = True
        parser = BrowserForm()
        parser.feed(self.client.get(f'/produccion/cambios/{row.id}').get_data(as_text=True))
        before = hashes()
        response = self.client.post(f'/produccion/cambios/{row.id}/autorizar',
            headers={'X-CSRFToken': parser.values['csrf_token']},
            json={'order_id': 999, 'state': 'rejected'})
        self.assertIn(response.status_code, (400, 415),
                      f'state={row.state.name}; hashes unchanged={before == hashes()}')
        self.assertEqual(before, hashes())

class IndependentMcpHistory(McpDbCase):
    def decide_change(self, rejecting):
        self.seed('personas')
        user = User(username='Independent', password='unused', role=RoleEnum.ADMINISTRADOR)
        db.session.add(user)
        db.session.commit()
        who = Actor(user.id, user.username, RoleEnum.ADMINISTRADOR, 'web')
        people.create(db.session, who, {'nombre': 'Independent', 'user_id': user.id})
        rid = self.seed('production_changes', review='Checked')
        if rejecting:
            pc.reject(db.session, who, rid, reason='Unsafe')
        else:
            pc.authorize(db.session, who, rid, today=TODAY)
        db.session.commit()
        return rid
    async def test_s5_decided_mcp_get_retains_history(self):
        rid = self.decide_change(False)
        before = hashes()
        result = await self.call(mcp_actor(), 'qms_get', {'module': 'production_changes', 'id': rid})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(before, hashes())

    async def test_s5_decided_mcp_list_retains_history(self):
        rid = self.decide_change(True)
        result = await self.call(mcp_actor(), 'qms_list', {'module': 'production_changes',
                                'filters': {'state': 'rejected', 'order_id': 1}})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(1, result.structured_content['total'])
        self.assertEqual(rid, result.structured_content['items'][0]['id'])
