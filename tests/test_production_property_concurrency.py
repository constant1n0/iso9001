"""PostgreSQL custody writes wait and refresh a cached item after return."""
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
from app.services import audit, clientes, people, production_property as service
from app.services.errors import ValidationError
from test_design import actor
from test_equipment_concurrency import _worker_backend
from test_production_property import ITEM, TODAY

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class PropertyContention(unittest.TestCase):
    def test_update_waits_for_return_then_refreshes_cached_item(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'property_race_' + uuid4().hex
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
            clientes.create(session, who, {'nombre': 'Customer'})
            people.create(session, who, {'nombre': 'Custodian'})
            service.items.create(session, who, ITEM, today=TODAY)
            session.commit()
        backend, outcome = Queue(), Queue()
        ready, proceed, done = threading.Event(), threading.Event(), threading.Event()
        def worker():
            try:
                with Session(engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        cached = service.items.get(session, who, 1)
                        self.assertEqual('custody', cached.status.name)
                        ready.set()
                        self.assertTrue(proceed.wait(5))
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        service.items.update(session, who, 1, {'location': 'Wrong'}, today=TODAY)
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
                done.set()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        with Session(engine) as holder:
            service.transition(holder, who, 1, 'returned', today=TODAY,
                terminal_date=TODAY, terminal_note='Returned')
            holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
            proceed.set()
            try:
                waiting, blockers, deadline = _worker_backend(backend), [], time.monotonic() + 5
                with base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder_pid in blockers or done.wait(0.02):
                            break
                self.assertIn(holder_pid, blockers)
                holder.commit()
            finally:
                holder.rollback()
                thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.READ_ONLY, outcome.get(timeout=1))
        with Session(engine) as session:
            self.assertEqual('Workshop', service.items.get(session, who, 1).location)
            self.assertEqual(1, len(list(session.scalars(select(AuditLog).where(
                AuditLog.entity_type == 'customer_property', AuditLog.action == 'update')))))
