"""PostgreSQL contention for DD3 plans/evidence versus project cancellation."""
import os
from queue import Queue
import threading
import time
import unittest

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import test_design_concurrency as parent_lock
from app.models import AuditLog
from app.services import audit, design, design_controls
from app.services.errors import ValidationError
from test_equipment_concurrency import _worker_backend


@unittest.skipUnless(parent_lock.URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class ControlContention(unittest.TestCase):
    """Reuse DD1's isolated private-schema fixture, never the public database."""

    setUp = parent_lock.DesignContention.setUp
    _drop_schema = parent_lock.DesignContention._drop_schema

    def contend(self, record=False):
        """Observe a real pg_blocking_pids wait, then refreshed cancellation refusal."""
        backend, outcome = Queue(), Queue()
        finished = threading.Event()
        who = parent_lock.WHO
        data = dict(project_id=self.project_id, kind='review', responsible_id=self.owner_id)
        register = design_controls.records if record else design_controls.plans
        data |= (dict(date=parent_lock.DAY, result='conforming', evidence='Minutes')
                 if record else dict(description='Review'))
        kwargs = {'today': parent_lock.DAY} if record else {}
        with Session(self.engine) as setup:
            design.stages.create(setup, who, dict(project_id=self.project_id,
                name='Plan', owner_id=self.owner_id), today=parent_lock.DAY)
            design.transition(setup, who, self.project_id, 'planificado')
            setup.commit()
            before = setup.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1))

        def worker():
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        register.create(session, who, data, **kwargs)
                        session.commit()
                        result = 'accepted'
                    except ValidationError as exc:
                        session.rollback()
                        result = exc.message
                    finally:
                        remove()
            except BaseException as exc:
                backend.put(exc)
                result = exc
            finally:
                outcome.put(result)
                finished.set()

        thread = threading.Thread(target=worker, daemon=True)
        with Session(self.engine) as first:
            remove = audit.install_audit_guard(first)
            try:
                design.transition(first, who, self.project_id, 'cancelado', cancellation_reason='Stop')
                holder = first.scalar(text('SELECT pg_backend_pid()'))
                thread.start()
                waiting = _worker_backend(backend)
                blockers = []
                deadline = time.monotonic() + 5
                with self.base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder in blockers or finished.is_set():
                            break
                        finished.wait(0.02)
                self.assertIn(holder, blockers, 'Control writer never waited for the project lock')
                self.assertFalse(finished.is_set())
                first.commit()
            finally:
                first.rollback()
                if thread.ident is not None:
                    thread.join(timeout=12)
                remove()
        self.assertFalse(thread.is_alive())
        result = outcome.get(timeout=1)
        if isinstance(result, BaseException):
            raise result
        self.assertEqual(design.READ_ONLY, result)
        with Session(self.engine) as reader:
            self.assertEqual([], register.list_(reader, who, project_id=self.project_id))
            self.assertEqual(before + 1, reader.scalar(select(AuditLog.id).order_by(
                AuditLog.id.desc()).limit(1)))

    def test_plan_append_waits_for_cancel_then_rejects_without_audit(self):
        self.contend()

    def test_record_append_waits_for_cancel_then_rejects_without_audit(self):
        self.contend(record=True)
