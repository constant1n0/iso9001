"""Release public rules: refused operations preserve database and audit hashes."""
from datetime import date
from itertools import product
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from test_production_property import PropertyCase, TODAY
from test_design import actor
from test_communication_independent import hashes
from app.extensions import db
from app.models import ProductRelease, ReleaseVerification, Person, RoleEnum
from app.services import product_release as service, people, audit
from app.services.errors import Conflict, PermissionDenied

CHECK = dict(criterion='Fits drawing', result='conforming', evidence='Report R-1',
             person_id=1, date=TODAY)
DECISION = dict(decision_date=TODAY, decided_by_id=1, decided_by_user_id=1,
                decision_note='Decision', concession=False)


class ReleaseCase(PropertyCase):
    def finished(self, **values):
        row = self.create(**values)
        self.service.transition(db.session, self.who, row.id, 'in_progress')
        self.service.transition(db.session, self.who, row.id, 'finished')
        db.session.commit()
        return row

    def release(self, order=None, **values):
        row = service.releases.create(db.session, self.who,
            {'order_id': (order or self.finished()).id} | values)
        db.session.commit()
        return row

    def verification(self, row, **values):
        result = service.verifications.create(db.session, self.who,
            CHECK | {'release_id': row.id} | values, today=TODAY)
        db.session.commit()
        return result

    def decided(self, row, status='released'):
        db.session.execute(ProductRelease.__table__.update().where(
            ProductRelease.id == row.id).values(status=status, **DECISION)
            .execution_options(synchronize_session=False))
        db.session.commit()


