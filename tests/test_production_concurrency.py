"""PostgreSQL contention proves cached orders are refreshed after a real lock wait."""
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
from app.models import AuditLog
from app.services import audit, people, process_operations, production
from app.services.errors import ValidationError
from test_design import actor
from test_equipment_concurrency import _worker_backend

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class ProductionContention(unittest.TestCase):
    def test_update_waits_for_cancel_then_refreshes_cached_parent(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'production_race_' + uuid4().hex
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
            process = process_operations.create(session, who, {'proceso': 'Manufacturing'})
            row = production.orders.create(session, who, dict(code='O-1', kind='production',
                title='Original', process_id=process.id_proceso))
            record_id = row.id
            session.commit()
        backend, outcome = Queue(), Queue()
        ready, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        def worker():
            try:
                with Session(engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        cached = production.orders.get(session, who, record_id)
                        ready.set()
                        self.assertTrue(proceed.wait(5))
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        production.orders.update(session, who, cached.id, {'title': 'Must not persist'})
                        session.commit()
                        result = 'accepted'
                    except ValidationError as exc:
                        session.rollback()
                        result = str(exc)
                    finally:
                        remove()
            except BaseException as exc:
                backend.put(exc)
                result = exc
            finally:
                outcome.put(result)
                finished.set()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        with Session(engine) as holder:
            production.transition(holder, who, record_id, 'cancelled', cancellation_reason='Stop')
            holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
            proceed.set()
            try:
                waiting = _worker_backend(backend)
                blockers = []
                deadline = time.monotonic() + 5
                with base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder_pid in blockers or finished.is_set():
                            break
                        finished.wait(0.02)
                self.assertIn(holder_pid, blockers)
                holder.commit()
            finally:
                holder.rollback()
                thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(production.READ_ONLY, outcome.get(timeout=1))
        with Session(engine) as session:
            row = production.orders.get(session, who, record_id)
            self.assertEqual(('cancelled', 'Original'), (row.state.name, row.title))
            self.assertEqual(2, len(list(session.scalars(select(AuditLog).where(
                AuditLog.entity_type == 'production_orders')))))
