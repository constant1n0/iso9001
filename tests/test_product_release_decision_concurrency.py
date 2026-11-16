"""PostgreSQL decisions serialize on the release and re-read a competing verdict."""
import os
from queue import Queue
import threading
import time
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from app.extensions import db
from app.models import User, RoleEnum
from app.services import audit, people, process_operations, production, product_release as service
from app.services.actor import Actor
from app.services.errors import ValidationError
from test_equipment_concurrency import _worker_backend
from test_product_release import CHECK, TODAY

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class DecisionContention(unittest.TestCase):
    def test_authorize_blocks_reject_then_refuses_stale_draft(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'release_decision_' + uuid4().hex
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
        who = Actor(1, 'Authority', RoleEnum.ADMINISTRADOR, 'web')
        with Session(engine) as session:
            session.add(User(id=1, username='authority', email='authority@example.test',
                             password='unused', role=RoleEnum.ADMINISTRADOR))
            session.flush()
            people.create(session, who, {'nombre': 'Owner'})
            people.create(session, who, {'nombre': 'Authority', 'user_id': 1})
            process_operations.create(session, who, {'proceso': 'Manufacturing'})
            production.orders.create(session, who, dict(code='O-1', kind='production',
                title='Product', process_id=1, owner_id=1))
            production.transition(session, who, 1, 'in_progress')
            production.transition(session, who, 1, 'finished')
            service.releases.create(session, who, {'order_id': 1})
            service.verifications.create(session, who, CHECK | {'release_id': 1}, today=TODAY)
            session.commit()
        backend, outcome = Queue(), Queue()
        done = threading.Event()
        def worker():
            try:
                with Session(engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        # Warm draft identity must not bypass the post-wait refresh;
                        # keep a strong reference so the identity map retains it.
                        warm = service.releases.get(session, who, 1)
                        assert warm.status.name == 'draft'
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        service.reject(session, who, 1, reason='Competing verdict', today=TODAY)
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
        with Session(engine) as holder:
            service.authorize(holder, who, 1, today=TODAY)
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
                holder.commit()
            finally:
                holder.rollback()
                thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(service.READ_ONLY, outcome.get(timeout=1))
        with Session(engine) as session:
            row = service.releases.get(session, who, 1)
            self.assertEqual(('released', 2, 1),
                             (row.status.name, row.decided_by_id, row.decided_by_user_id))
