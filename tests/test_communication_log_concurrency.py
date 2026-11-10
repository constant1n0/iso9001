"""PostgreSQL row contention: serialise appends and validate after the lock wait.

Private schemas in the explicit disposable TEST_POSTGRES_URI isolate these tests.
pg_blocking_pids proves actual contention; elapsed time alone is not evidence.
"""
import os
from datetime import date, timedelta
from queue import Queue
import threading
import time
import unittest
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import audit, communication, people
from app.services.actor import Actor
from app.services.errors import ValidationError
from test_equipment_concurrency import _worker_backend
from test_communication_log import LOG, PLAN

URI = os.environ.get('TEST_POSTGRES_URI')
DAY = date(2020, 1, 2)
ADMIN = Actor(None, 'contention-test', RoleEnum.ADMINISTRADOR, 'cli')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class CommunicationLogContentionTestCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        self.schema = 'communication_race_' + uuid4().hex
        self.base_engine = create_engine(URI, connect_args={'connect_timeout': 5}, pool_timeout=5)
        self.addCleanup(self.base_engine.dispose)
        with self.base_engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self._drop_schema)
        self.engine = self.base_engine.execution_options(schema_translate_map={None: self.schema})
        with self.engine.begin() as connection:
            db.metadata.create_all(connection)
        with Session(self.engine) as session:
            remove = audit.install_audit_guard(session)
            try:
                person = people.create(session, ADMIN, {'nombre': 'Communicator'})
                plan = communication.items.create(session, ADMIN, PLAN | {'owner_id': person.id})
                self.plan_id, self.person_id = plan.id, person.id
                session.commit()
            finally:
                remove()

    def _drop_schema(self):
        with self.base_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            connection.execute(DropSchema(self.schema, cascade=True))

    def contend(self, following_date, accepted):
        from app.services import communication_log
        finished = threading.Event()
        backend, outcome = Queue(), Queue()
        data = LOG | {'plan_id': self.plan_id, 'communicator_id': self.person_id}
        final_next = date(2021, 3, 17)

        def append():
            result = None
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        connection = session.connection()
                        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                        connection.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(connection.scalar(text('SELECT pg_backend_pid()')))
                        row = communication_log.items.create(session, ADMIN, data | {
                            'date': following_date, 'evidence': 'Second evidence',
                            'next_date': final_next}, today=DAY)
                        identity = row.id
                        session.commit()
                        result = ('created', identity)
                    except ValidationError as exc:
                        session.rollback()
                        result = ('rejected', exc.message)
                    finally:
                        remove()
            except BaseException as exc:
                backend.put(exc)
                result = exc
            finally:
                outcome.put(result)
                finished.set()

        worker = threading.Thread(target=append, daemon=True)
        with Session(self.engine) as first:
            remove = audit.install_audit_guard(first)
            try:
                row = communication_log.items.create(first, ADMIN, data, today=DAY)
                first_id = row.id
                holder_pid = first.scalar(text('SELECT pg_backend_pid()'))
                worker.start()
                waiting_pid = _worker_backend(backend)
                deadline = time.monotonic() + 5
                blockers = []
                with self.base_engine.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'),
                                                   {'pid': waiting_pid})
                        if holder_pid in blockers or finished.is_set():
                            break
                        finished.wait(0.02)
                if finished.is_set():
                    result = outcome.get(timeout=1)
                    if isinstance(result, BaseException):
                        raise result
                self.assertIn(holder_pid, blockers, 'Append never waited for the plan lock')
                self.assertFalse(finished.is_set())
                with Session(self.engine) as reader:
                    self.assertEqual(0, communication_log.items.list_page(reader, ADMIN)[1])
                    self.assertEqual(PLAN['next_date'], communication.items.get(
                        reader, ADMIN, self.plan_id).next_date)
                first.commit()
            finally:
                first.rollback()
                if worker.ident is not None:
                    worker.join(timeout=12)
                remove()
        self.assertFalse(worker.is_alive(), 'Append worker did not finish')
        result = outcome.get(timeout=1)
        if isinstance(result, BaseException):
            raise result
        if accepted:
            self.assertEqual('created', result[0], result)
            self.assertGreater(result[1], first_id)
        else:
            self.assertEqual(('rejected', communication_log.BACKDATED), result)
        with Session(self.engine) as reader:
            history, total = communication_log.items.list_page(reader, ADMIN)
            self.assertEqual(2 if accepted else 1, total)
            self.assertEqual('Minutes M-1', next(row.evidence for row in history if row.id == first_id))
            self.assertEqual(final_next if accepted else LOG['next_date'],
                             communication.items.get(reader, ADMIN, self.plan_id).next_date)
            self.assertEqual(history[0].id, communication_log.latest(reader, self.plan_id).id)
            audits = list(reader.scalars(select(AuditLog).order_by(AuditLog.id)))
            self.assertEqual(6 if accepted else 4, len(audits))
            self.assertEqual(2 if accepted else 1, sum(row.action == 'update' for row in audits))
            self.assertEqual({'cli'}, {row.channel for row in audits})

    def test_backdated_append_waits_then_rejects_without_changing_plan_or_audit(self):
        self.contend(DAY - timedelta(days=1), accepted=False)

    def test_same_day_append_waits_then_updates_next_date_and_keeps_both_evidences(self):
        self.contend(DAY, accepted=True)
