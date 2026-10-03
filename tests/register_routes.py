"""Shared fixture for the HTML register route tests (defines no tests itself).

The app runs with the audit flush guard installed, so a web write that skips
its audit row fails with ``AuditGuardViolation``.
"""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap
from sqlalchemy import inspect as sa_inspect
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import AuditLog, RoleEnum, User
from app.services import audit
from app.services.actor import Actor

PASSWORD_HASH = generate_password_hash("StrongPassword123!")


class RegisterRoutesBase(unittest.TestCase):
    BASE = ""  # URL prefix of the register, e.g. "/capacitaciones"

    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(), email=f"{role.name.lower()}@example.com",
                                    password=PASSWORD_HASH, role=role))
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
            self.remove_guard = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        self.remove_guard()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, role: RoleEnum = RoleEnum.OPERATIVO) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])

    def flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def actions(self):
        with self.app.app_context():
            return [r.action for r in AuditLog.query.order_by(AuditLog.id)]

    def seed_with(self, module, values) -> int:
        """Create a record through its service, as production code would."""
        with self.app.app_context():
            created = module.create(db.session, Actor(1, "seed", RoleEnum.ADMINISTRADOR, "system"), values)
            db.session.commit()
            return sa_inspect(created).identity[0]

    def count(self, model) -> int:
        with self.app.app_context():
            return model.query.count()

    def fetch(self, model, record_id):
        with self.app.app_context():
            found = db.session.get(model, record_id)
            db.session.expunge(found)
            return found
