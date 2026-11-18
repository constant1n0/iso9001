"""PostgreSQL duplicate draft creation waits on the order and rechecks uniqueness."""
import os
from queue import Queue
import threading
import time
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from app.extensions import db
from app.models import ProductRelease
from app.services import audit, process_operations, production, product_release as service
from app.services.errors import Conflict
from test_design import actor
from test_equipment_concurrency import _worker_backend

URI = os.environ.get('TEST_POSTGRES_URI')


def assert_waiting_on_row(test: unittest.TestCase, observer, pid: int,
                          table: str) -> None:
    """Distinguish the intended row lock from FK or unique-index contention.

    pg_stat_activity truncates query text (track_activity_query_size), so the
    FROM and FOR UPDATE clauses of a wide SELECT are not visible there. A
    waiter on a SELECT ... FOR UPDATE row lock holds the tuple lock on that
    table in AccessExclusiveLock mode; FK checks (FOR KEY SHARE) take it in
    AccessShareLock mode and unique-index waits take no tuple lock at all.
    """
    activity = observer.execute(text(
        'SELECT query, wait_event_type FROM pg_stat_activity WHERE pid = :pid'),
        {'pid': pid}).one()
    test.assertEqual('Lock', activity.wait_event_type)
    test.assertRegex(activity.query, r'^\s*SELECT\b')
    test.assertIn(f'{table}.id', activity.query)
    modes = observer.scalars(text(
        "SELECT l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
        "WHERE l.pid = :pid AND l.locktype = 'tuple' AND c.relname = :table"),
        {'pid': pid, 'table': table}).all()
    test.assertIn('AccessExclusiveLock', modes)


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class ReleaseContention(unittest.TestCase):
    def test_concurrent_creation_waits_then_refuses_duplicate(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'release_race_' + uuid4().hex
        base = create_engine(URI, connect_args={'connect_timeout': 5}, pool_timeout=5)
        self.addCleanup(base.dispose)
        with base.begin() as connection:
            connection.execute(CreateSchema(schema))
        def cleanup():
            with base.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(DropSchema(schema, cascade=True))
        self.addCleanup(cleanup)
        engine = base.execution_options(schema_translate_map={None: schema})
        with engine.begin() as connection:
            db.metadata.create_all(connection)
        who = actor()
        with Session(engine) as session:
            process_operations.create(session, who, {'proceso': 'Manufacturing'})
            production.orders.create(session, who, dict(code='O-1', kind='production', title='Product', process_id=1))
            production.transition(session, who, 1, 'in_progress')
            production.transition(session, who, 1, 'finished')
            session.commit()
        backend, outcome = Queue(), Queue()
        done = threading.Event()
        def worker():
            try:
                with Session(engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        service.releases.create(session, who, {'order_id': 1})
                        session.commit()
                        result = 'accepted'
                    except Conflict as exc:
                        session.rollback()
                        result = str(exc)
                    finally:
                        remove()
            except BaseException as exc:
                backend.put(exc)
                result = exc
            finally:
                outcome.put(result)
                done.set()
        with Session(engine) as holder:
            service.releases.create(holder, who, {'order_id': 1})
            holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                waiting, blockers, deadline = _worker_backend(backend), [], time.monotonic() + 5
                with base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder_pid in blockers or done.wait(0.02):
                            break
                    self.assertIn(holder_pid, blockers)
                    assert_waiting_on_row(self, observer, waiting, 'production_orders')
                self.assertFalse(done.is_set())
                holder.commit()
            finally:
                holder.rollback()
                thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.SPEC.conflict, outcome.get(timeout=1))
        with Session(engine) as session:
            self.assertEqual(1, len(list(session.scalars(select(ProductRelease)))))
