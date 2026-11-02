"""Real PostgreSQL row contention for failed checks and subsequent recovery.

Each test uses a private schema in TEST_POSTGRES_URI (the disposable CI DB).
A worker must actually wait for the failure transaction's equipment-row lock:
pg_blocking_pids proves the wait, rather than elapsed time or SQL compilation.
No application behavior is mocked. Missing PostgreSQL is a skip locally, never
proof; CI must supply its explicit test URI.
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import date, timedelta
from queue import Queue
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import audit, equipment
from app.services.actor import Actor
from app.services.errors import ValidationError

URI = os.environ.get('TEST_POSTGRES_URI')
DAY = date(2026, 11, 2)
ADMIN = Actor(None, 'contention-test', RoleEnum.ADMINISTRADOR, 'cli')
PASS = dict(tipo='verificacion', resultado='apto', realizado_por='Laboratorio',
            referencia='Patrón trazable R-1', fecha_proxima=DAY + timedelta(days=365))
FAIL = dict(resultado='no_apto', fecha_proxima=None,
            evaluacion_impacto='Revisadas mediciones del lote A', acciones='Recomprobar lote A')


def _worker_backend(signals: Queue) -> int:
    """Return the backend PID or re-raise the original worker startup error."""
    signal = signals.get(timeout=5)
    if isinstance(signal, BaseException):
        raise signal
    return signal


class WorkerSignalTestCase(unittest.TestCase):
    def test_startup_exception_reaches_parent_without_becoming_queue_timeout(self) -> None:
        signals = Queue()
        cause = RuntimeError('worker startup failed')
        signals.put(cause)
        with self.assertRaises(RuntimeError) as caught:
            _worker_backend(signals)
        self.assertIs(cause, caught.exception)
        self.assertEqual('worker startup failed', str(caught.exception))
        signals.put(123)
        self.assertEqual(123, _worker_backend(signals))


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class EquipmentContentionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        self.schema = 'equipment_race_' + uuid4().hex
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
                item = equipment.equipment.create(session, ADMIN, dict(codigo='EQ-1', nombre='Balanza'))
                self.equipment_id = item.id
                equipment.checks.create(session, ADMIN,
                    PASS | dict(equipo_id=item.id, fecha=DAY - timedelta(days=1)), today=DAY)
                session.commit()
            finally:
                remove()

    def _drop_schema(self) -> None:
        with self.base_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            connection.execute(DropSchema(self.schema, cascade=True))

    def _contended_recovery(self, recovery_date: date, accepted: bool) -> None:
        attempted, finished = threading.Event(), threading.Event()
        backend: Queue = Queue()
        outcome: Queue = Queue()

        def recover() -> None:
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    connection = session.connection()

                    def observe_lock(conn, cursor, statement, parameters, context, many):
                        if 'FOR UPDATE' in statement and 'equipos_medicion' in statement:
                            attempted.set()

                    event.listen(connection, 'before_cursor_execute', observe_lock)
                    try:
                        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                        connection.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(connection.scalar(text('SELECT pg_backend_pid()')))
                        row = equipment.checks.create(session, ADMIN,
                            PASS | dict(equipo_id=self.equipment_id, fecha=recovery_date), today=DAY)
                        identity = row.id
                        session.commit()
                        result = ('created', identity)
                    except ValidationError as exc:
                        session.rollback()
                        result = ('rejected', exc.message)
                    finally:
                        event.remove(connection, 'before_cursor_execute', observe_lock)
                        remove()
            except BaseException as exc:
                backend.put(exc)  # wake the parent even when startup fails before a PID
                result = exc
            finally:
                outcome.put(result)  # publish success only after cleanup also succeeded
                finished.set()

        worker = threading.Thread(target=recover, daemon=True)
        with Session(self.engine) as first:
            remove = audit.install_audit_guard(first)
            try:
                failure = equipment.checks.create(first, ADMIN,
                    PASS | FAIL | dict(equipo_id=self.equipment_id, fecha=DAY), today=DAY)
                failure_id = failure.id
                holder_pid = first.scalar(text('SELECT pg_backend_pid()'))
                worker.start()
                waiting_pid = _worker_backend(backend)
                self.assertTrue(attempted.wait(3), 'Recovery did not attempt the row lock')
                deadline = time.monotonic() + 5
                blockers = []
                with self.base_engine.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting_pid})
                        if holder_pid in blockers or finished.is_set():
                            break
                        finished.wait(0.02)
                self.assertIn(holder_pid, blockers, 'Recovery never waited for the failure transaction')
                self.assertFalse(finished.is_set())
                # Other readers still see only the committed prior successful check.
                with Session(self.engine) as reader:
                    item = equipment.equipment.get(reader, ADMIN, self.equipment_id)
                    self.assertEqual('apto', equipment.status(reader, item, today=DAY))
                    self.assertEqual(1, equipment.checks.list_page(reader, ADMIN)[1])
                first.commit()
            finally:
                first.rollback()  # also releases the lock after a failed assertion
                if worker.ident is not None:
                    worker.join(timeout=12)
                remove()
        self.assertFalse(worker.is_alive(), 'Recovery worker did not finish')
        result = outcome.get(timeout=1)
        if isinstance(result, BaseException):
            raise result
        if accepted:
            self.assertEqual('created', result[0], result)
            self.assertGreater(result[1], failure_id)
        else:
            self.assertEqual(('rejected', equipment.BACKDATED), result)
        with Session(self.engine) as reader:
            item = equipment.equipment.get(reader, ADMIN, self.equipment_id)
            self.assertEqual('apto' if accepted else 'no_apto', equipment.status(reader, item, today=DAY))
            history, total = equipment.checks.list_page(reader, ADMIN)
            self.assertEqual(3 if accepted else 2, total)
            self.assertEqual('no_apto', next(row.resultado.name for row in history if row.id == failure_id))
            self.assertEqual(history[0].id, equipment.latest(reader, item.id).id)
            rows = list(reader.scalars(select(AuditLog).order_by(AuditLog.id)))
            self.assertEqual(4 if accepted else 3, len(rows))
            self.assertEqual({'create'}, {row.action for row in rows})
            self.assertEqual({'cli'}, {row.channel for row in rows})

    def test_backdated_recovery_waits_then_refuses_to_hide_committed_failure(self) -> None:
        self._contended_recovery(DAY - timedelta(days=1), accepted=False)

    def test_new_success_waits_then_recovers_without_erasing_failed_evidence(self) -> None:
        self._contended_recovery(DAY, accepted=True)
