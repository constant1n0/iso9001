"""PostgreSQL contention proves maintenance observes committed history and retirement."""
import os
import threading
import time
import unittest
from datetime import timedelta
from queue import Queue
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import audit
from app.services.actor import Actor
from app.services.errors import ValidationError
from test_infrastructure import DAY, NEXT, MASTER, ENTRY

ADMIN = Actor(None, 'contention-test', RoleEnum.ADMINISTRADOR, 'cli')

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class InfrastructureContentionTestCase(unittest.TestCase):
    def setUp(self):
        from app.services import infrastructure
        self.service = infrastructure
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        self.schema = 'infrastructure_race_' + uuid4().hex
        self.base = create_engine(URI, connect_args={'connect_timeout': 5}, pool_timeout=5)
        self.addCleanup(self.base.dispose)
        with self.base.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self.drop_schema)
        self.engine = self.base.execution_options(schema_translate_map={None: self.schema})
        with self.engine.begin() as connection:
            db.metadata.create_all(connection)
        with Session(self.engine) as session:
            item = self.service.items.create(session, ADMIN, MASTER)
            self.identity = item.id
            session.commit()

    def drop_schema(self):
        with self.base.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            connection.execute(DropSchema(self.schema, cascade=True))

    def contend(self, *, retire=False, backdated=False):
        signals, outcomes = Queue(), Queue()
        done = threading.Event()

        def append():
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        # Preload stale identity: FOR UPDATE must refresh it after waiting.
                        stale = self.service.items.get(session, ADMIN, self.identity)
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        signals.put(session.scalar(text('SELECT pg_backend_pid()')))
                        row = self.service.maintenance.create(session, ADMIN, ENTRY | dict(
                            elemento_id=self.identity, fecha=DAY - timedelta(days=1) if backdated else DAY,
                            fecha_proxima=NEXT + timedelta(days=1)), today=DAY)
                        self.assertEqual(NEXT + timedelta(days=1), stale.fecha_proxima)
                        session.commit()
                        result = ('created', row.id)
                    except ValidationError as exc:
                        session.rollback()
                        result = ('rejected', exc.message)
                    finally:
                        remove()
            except BaseException as exc:
                signals.put(exc)
                result = exc
            outcomes.put(result)
            done.set()

        worker = threading.Thread(target=append, daemon=True)
        with Session(self.engine) as first:
            remove = audit.install_audit_guard(first)
            try:
                if retire:
                    self.service.items.update(first, ADMIN, self.identity, {'retirado': True})
                else:
                    self.service.maintenance.create(first, ADMIN, ENTRY | dict(elemento_id=self.identity), today=DAY)
                holder = first.scalar(text('SELECT pg_backend_pid()'))
                worker.start()
                waiting = signals.get(timeout=5)
                if isinstance(waiting, BaseException):
                    raise waiting
                deadline = time.monotonic() + 5
                blockers = []
                with self.base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder in blockers or done.is_set():
                            break
                        done.wait(.02)
                self.assertIn(holder, blockers, 'Append did not wait for parent row lock')
                self.assertFalse(done.is_set())
                first.commit()
            finally:
                first.rollback()
                worker.join(timeout=12)
                remove()
        self.assertFalse(worker.is_alive())
        result = outcomes.get(timeout=1)
        if isinstance(result, BaseException):
            raise result
        if retire or backdated:
            self.assertEqual(('rejected', self.service.RETIRED if retire else self.service.BACKDATED), result)
        else:
            self.assertEqual('created', result[0])
        with Session(self.engine) as session:
            item = self.service.items.get(session, ADMIN, self.identity)
            expected_count = 0 if retire else 1 if backdated else 2
            self.assertEqual(expected_count, self.service.maintenance.list_page(session, ADMIN)[1])
            self.assertEqual(DAY if retire else NEXT if backdated else NEXT + timedelta(days=1), item.fecha_proxima)
            rows = list(session.scalars(select(AuditLog)))
            self.assertEqual(2 if retire else 3 if backdated else 5, len(rows))

    def test_retirement_serializes_and_refreshes_preloaded_parent(self):
        self.contend(retire=True)

    def test_new_history_serializes_and_refuses_backdate(self):
        self.contend(backdated=True)

    def test_same_day_history_serializes_and_applies_explicit_next_date(self):
        self.contend()
