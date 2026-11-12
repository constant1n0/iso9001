"""PostgreSQL-only parent-lock contention, isolated in a disposable private schema."""
import os
from datetime import date
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
from app.services import audit, design, people
from app.services.actor import Actor
from app.services.errors import ValidationError
from test_equipment_concurrency import _worker_backend

URI = os.environ.get('TEST_POSTGRES_URI')
WHO = Actor(None, 'design-contention', RoleEnum.ADMINISTRADOR, 'web')
DAY = date(2026, 1, 1)


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class DesignContention(unittest.TestCase):
    def setUp(self):
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        self.schema = 'design_race_' + uuid4().hex
        self.base = create_engine(URI, connect_args={'connect_timeout': 5}, pool_timeout=5)
        self.addCleanup(self.base.dispose)
        with self.base.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self._drop_schema)
        self.engine = self.base.execution_options(schema_translate_map={None: self.schema})
        with self.engine.begin() as connection:
            db.metadata.create_all(connection)
        with Session(self.engine) as session:
            person = people.create(session, WHO, {'nombre': 'Owner'})
            project = design.projects.create(session, WHO, {
                'title': 'Design', 'description': 'Product', 'owner_id': person.id})
            self.project_id, self.owner_id = project.id, person.id
            session.commit()

    def _drop_schema(self):
        with self.base.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            connection.execute(DropSchema(self.schema, cascade=True))

    def contend(self, cancel_first):
        """Prove a real blocked backend, then check validation after the lock wait."""
        backend, outcome = Queue(), Queue()
        finished = threading.Event()
        stage_data = {'project_id': self.project_id, 'name': 'Plan', 'owner_id': self.owner_id}

        def worker():
            try:
                with Session(self.engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        if cancel_first:
                            design.stages.create(session, WHO, stage_data, today=DAY)
                        else:
                            design.transition(session, WHO, self.project_id, 'planificado')
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
                if cancel_first:
                    design.transition(first, WHO, self.project_id, 'cancelado', cancellation_reason='Stop')
                else:
                    design.stages.create(first, WHO, stage_data, today=DAY)
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
                self.assertIn(holder, blockers, 'Writer never waited for the parent lock')
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
        self.assertEqual(design.READ_ONLY if cancel_first else 'accepted', result)
        with Session(self.engine) as reader:
            project = design.projects.get(reader, WHO, self.project_id)
            stages = design.stages.list_(reader, WHO, project_id=self.project_id)
            self.assertEqual('cancelado' if cancel_first else 'planificado', project.state.name)
            self.assertEqual(0 if cancel_first else 1, len(stages))
            rows = list(reader.scalars(select(AuditLog)))
            self.assertEqual(3 if cancel_first else 4, len(rows))

    def test_stage_append_waits_for_cancel_then_rejects_without_audit(self):
        self.contend(cancel_first=True)

    def test_planning_waits_for_stage_append_then_observes_the_stage(self):
        self.contend(cancel_first=False)
