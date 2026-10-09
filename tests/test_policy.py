"""Table-driven tests for the central permission policy (the approved matrix)."""

from __future__ import annotations

import ast
import unittest

from app.models import RoleEnum

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
ALL_ROLES = frozenset(RoleEnum)
ADMIN_ONLY = frozenset({ADMIN})
ADMIN_AUDITOR = frozenset({ADMIN, AUDITOR})

NOBODY = frozenset()

# resource name -> (roles that read, roles that create/update, roles that delete)
# The approved matrix (decision D1), written by hand; deliberately NOT derived
# from the policy module under test.
EXPECTED = {
    "NONCONFORMITIES": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
    "IMPROVEMENTS": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
    "CUSTOMER_SATISFACTION": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
    "TRAINING": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
    "INTERESTED_PARTIES": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
    "AUDITS": (ADMIN_AUDITOR, ADMIN_AUDITOR, ADMIN_ONLY),
    "DOCUMENTS": (ADMIN_ONLY, ADMIN_ONLY, ADMIN_ONLY),
    "AUDIT_INDICATORS": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "ROLES_RESPONSIBILITIES": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "RISKS_OPPORTUNITIES": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "TRAINING_RESOURCES": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "PROCESS_OPERATIONS": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "USERS": (ADMIN_AUDITOR, ADMIN_ONLY, NOBODY),
    "AUDIT_LOG": (ADMIN_AUDITOR, ADMIN_ONLY, NOBODY),
    "API_TOKENS": (ADMIN_ONLY, ADMIN_ONLY, NOBODY),
    "PEOPLE": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "COMPETENCE": (ALL_ROLES, ADMIN_AUDITOR, ADMIN_ONLY),
    "CORRECTIVE_ACTIONS": (ALL_ROLES, ALL_ROLES, ADMIN_ONLY),
}

FORBIDDEN_MODULES = ("flask", "flask_login", "werkzeug.local", "app.routes")
FORBIDDEN_NAMES = frozenset({"current_user", "request"})


def _is_forbidden(module: str) -> bool:
    return any(module == m or module.startswith(f"{m}.") for m in FORBIDDEN_MODULES)


