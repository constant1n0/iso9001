"""PostgreSQL authorization waits for an order owner change and refreshes it."""
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
from app.models import AuditLog, ProductionOrder, RoleEnum, User
from app.services import audit, people, process_operations, production
from test_design import actor, TODAY
from test_equipment_concurrency import _worker_backend

URI = os.environ.get('TEST_POSTGRES_URI')


@unittest.skipUnless(URI or os.environ.get('CI'), 'TEST_POSTGRES_URI is not set')
class ChangeContention(unittest.TestCase):
    def test_authorization_waits_and_refreshes_every_order_owner(self):
        from app.services import production_changes
        from app.services.errors import ValidationError
        self.assertTrue(URI, 'CI must provide TEST_POSTGRES_URI')
        schema = 'production_changes_race_' + uuid4().hex
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
            user = User(username='Approver', password='unused', role=RoleEnum.ADMINISTRADOR)
            session.add(user)
            session.flush()
            people.create(session, who, {'nombre': 'Owner'})
            approver = people.create(session, who, {'nombre': 'Approver', 'user_id': user.id})
            admin = type(who)(user.id, 'Approver', RoleEnum.ADMINISTRADOR, 'web')
            process_operations.create(session, who, {'proceso': 'Manufacturing'})
            for n in (1, 2):
                production.orders.create(session, who, dict(code=f'O-{n}', kind='production',
                    title='Order', process_id=1, owner_id=1))
            production_changes.changes.create(session, who, dict(reason='Reason',
                description='Change', actions='Act', review='Reviewed', order_ids=[2, 1]))
            session.commit()
            approver_id = approver.id
        backend, outcome = Queue(), Queue()
        ready, proceed, done = threading.Event(), threading.Event(), threading.Event()
        def worker():
            try:
                with Session(engine) as session:
                    remove = audit.install_audit_guard(session)
                    try:
                        cached = production_changes.changes.get(session, admin, 1)
                        self.assertEqual([1, 1], [o.owner_id for o in cached.orders])
                        ready.set()
                        self.assertTrue(proceed.wait(5))
                        session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        backend.put(session.scalar(text('SELECT pg_backend_pid()')))
                        production_changes.authorize(session, admin, 1, today=TODAY)
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
            production.orders.update(holder, who, 2, {'owner_id': approver_id})
            holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
            proceed.set()
            try:
                waiting = _worker_backend(backend)
                blockers, deadline = [], time.monotonic() + 5
                with base.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting})
                        if holder_pid in blockers or done.is_set():
                            break
                        done.wait(0.02)
                self.assertIn(holder_pid, blockers)
                holder.commit()
            finally:
                holder.rollback()
                thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertEqual(production_changes.SEPARATION, outcome.get(timeout=1))
        with Session(engine) as session:
            self.assertEqual('proposed', production_changes.changes.get(session, who, 1).state.name)
            logs = list(session.scalars(select(AuditLog).where(
                AuditLog.entity_type == 'production_changes', AuditLog.action == 'update')))
            self.assertEqual([], logs)
