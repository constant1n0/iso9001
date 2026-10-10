"""The web authorization adapter: ``require_permission`` and the ``can()`` template global."""

from __future__ import annotations

import importlib.util
import unittest

import test_auth_bootstrap as bootstrap
from flask import render_template_string
from flask_login import login_required, login_user
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import RoleEnum, User

DENIED = ("danger", "No tienes permiso para acceder a esta página.")


class WebAuthorizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(), email=f"{role.name.lower()}@example.com",
                                    password=generate_password_hash("StrongPassword123!"), role=role))
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _client(self, role: RoleEnum | None):
        client = self.app.test_client()
        if role is not None:
            with client.session_transaction() as session:
                session["_user_id"] = str(self.ids[role])
                session["_fresh"] = True
        return client

    def _guarded(self, action, resource, name="probe"):
        from app.utils.permissions import require_permission

        calls = []

        def view():
            calls.append(1)
            return "reached"

        view.__name__ = name
        guarded = login_required(require_permission(action, resource)(view))
        self.app.add_url_rule(f"/_probe/{name}", name, guarded, methods=["GET", "POST"])
        return calls

    def test_the_guard_lets_permitted_roles_through_and_redirects_the_rest(self) -> None:
        calls = self._guarded("delete", "nonconformities")
        admin = self._client(RoleEnum.ADMINISTRADOR).get("/_probe/probe")
        self.assertEqual((200, b"reached"), (admin.status_code, admin.data))
        client = self._client(RoleEnum.OPERATIVO)
        denied = client.get("/_probe/probe")
        self.assertEqual(302, denied.status_code)
        self.assertTrue(denied.headers["Location"].endswith("/dashboard/"))
        with client.session_transaction() as session:
            self.assertIn(DENIED, session["_flashes"])
        self.assertEqual([1], calls)  # the view never ran for the denied role

    def test_the_guard_applies_to_every_method_and_to_anonymous_users_logs_in_first(self) -> None:
        self._guarded("create", "audits")
        auditor = self._client(RoleEnum.AUDITOR)
        self.assertEqual(200, auditor.post("/_probe/probe").status_code)
        operativo = self._client(RoleEnum.OPERATIVO)
        self.assertEqual(302, operativo.post("/_probe/probe").status_code)
        anonymous = self._client(None).get("/_probe/probe")
        self.assertEqual(302, anonymous.status_code)
        self.assertIn("/login", anonymous.headers["Location"])

    def test_an_unknown_action_or_resource_fails_at_decoration_time(self) -> None:
        from app.utils.permissions import require_permission

        for action, resource in (("destroy", "audits"), ("read", "nope")):
            with self.subTest(action=action, resource=resource):
                with self.assertRaises(ValueError):
                    require_permission(action, resource)

    def _render(self, role: RoleEnum | None, template: str) -> str:
        with self.app.test_request_context():
            if role is not None:
                login_user(db.session.get(User, self.ids[role]))
            return render_template_string(template)

    def test_can_follows_the_policy_for_the_logged_in_user(self) -> None:
        template = "{{ can('delete', 'nonconformities') }}/{{ can('read', 'audits') }}/{{ can('read', 'documents') }}"
        with self.app.app_context():
            self.assertEqual("True/True/True", self._render(RoleEnum.ADMINISTRADOR, template))
            self.assertEqual("False/True/True", self._render(RoleEnum.AUDITOR, template))
            self.assertEqual("False/False/True", self._render(RoleEnum.OPERATIVO, template))

    def test_can_is_false_when_nobody_is_logged_in_and_rejects_typos_loudly(self) -> None:
        with self.app.app_context():
            self.assertEqual("False", self._render(None, "{{ can('read', 'audits') }}"))
            with self.assertRaises(ValueError):
                self._render(RoleEnum.ADMINISTRADOR, "{{ can('read', 'auditoria') }}")

    def test_the_old_role_decorator_module_is_gone(self) -> None:
        self.assertIsNone(importlib.util.find_spec("app.utils.decorators"))


if __name__ == "__main__":
    unittest.main()