def forbidden_framework_use(source: str) -> list[str]:
    """Forbidden modules imported and request globals referenced by ``source``.

    Parses the syntax tree, so comments, docstrings and look-alike names such
    as ``request_id`` never count. Relative imports are resolved against the
    ``app.services`` package.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if _is_forbidden(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ["app", "services"][: 2 - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if _is_forbidden(module):
                found.append(module)
            found += [a.name for a in node.names if a.name in FORBIDDEN_NAMES]
            found += [
                f"{module}.{a.name}"
                for a in node.names
                if _is_forbidden(f"{module}.{a.name}") and not _is_forbidden(module)
            ]
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(node.id)
    return found


def make_actor(role, channel="web", scopes=None):
    from app.services.actor import Actor

    return Actor(user_id=1, label="tester", role=role, channel=channel, scopes=scopes)


class PolicyMatrixTestCase(unittest.TestCase):
    def test_every_resource_is_covered_by_the_table(self) -> None:
        from app.services.policy import Resource

        self.assertEqual(set(EXPECTED), {r.name for r in Resource})

    def test_matrix_for_every_role_resource_and_action(self) -> None:
        from app.services.policy import Action, Resource, can

        for name, (read_roles, write_roles, delete_roles) in EXPECTED.items():
            resource = Resource[name]
            for role in RoleEnum:
                actor = make_actor(role)
                for action in Action:
                    allowed_roles = {
                        Action.READ: read_roles,
                        Action.DELETE: delete_roles,
                    }.get(action, write_roles)
                    with self.subTest(resource=name, role=role.name, action=action):
                        self.assertEqual(
                            role in allowed_roles, can(actor, action, resource)
                        )

    def test_require_raises_permission_denied_only_when_can_is_false(self) -> None:
        from app.services.errors import PermissionDenied
        from app.services.policy import Action, Resource, require

        require(make_actor(ADMIN), Action.DELETE, Resource.NONCONFORMITIES)
        with self.assertRaises(PermissionDenied):
            require(make_actor(OPERATIVO), Action.DELETE, Resource.NONCONFORMITIES)

    def test_mcp_channel_is_never_allowed_to_delete(self) -> None:
        from app.services.policy import Action, Resource, can

        for resource in Resource:
            with self.subTest(resource=resource.name):
                self.assertFalse(
                    can(make_actor(ADMIN, channel="mcp"), Action.DELETE, resource)
                )
        # The same actor on the web channel may still delete.
        self.assertTrue(
            can(make_actor(ADMIN), Action.DELETE, Resource.NONCONFORMITIES)
        )

    def test_mcp_channel_keeps_non_delete_access(self) -> None:
        from app.services.policy import Action, Resource, can

        actor = make_actor(OPERATIVO, channel="mcp")
        self.assertTrue(can(actor, Action.READ, Resource.NONCONFORMITIES))
        self.assertTrue(can(actor, Action.UPDATE, Resource.NONCONFORMITIES))

    def test_scopes_intersect_with_the_role(self) -> None:
        from app.services.policy import Action, Resource, can

        read_only = frozenset({"read"})
        write_only = frozenset({"write"})
        both = frozenset({"read", "write"})
        cases = [
            # (role, scopes, action, resource, expected)
            (ADMIN, read_only, Action.READ, Resource.DOCUMENTS, True),
            (ADMIN, read_only, Action.CREATE, Resource.DOCUMENTS, False),
            (ADMIN, read_only, Action.UPDATE, Resource.DOCUMENTS, False),
            (ADMIN, read_only, Action.DELETE, Resource.DOCUMENTS, False),
            (ADMIN, write_only, Action.READ, Resource.DOCUMENTS, False),
            (ADMIN, write_only, Action.DELETE, Resource.DOCUMENTS, True),
            (ADMIN, frozenset(), Action.READ, Resource.DOCUMENTS, False),
            (ADMIN, both, Action.UPDATE, Resource.DOCUMENTS, True),
            # Scopes never widen the role.
            (OPERATIVO, both, Action.READ, Resource.DOCUMENTS, False),
            (OPERATIVO, both, Action.DELETE, Resource.NONCONFORMITIES, False),
            (OPERATIVO, both, Action.CREATE, Resource.NONCONFORMITIES, True),
            (OPERATIVO, both, Action.CREATE, Resource.RISKS_OPPORTUNITIES, False),
        ]
        for role, scopes, action, resource, expected in cases:
            with self.subTest(role=role.name, scopes=sorted(scopes), action=action):
                self.assertEqual(
                    expected, can(make_actor(role, scopes=scopes), action, resource)
                )

    def test_nobody_may_delete_users_or_the_audit_log(self) -> None:
        from app.services.policy import Action, Resource, can

        for resource in (Resource.USERS, Resource.AUDIT_LOG):
            for role in RoleEnum:
                with self.subTest(resource=resource.name, role=role.name):
                    self.assertFalse(can(make_actor(role, channel="system"), Action.DELETE, resource))

    def test_the_mcp_channel_can_never_manage_api_tokens(self) -> None:
        from app.services.policy import Action, Resource, can

        actor = make_actor(ADMIN, channel="mcp", scopes=frozenset({"read", "write"}))
        for action in Action:
            with self.subTest(action=action):
                self.assertFalse(can(actor, action, Resource.API_TOKENS))
        self.assertTrue(can(make_actor(ADMIN, channel="cli"), Action.CREATE, Resource.API_TOKENS))

    def test_mcp_with_full_scopes_still_cannot_delete(self) -> None:
        from app.services.policy import Action, Resource, can

        actor = make_actor(ADMIN, channel="mcp", scopes=frozenset({"read", "write"}))
        self.assertFalse(can(actor, Action.DELETE, Resource.NONCONFORMITIES))


class ActorTestCase(unittest.TestCase):
    def test_actor_is_frozen(self) -> None:
        import dataclasses

        actor = make_actor(ADMIN)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            actor.role = OPERATIVO  # type: ignore[misc]

    def test_unknown_channel_is_rejected(self) -> None:
        from app.services.actor import Actor

        with self.assertRaises(ValueError):
            Actor(user_id=1, label="x", role=ADMIN, channel="smoke", scopes=None)

    def test_from_user_takes_the_channel_explicitly(self) -> None:
        from app.models import User
        from app.services.actor import Actor

        user = User(id=7, username="ana", role=AUDITOR)
        actor = Actor.from_user(user, channel="web")
        self.assertEqual((7, "ana", AUDITOR, "web", None), (
            actor.user_id, actor.label, actor.role, actor.channel, actor.scopes,
        ))
        scoped = Actor.from_user(user, channel="mcp", scopes=["read"])
        self.assertEqual(frozenset({"read"}), scoped.scopes)

    def test_flask_free_check_detects_forbidden_imports_and_globals(self) -> None:
        bad = {
            "import flask": ["flask"],
            "from flask import g": ["flask"],
            "import flask_login": ["flask_login"],
            "from flask_login import current_user": ["flask_login", "current_user"],
            "from werkzeug.local import LocalProxy": ["werkzeug.local"],
            "from ..routes import x": ["app.routes"],
            "from .. import routes": ["app.routes"],
            "import app.routes.foo": ["app.routes.foo"],
            "def f():\n    return request.args": ["request"],
        }
        for source, expected in bad.items():
            with self.subTest(source=source):
                found = forbidden_framework_use(source)
                for item in expected:
                    self.assertIn(item, found)
        for source in ("from ..models import User", "request_id = 1", "import json"):
            with self.subTest(source=source):
                self.assertEqual([], forbidden_framework_use(source))

    def test_service_package_is_framework_free(self) -> None:
        """No service module imports Flask, Flask-Login, werkzeug.local or routes.

        Flask-SQLAlchemy still reaches the services transitively through
        ``app.models`` and ``app.extensions``. That is accepted: the future MCP
        process runs inside an application context. What must never happen is a
        direct dependency on the request/session machinery, so a service stays
        callable with an explicit ``Session`` and ``Actor`` and nothing else.
        """
        import pathlib

        import app.services as pkg

        root = pathlib.Path(pkg.__file__).parent
        modules = sorted(root.glob("*.py"))
        self.assertGreater(len(modules), 1)
        for path in modules:
            with self.subTest(module=path.name):
                self.assertEqual([], forbidden_framework_use(path.read_text()))


class DomainErrorsTestCase(unittest.TestCase):
    def test_hierarchy_and_messages(self) -> None:
        from app.services import errors

        for cls in (
            errors.NotFound,
            errors.Conflict,
            errors.PermissionDenied,
            errors.ValidationError,
        ):
            with self.subTest(error=cls.__name__):
                self.assertTrue(issubclass(cls, errors.DomainError))
                self.assertTrue(cls().message)
                self.assertEqual("custom", cls("custom").message)


if __name__ == "__main__":
    unittest.main()
