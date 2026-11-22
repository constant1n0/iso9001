"""Customer requirements: public service rules and unchanged rejection hashes."""
from datetime import date, timedelta
from itertools import product
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from register_routes import RegisterRoutesBase
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import clientes, people
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError

TODAY = date(2026, 10, 20)
ORDER = dict(code='C-1', customer_id=1, kind='order', received_date=TODAY,
             requirements='Supply containers')
REVIEW = dict(result='accepted', differences_resolved=True, undocumented=False,
              **{f'check_{c}': 'conforming' for c in 'abcde'})


class OrderCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.who = Actor(self.ids[RoleEnum.ADMINISTRADOR], 'Admin', RoleEnum.ADMINISTRADOR, 'web')
        clientes.create(db.session, self.who, {'nombre': 'Customer'})
        people.create(db.session, self.who, {'nombre': 'Reviewer', 'user_id': self.who.user_id})
        db.session.commit()

    @property
    def service(self):
        from app.services import customer_orders
        return customer_orders

    def create(self, **values):
        row = self.service.orders.create(db.session, self.who, ORDER | values, today=TODAY)
        db.session.commit()
        return row

    def latest(self, row):
        return self.service.revisions.list_(db.session, self.who, order_id=row.id)[0]

    def review(self, revision, **values):
        row = self.service.review(db.session, self.who, revision.id, REVIEW | values, today=TODAY)
        db.session.commit()
        return row

    def revision(self, order, **values):
        row = self.service.revisions.create(db.session, self.who, dict(order_id=order.id,
            requirements='Changed', change_reason='New needs', communicated_to='Customer and workshop') | values)
        db.session.commit()
        return row

    def refuse(self, operation, message=None, error=ValidationError):
        before = hashes()
        with self.assertRaises(error) as caught:
            operation()
        if message:
            self.assertEqual(message, str(caught.exception))
        if isinstance(caught.exception.__cause__, IntegrityError):
            db.session.rollback()  # a database refusal has already aborted the transaction
        self.assertEqual(before, hashes())
        db.session.rollback()


