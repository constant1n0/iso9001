"""PostgreSQL review wins against a stale concurrent draft edit under row locks."""
import os
from queue import Queue
import threading
import time
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from test_customer_orders import ORDER, REVIEW, TODAY
from test_product_release_concurrency import assert_waiting_on_row
from test_equipment_concurrency import _worker_backend
from app.extensions import db
from app.models import Cliente, Person, User, RoleEnum
from app.services import customer_orders as service, audit
from app.services.actor import Actor
from app.services.errors import ValidationError

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class OrderContention(unittest.TestCase):
    def test_review_waiter_refreshes_parent_and_revision(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'customer_order_race_' + uuid4().hex
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
            connection.execute(Cliente.__table__.insert().values(id=1, nombre='Customer'))
            connection.execute(User.__table__.insert().values(id=1, username='Admin', password='unused', role=RoleEnum.ADMINISTRADOR))
            connection.execute(Person.__table__.insert().values(id=1, nombre='Reviewer', user_id=1))
        who = Actor(1, 'Admin', RoleEnum.ADMINISTRADOR, 'web')
        with Session(engine) as session:
            service.orders.create(session, who, ORDER, today=TODAY)
            session.commit()
        ready, backend, outcome = threading.Event(), Queue(), Queue()
        def worker():
            try:
                with Session(engine) as session:
                    service.revisions.get(session, who, 1)  # deliberately warm stale identity
                    ready.set()
                    start.wait(5)
                    remove = audit.install_audit_guard(session)
                    try:
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        service.revisions.update(session, who, 1, {'requirements': 'Stale edit'})
                        session.commit()
                        outcome.put('unexpected write')
                    except ValidationError as exc:
                        session.rollback()
                        outcome.put(str(exc))
                    finally:
                        remove()
            except BaseException as exc:
                backend.put(exc)
                outcome.put(exc)
        start = threading.Event()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with Session(engine) as holder:
                service.review(holder, who, 1, REVIEW, today=TODAY)
                holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
                start.set()
                waiting, blockers, deadline = _worker_backend(backend), [], time.monotonic() + 5
                with base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder_pid in blockers:
                            break
                        time.sleep(0.02)
                    self.assertIn(holder_pid, blockers)
                    assert_waiting_on_row(self, observer, waiting, 'customer_orders')
                holder.commit()
        finally:
            start.set()
            thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.READ_ONLY, outcome.get(timeout=1))
