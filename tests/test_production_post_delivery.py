"""PS5 public activity rules; refused writes preserve domain and audit hashes."""
from datetime import date
from itertools import product
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from test_production_property import PropertyCase, TODAY
from test_design import actor
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import people, reclamaciones, nonconformities
from app.services.errors import Conflict, PermissionDenied

ACTIVITY = dict(description='Warranty visit', kind='warranty', responsible_id=1,
                due_date=TODAY, customer_id=1)
RESULT = dict(result_date=TODAY, result='Repaired', evidence='Report R-1')


class PostDeliveryCase(PropertyCase):
    def setUp(self):
        super().setUp()
        from app.services import production_post_delivery
        self.post = production_post_delivery

    def activity(self, **values):
        row = self.post.activities.create(db.session, self.who, ACTIVITY | values, today=TODAY)
        db.session.commit()
        return row

    def change(self, row, **values):
        result = self.post.activities.update(db.session, self.who, row.id, values, today=TODAY)
        db.session.commit()
        return result

    def finish(self, row, target='completed', **values):
        result = self.post.transition(db.session, self.who, row.id, target, today=TODAY, **values)
        db.session.commit()
        return result


class PostDeliveryRules(PostDeliveryCase):
    def test_required_description_kind_responsible_and_due_date(self):
        for key in ('description', 'kind', 'responsible_id', 'due_date'):
            self.reject(lambda: self.post.activities.create(db.session, self.who,
                {k: v for k, v in ACTIVITY.items() if k != key}, today=TODAY),
                f'Faltan campos obligatorios: {key}.')
            self.reject(lambda: self.activity(**{key: None}))
        for values in ({'description': ' '}, {'kind': 'release'}, {'responsible_id': True}):
            self.reject(lambda: self.activity(**values))
        row = self.activity(source='Contract C-1')
        self.assertEqual(('pending', TODAY, 'Contract C-1'), (row.status.name, row.due_date, row.source))

    def test_responsible_new_active_retained_inactive_and_protected(self):
        row = self.activity()
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        self.change(row, responsible_id=1)
        self.assertEqual(before, hashes())
        self.reject(lambda: self.activity())
        self.reject(lambda: self.activity(responsible_id=999))
        self.reject(lambda: people.delete(db.session, self.who, 1), people.STILL_REFERENCED, Conflict)

    def test_citations_live_new_orders_retained_cancelled_and_restrict(self):
        from app.models import Cliente, ProductionOrder, Reclamacion, NoConformidad
        from sqlalchemy.exc import IntegrityError
        order = self.create()
        nc = nonconformities.create(db.session, self.who,
            {'descripcion': 'Fault', 'fecha_detectada': TODAY})
        complaint = reclamaciones.create(db.session, self.who,
            {'cliente_id': 1, 'descripcion': 'Fault'}, today=TODAY)
        db.session.commit()
        row = self.activity(order_ids=[order.id, order.id], complaint_id=complaint.id, nonconformity_id=nc.id)
        self.assertEqual([order.id], row.order_ids)
        for key in ('customer_id', 'complaint_id', 'nonconformity_id'):
            self.reject(lambda: self.activity(**{key: 999}))
        for bad in ([999], [True], None):
            self.reject(lambda: self.activity(order_ids=bad))
        self.service.transition(db.session, self.who, order.id, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        before = hashes()
        self.change(row, order_ids=[order.id])
        self.assertEqual(before, hashes())
        self.reject(lambda: self.activity(order_ids=[order.id]))
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        for model in (Cliente, ProductionOrder, Reclamacion, NoConformidad):
            self.reject(lambda: db.session.execute(model.__table__.delete().where(model.id == 1)), error=IntegrityError)
        self.assertEqual([], self.change(row, order_ids=[]).order_ids)

    def test_completion_date_result_evidence_and_terminal_readonly_not_release(self):
        from app.services import audit
        order = self.create()
        before = audit.snapshot(order)
        row = self.activity(order_ids=[order.id])
        for values in ({}, {'result_date': TODAY}, RESULT | {'result_date': date(2999, 1, 1)},
                       RESULT | {'result': ' '}, RESULT | {'evidence': None}, RESULT | {'reason': 'Extra'}):
            self.reject(lambda: self.finish(row, **values))
        self.finish(row, **RESULT)
        self.assertEqual(('completed', TODAY, 'Repaired', 'Report R-1'),
            (row.status.name, row.result_date, row.result, row.evidence))
        self.assertEqual(before, audit.snapshot(self.service.orders.get(db.session, self.who, order.id)))
        self.reject(lambda: self.change(row))
        self.reject(lambda: self.finish(row, **RESULT))
        self.reject(lambda: self.post.activities.delete(db.session, self.who, row.id))

    def test_cancel_requires_dated_reason_and_readonly(self):
        row = self.activity()
        for values in ({}, {'result_date': TODAY}, {'reason': 'Stop'},
                       {'result_date': date(2999, 1, 1), 'reason': 'Stop'},
                       {'result_date': TODAY, 'reason': ' '}, {'result_date': TODAY, 'reason': 'Stop', 'evidence': 'Extra'}):
            self.reject(lambda: self.finish(row, 'cancelled', **values))
        self.finish(row, 'cancelled', result_date=TODAY, reason='Stop')
        self.assertEqual(('cancelled', TODAY, 'Stop', None, None),
            (row.status.name, row.result_date, row.reason, row.result, row.evidence))
        self.reject(lambda: self.change(row, description='Other'))
        self.reject(lambda: self.finish(row, **RESULT))
        self.reject(lambda: self.post.activities.delete(db.session, self.who, row.id))

    def test_overdue_boundary_and_each_filter(self):
        late = self.activity(due_date=date(2026, 1, 1))
        today = self.activity(customer_id=None, kind='other')
        closed = self.activity(due_date=date(2026, 1, 1))
        self.finish(closed, **RESULT)
        self.assertTrue(self.post.is_overdue(late, today=TODAY))
        for row in (today, closed):
            self.assertFalse(self.post.is_overdue(row, today=TODAY))
        for filters, expected in (({'overdue': True}, [late.id]), ({'overdue': False}, [today.id, closed.id]),
            ({'status': 'completed'}, [closed.id]), ({'kind': 'other'}, [today.id]),
            ({'customer_id': 1}, [late.id, closed.id]), ({'responsible_id': 1}, [late.id, today.id, closed.id])):
            self.assertEqual(sorted(expected), sorted(r.id for r in self.post.activities.list_(db.session, self.who, today=TODAY, **filters)))

    def test_roles_web_mcp_lifecycle_and_admin_unlinked_pending_delete(self):
        row = self.activity()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, self.post.activities.get(db.session, who, row.id).id)
            self.assertIn(row, self.post.activities.list_(db.session, who, today=TODAY))
            if role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.post.activities.create(db.session, who, ACTIVITY, today=TODAY), error=PermissionDenied)
                self.reject(lambda: self.post.activities.update(db.session, who, row.id, {}, today=TODAY), error=PermissionDenied)
            else:
                self.post.activities.update(db.session, who, row.id, {}, today=TODAY)
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                for target, values in (('completed', RESULT), ('cancelled', {'result_date': TODAY, 'reason': 'Stop'})):
                    self.reject(lambda: self.post.transition(db.session, who, row.id, target, today=TODAY, **values), error=PermissionDenied)
            if channel == 'mcp' or role is not RoleEnum.ADMINISTRADOR:
                self.reject(lambda: self.post.activities.delete(db.session, who, row.id), error=PermissionDenied)
        for key in ('status', 'result_date', 'result', 'evidence', 'reason', 'id', 'overdue'):
            self.reject(lambda: self.change(row, **{key: 'x'}))
        linked = self.activity(order_ids=[self.create().id])
        self.reject(lambda: self.post.activities.delete(db.session, self.who, linked.id))
        self.post.activities.delete(db.session, self.who, row.id)
        db.session.commit()
        self.assertNotIn(row, self.post.activities.list_(db.session, self.who, today=TODAY))

    def test_every_write_locks_and_refreshes_person_after_parent_lock(self):
        from app.models import Person
        row = self.activity()
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        unused = self.activity()
        try:
            for operation in (lambda: self.change(row, source='Legal'), lambda: self.finish(row, **RESULT),
                              lambda: self.post.activities.delete(db.session, self.who, unused.id)):
                seen.clear()
                operation()
                self.assertTrue(any('FROM post_delivery_activities' in sql and 'FOR UPDATE' in sql and refresh for sql, refresh in seen))
                self.assertTrue(all(refresh for _, refresh in seen))
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        row = self.activity()
        other = people.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        original = self.post._lock
        def deactivate(session, record_id):
            locked = original(session, record_id)
            session.execute(Person.__table__.update().where(Person.id == other.id)
                .values(activo=False).execution_options(synchronize_session=False))
            return locked
        with patch.object(self.post, '_lock', side_effect=deactivate):
            self.reject(lambda: self.change(row, responsible_id=other.id))

    def test_warm_parent_and_order_are_refreshed_under_lock(self):
        from app.models import PostDeliveryActivity, ProductionOrder
        row = self.activity()
        order = self.create()
        original = self.post._lock
        def complete(session, record_id):
            session.execute(PostDeliveryActivity.__table__.update().where(
                PostDeliveryActivity.id == record_id).values(status='completed', **RESULT)
                .execution_options(synchronize_session=False))
            return original(session, record_id)
        with patch.object(self.post, '_lock', side_effect=complete):
            self.reject(lambda: self.change(row, description='Wrong'), self.post.READ_ONLY)
        def cancel_order(session, record_id):
            locked = original(session, record_id)
            session.execute(ProductionOrder.__table__.update().where(
                ProductionOrder.id == order.id).values(state='cancelled', cancellation_reason='Stop')
                .execution_options(synchronize_session=False))
            return locked
        with patch.object(self.post, '_lock', side_effect=cancel_order):
            self.reject(lambda: self.change(row, order_ids=[order.id]),
                'Elige órdenes no canceladas para los nuevos vínculos.')

    def test_s3_all_activity_writes_leave_order_snapshot_and_audit_unchanged(self):
        from app.models import AuditLog
        from app.services import audit
        order = self.create()
        before = audit.snapshot(order)
        logs = AuditLog.query.filter_by(entity_type='production_orders').count()
        for target, values in (('completed', RESULT), ('cancelled', {'result_date': TODAY, 'reason': 'Stop'})):
            row = self.activity(order_ids=[order.id])
            self.change(row, source='Contract')
            self.finish(row, target, **values)
            self.assertEqual(before, audit.snapshot(self.service.orders.get(db.session, self.who, order.id)))
            self.assertEqual(logs, AuditLog.query.filter_by(entity_type='production_orders').count())
        self.assertNotIn('released', type(order.state).__members__)
