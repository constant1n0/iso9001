"""PostgreSQL DD4 contention with warm identity maps and observed row-lock waits."""
import os
from queue import Queue
import threading
import time
import unittest

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import test_design_concurrency as parent_lock
from test_design_changes import CHANGE
from test_equipment_concurrency import _worker_backend
from app.models import AuditLog, DesignProject, RoleEnum, User
from app.services import audit, design, design_changes, design_controls, design_io, people
from app.services.actor import Actor
from app.services.errors import ValidationError


@unittest.skipUnless(parent_lock.URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class DesignCompletionContention(unittest.TestCase):
    """Private schema and bounded sessions, never public production data."""

    _drop_schema = parent_lock.DesignContention._drop_schema

    def setUp(self):
        parent_lock.DesignContention.setUp(self)
        with Session(self.engine) as session:
            user = User(username='approver', password='unused', role=RoleEnum.ADMINISTRADOR)
            session.add(user)
            session.flush()
            self.admin = Actor(user.id, 'Approver', RoleEnum.ADMINISTRADOR, 'web')
            people.create(session, parent_lock.WHO, {'nombre': 'Approver', 'user_id': user.id})
            design.stages.create(session, parent_lock.WHO, dict(project_id=self.project_id,
                name='Plan', owner_id=self.owner_id), today=parent_lock.DAY)
            design.transition(session, parent_lock.WHO, self.project_id, 'planificado')
            design.transition(session, parent_lock.WHO, self.project_id, 'en_curso')
            output = design_io.outputs.create(session, parent_lock.WHO, dict(
                project_id=self.project_id, kind='other', text='Output', acceptance_criteria='Works'))
            self.output_id = output.id
            design_changes.approve_output(session, self.admin, output.id, today=parent_lock.DAY)
            session.commit()

    def contend(self, proposal_first=False):
        backend, outcome = Queue(), Queue()
        warm, start, finished = threading.Event(), threading.Event(), threading.Event()
        def worker():
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        # Cache the old in-progress parent before the other transaction.
                        cached = session.get(DesignProject, self.project_id)
                        warm.set()
                        self.assertTrue(start.wait(5))
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        if proposal_first:
                            design.complete(session, self.admin, cached.id, today=parent_lock.DAY)
                        else:
                            design_controls.records.create(session, parent_lock.WHO, dict(
                                project_id=cached.id, kind='review', responsible_id=self.owner_id,
                                date=parent_lock.DAY, result='conforming', evidence='Late minutes'),
                                today=parent_lock.DAY)
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
        thread.start()
        self.assertTrue(warm.wait(5))
        with Session(self.engine) as holder:
            remove = audit.install_audit_guard(holder)
            try:
                if proposal_first:
                    design_changes.changes.create(holder, parent_lock.WHO, CHANGE | dict(
                        project_id=self.project_id, output_ids=[self.output_id]))
                else:
                    design.complete(holder, self.admin, self.project_id, today=parent_lock.DAY)
                expected_audits = holder.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1))
                pid = holder.scalar(text('SELECT pg_backend_pid()'))
                start.set()
                waiting = _worker_backend(backend)
                blockers = []
                deadline = time.monotonic() + 5
                with self.base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if pid in blockers or finished.is_set():
                            break
                        finished.wait(0.02)
                self.assertIn(pid, blockers, 'DD4 writer did not wait for the project lock')
                self.assertFalse(finished.is_set())
                holder.commit()
            finally:
                start.set()
                holder.rollback()
                thread.join(timeout=12)
                remove()
        self.assertFalse(thread.is_alive())
        result = outcome.get(timeout=1)
        if isinstance(result, BaseException):
            raise result
        self.assertEqual('Resuelve todos los cambios propuestos antes de completar.'
                         if proposal_first else 'Un proyecto completado es de solo lectura.', result)
        with Session(self.engine) as reader:
            self.assertEqual(expected_audits, reader.scalar(select(AuditLog.id).order_by(
                AuditLog.id.desc()).limit(1)))
            self.assertEqual([], design_controls.records.list_(reader, parent_lock.WHO,
                                                               project_id=self.project_id))

    def test_completion_blocks_late_control_and_refreshes_warm_parent(self):
        self.contend()

    def test_proposal_blocks_completion_until_it_can_observe_the_new_change(self):
        self.contend(proposal_first=True)