class OrderRules(OrderCase):
    def test_required_fields_received_date_and_unique_code(self):
        for key in ORDER:
            self.refuse(lambda: self.service.orders.create(db.session, self.who,
                {k: v for k, v in ORDER.items() if k != key}, today=TODAY),
                f'Faltan campos obligatorios: {key}.')
        for values in ({'received_date': TODAY + timedelta(days=1)}, {'customer_id': True},
                       {'customer_id': 999}, {'requirements': ' '}, {'code': ' '}, {'kind': 'other'}):
            self.refuse(lambda: self.create(**values))
        row = self.create()
        self.assertEqual('pending', row.status.name)
        self.refuse(lambda: self.create(), error=Conflict)

    def test_numbering_latest_editable_and_new_revision_gate(self):
        order = self.create()
        first = self.latest(order)
        self.assertEqual(1, first.number)
        self.refuse(lambda: self.revision(order))
        self.review(first, result='rejected', check_a='nonconforming', differences_resolved=False)
        for key in ('change_reason', 'communicated_to'):
            self.refuse(lambda: self.revision(order, **{key: ' '}))
        second = self.revision(order)
        self.assertEqual(2, second.number)
        self.assertEqual('pending', self.service.orders.get(db.session, self.who, order.id).status.name)
        self.refuse(lambda: self.service.revisions.update(db.session, self.who, first.id, {}))
        self.service.revisions.update(db.session, self.who, second.id, {'requirements': 'Updated'})
        db.session.commit()
        self.assertEqual('Updated', self.latest(order).requirements)

    def test_checklist_acceptance_confirmation_and_immutable_review(self):
        row = self.create()
        revision = self.latest(row)
        for key in REVIEW:
            self.refuse(lambda: self.service.review(db.session, self.who, revision.id,
                {k: v for k, v in REVIEW.items() if k != key}, today=TODAY))
        for values in ({'check_a': 'nonconforming'}, {'differences_resolved': False},
                       {'undocumented': True}, {'check_b': 'other'}, {'differences_resolved': None},
                       {'confirmation_date': TODAY}, {'confirmation_means': 'Phone'}):
            self.refuse(lambda: self.review(revision, **values))
        self.refuse(lambda: self.review(revision, undocumented=True,
            confirmation_date=TODAY + timedelta(days=1), confirmation_means='Phone'))
        self.review(revision, check_e='not_applicable', undocumented=True,
                    confirmation_date=TODAY, confirmation_means='Phone')
        reviewed = self.service.revisions.get(db.session, self.who, revision.id)
        self.assertEqual(('accepted', 1, TODAY),
            (reviewed.result.name, reviewed.reviewer_id, reviewed.review_date))
        self.assertEqual('accepted', self.service.orders.get(db.session, self.who, row.id).status.name)
        self.refuse(lambda: self.review(revision))
        for channel in ('web', 'mcp'):
            who = Actor(self.who.user_id, 'Admin', self.who.role, channel)
            self.refuse(lambda: self.service.revisions.update(db.session, who, revision.id, {}))
            self.refuse(lambda: self.service.orders.update(db.session, who, row.id, {}, today=TODAY))
        self.revision(row)
        self.assertEqual('pending', self.service.orders.get(db.session, self.who, row.id).status.name)

    def test_permissions_cancel_delete_and_citation_protection(self):
        row = self.create()
        revision = self.latest(row)
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = Actor(self.who.user_id, 'User', role, channel)
            self.assertEqual(row.id, self.service.orders.get(db.session, who, row.id).id)
            if role is RoleEnum.OPERATIVO:
                self.refuse(lambda: self.service.orders.update(db.session, who, row.id, {}, today=TODAY), error=PermissionDenied)
            else:
                self.service.orders.update(db.session, who, row.id, {}, today=TODAY)
            if role is not RoleEnum.ADMINISTRADOR or channel != 'web':
                self.refuse(lambda: self.service.review(db.session, who, revision.id, REVIEW, today=TODAY), error=PermissionDenied)
                self.refuse(lambda: self.service.cancel(db.session, who, row.id, reason='Stop'), error=PermissionDenied)
                self.refuse(lambda: self.service.orders.delete(db.session, who, row.id), error=PermissionDenied)
        self.refuse(lambda: clientes.delete(db.session, self.who, 1), error=Conflict)
        self.review(revision)
        self.refuse(lambda: people.delete(db.session, self.who, 1), people.STILL_REFERENCED, Conflict)
        self.refuse(lambda: self.service.orders.delete(db.session, self.who, row.id))
        for reason in (None, '', ' '):
            self.refuse(lambda: self.service.cancel(db.session, self.who, row.id, reason=reason))
        self.service.cancel(db.session, self.who, row.id, reason='Stop')
        db.session.commit()
        self.assertEqual('cancelled', self.service.orders.get(db.session, self.who, row.id).status.name)
        self.refuse(lambda: self.revision(row))
        draft = self.create(code='C-2')
        self.service.orders.delete(db.session, self.who, draft.id)
        db.session.commit()
        self.assertEqual([row.id], [r.id for r in self.service.orders.list_(db.session, self.who)])

    def test_locks_refresh_and_noop(self):
        row = self.create()
        revision = self.latest(row)
        before = hashes()
        self.service.orders.update(db.session, self.who, row.id, ORDER, today=TODAY)
        self.service.revisions.update(db.session, self.who, revision.id, {'requirements': ORDER['requirements']})
        db.session.commit()
        self.assertEqual(before, hashes())
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            self.review(revision)
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        self.assertIn('FROM customer_orders', seen[0][0])
        self.assertIn('FROM customer_order_revisions', seen[1][0])
        self.assertTrue(all('FOR UPDATE' in sql and refresh for sql, refresh in seen[:2]))
        self.assertTrue(all(refresh for _, refresh in seen))
        from app.models import CustomerOrder
        self.assertFalse(row.cancelled)
        db.session.execute(CustomerOrder.__table__.update().where(CustomerOrder.id == row.id)
            .values(cancelled=True, cancellation_reason='Concurrent').execution_options(synchronize_session=False))
        self.assertFalse(row.cancelled)  # warm identity must not defeat post-lock refresh
        self.refuse(lambda: self.revision(row))

    def test_additive_migration_constraints_and_legacy_preservation(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        from app.models import CustomerOrder, CustomerOrderRevision
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/a2c4e6f8b0d3_add_customer_orders.py'
        spec = importlib.util.spec_from_file_location('order_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('f1b3d5e7a9c2', migration.down_revision)
        db.metadata.drop_all(db.engine, tables=[CustomerOrderRevision.__table__, CustomerOrder.__table__])
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return [(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all()) for t in legacy]
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                values = {k: v for k, v in ORDER.items() if k != 'requirements'}
                connection.execute(CustomerOrder.__table__.insert().values(**values))
                connection.execute(CustomerOrderRevision.__table__.insert().values(order_id=1, number=1, requirements='Needs'))
                for values in ({'number': 0}, {'number': 2}, {'result': 'accepted'},
                               {'reviewer_id': 1}, {'note_a': 'Unreviewed'}, {'requirements': ' '}):
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(CustomerOrderRevision.__table__.update().values(**values))
                for table in ('customer_orders', 'customer_order_revisions'):
                    self.assertTrue(inspect(connection).get_pk_constraint(table)['name'])
                    for fk in inspect(connection).get_foreign_keys(table):
                        self.assertTrue(fk['name'])
                        self.assertEqual('SET NULL' if fk['constrained_columns'][0] in
                            ('created_by_id', 'updated_by_id') else 'RESTRICT', fk['options']['ondelete'])
                migration.downgrade()
                self.assertEqual(before, snapshot())
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