class ReleaseRules(ReleaseCase):
    def test_order_required_finished_and_unique_nonrejected(self):
        self.reject(lambda: service.releases.create(db.session, self.who, {}),
                    'Faltan campos obligatorios: order_id.')
        for value in (None, True, 999):
            self.reject(lambda: service.releases.create(db.session, self.who, {'order_id': value}))
        order = self.create()
        self.reject(lambda: self.release(order), 'Elige una orden terminada.')
        self.service.transition(db.session, self.who, order.id, 'in_progress')
        db.session.commit()
        self.reject(lambda: self.release(order))
        self.service.transition(db.session, self.who, order.id, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        self.reject(lambda: self.release(order))
        order = self.finished()
        row = self.release(order)
        self.assertEqual(('draft', order.id), (row.status.name, row.order_id))
        self.reject(lambda: self.release(order), service.SPEC.conflict, Conflict)
        self.decided(row, 'rejected')
        self.assertNotEqual(row.id, self.release(order).id)

    def test_verification_required_active_person_and_not_future(self):
        row = self.release()
        data = CHECK | {'release_id': row.id}
        for key in data:
            self.reject(lambda: service.verifications.create(db.session, self.who,
                {k: v for k, v in data.items() if k != key}, today=TODAY),
                f'Faltan campos obligatorios: {key}.')
        for values in ({'criterion': ' '}, {'evidence': None}, {'person_id': 999},
                       {'person_id': True}, {'result': 'other'}, {'date': date(2999, 1, 1)}):
            self.reject(lambda: self.verification(row, **values))
        check = self.verification(row, date=date(1900, 1, 1))
        self.assertEqual(date(1900, 1, 1), check.date)
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        service.verifications.update(db.session, self.who, check.id, CHECK | {'date': check.date}, today=TODAY)
        db.session.commit()
        self.assertEqual(before, hashes())
        self.reject(lambda: self.verification(row))
        self.reject(lambda: people.delete(db.session, self.who, 1), people.STILL_REFERENCED, Conflict)

    def test_decided_parent_and_children_immutable_even_noop(self):
        for status in ('released', 'rejected'):
            row = self.release()
            check = self.verification(row)
            self.decided(row, status)
            for operation in (lambda: service.releases.update(db.session, self.who, row.id, {}),
                lambda: service.releases.delete(db.session, self.who, row.id),
                lambda: self.verification(row),
                lambda: service.verifications.update(db.session, self.who, check.id, {}, today=TODAY),
                lambda: service.verifications.delete(db.session, self.who, check.id)):
                self.reject(operation, service.READ_ONLY)

    def test_permissions_web_mcp_no_decision_or_delete_and_fixed_parent(self):
        row = self.release()
        check = self.verification(row)
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, service.releases.get(db.session, who, row.id).id)
            self.assertIn(row, service.releases.list_(db.session, who))
            if role is RoleEnum.OPERATIVO:
                self.reject(lambda: service.releases.update(db.session, who, row.id, {}), error=PermissionDenied)
                self.reject(lambda: service.verifications.create(db.session, who,
                    CHECK | {'release_id': row.id}, today=TODAY), error=PermissionDenied)
            else:
                service.releases.update(db.session, who, row.id, {})
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                self.reject(lambda: service.verifications.update(db.session, who, check.id, {}, today=TODAY), error=PermissionDenied)
            if channel == 'mcp' or role is not RoleEnum.ADMINISTRADOR:
                self.reject(lambda: service.releases.delete(db.session, who, row.id), error=PermissionDenied)
        for key in ('status', *DECISION, 'concession_reason', 'concession_approver_id',
                    'customer_approval_date', 'customer_approval_means', 'id'):
            self.reject(lambda: service.releases.update(db.session, self.who, row.id, {key: 'x'}))
        another = self.release()
        self.reject(lambda: service.verifications.update(db.session, self.who, check.id,
            {'release_id': another.id}, today=TODAY))
        other = self.finished()
        self.reject(lambda: service.releases.update(db.session, self.who, row.id, {'order_id': other.id}))

    def test_draft_child_crud_parent_delete_audit_and_orders_unchanged(self):
        order = self.finished(customer_id=1, lot='B-1')
        before = audit.snapshot(order)
        from app.models import AuditLog
        logs = AuditLog.query.filter_by(entity_type='production_orders').count()
        row = self.release(order, notes='Notes')
        check = self.verification(row)
        service.verifications.update(db.session, self.who, check.id, {'result': 'nonconforming'}, today=TODAY)
        db.session.commit()
        self.assertEqual('nonconforming', service.verifications.get(db.session, self.who, check.id).result.name)
        service.verifications.delete(db.session, self.who, check.id)
        db.session.commit()
        self.verification(row)
        service.releases.delete(db.session, self.who, row.id)
        db.session.commit()
        self.assertEqual([], service.verifications.list_(db.session, self.who, release_id=row.id))
        self.assertEqual(before, audit.snapshot(self.service.orders.get(db.session, self.who, order.id)))
        self.assertEqual(logs, AuditLog.query.filter_by(entity_type='production_orders').count())

    def test_filters_cited_order_restrict_and_order_delete_message(self):
        row = self.release(self.finished(customer_id=1))
        other = self.release()
        for filters in ({'order_id': row.order_id}, {'customer_id': 1}):
            self.assertEqual([row.id], [r.id for r in service.releases.list_(db.session, self.who, **filters)])
        self.assertEqual([], service.releases.list_(db.session, self.who, status='rejected'))
        self.reject(lambda: self.service.orders.delete(db.session, self.who, row.order_id))
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        from app.models import ProductionOrder
        self.reject(lambda: db.session.execute(ProductionOrder.__table__.delete().where(
            ProductionOrder.id == row.order_id)), error=IntegrityError)
        self.assertEqual(other.id, service.releases.get(db.session, self.who, other.id).id)

    def test_warm_parent_refreshes_before_child_write(self):
        row = self.release()
        check = self.verification(row)
        original = service._lock
        def decide(session, record_id):
            session.execute(ProductRelease.__table__.update().where(ProductRelease.id == record_id)
                .values(status='released', **DECISION).execution_options(synchronize_session=False))
            return original(session, record_id)
        with patch.object(service, '_lock', side_effect=decide):
            self.reject(lambda: service.verifications.update(db.session, self.who, check.id,
                {'criterion': 'Wrong'}, today=TODAY), service.READ_ONLY)

    def test_sql_locks_refresh_and_postlock_person_validation(self):
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            order = self.finished()
            seen.clear()
            row = self.release(order)
            self.assertTrue(any('FROM production_orders' in sql and 'FOR UPDATE' in sql and refresh for sql, refresh in seen))
            check = self.verification(row)
            for operation in (lambda: service.releases.update(db.session, self.who, row.id, {'notes': 'New'}),
                lambda: service.verifications.update(db.session, self.who, check.id, {}, today=TODAY),
                lambda: service.verifications.delete(db.session, self.who, check.id),
                lambda: service.releases.delete(db.session, self.who, row.id)):
                seen.clear()
                operation()
                db.session.commit()
                self.assertTrue(any('FROM product_releases' in sql and 'FOR UPDATE' in sql and refresh for sql, refresh in seen))
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        row = self.release()
        original = service._lock
        def deactivate(session, record_id):
            locked = original(session, record_id)
            session.execute(Person.__table__.update().where(Person.id == 1).values(activo=False)
                .execution_options(synchronize_session=False))
            return locked
        with patch.object(service, '_lock', side_effect=deactivate):
            self.reject(lambda: self.verification(row))
