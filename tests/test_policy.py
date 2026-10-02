"""Table-driven tests for the central permission policy (today's matrix)."""

from __future__ import annotations

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

# resource name -> (roles allowed to read/create/update, roles allowed to delete)
# Written by hand from the route decorators; deliberately NOT derived from
# the policy module under test.
EXPECTED = {
    "NONCONFORMITIES": (ALL_ROLES, ADMIN_ONLY),
    "IMPROVEMENTS": (ALL_ROLES, ALL_ROLES),
    "CUSTOMER_SATISFACTION": (ALL_ROLES, ALL_ROLES),
    "TRAINING": (ALL_ROLES, ALL_ROLES),
    "AUDITS": (ADMIN_AUDITOR, ADMIN_AUDITOR),
    "AUDIT_INDICATORS": (ALL_ROLES, ALL_ROLES),
    "DOCUMENTS": (ADMIN_ONLY, ADMIN_ONLY),
    "INTERESTED_PARTIES": (ALL_ROLES, ALL_ROLES),
    "ROLES_RESPONSIBILITIES": (ALL_ROLES, ALL_ROLES),
    "RISKS_OPPORTUNITIES": (ALL_ROLES, ALL_ROLES),
    "TRAINING_RESOURCES": (ALL_ROLES, ALL_ROLES),
    "PROCESS_OPERATIONS": (ALL_ROLES, ALL_ROLES),
}


def make_actor(role, channel="web", scopes=None):
    from app.services.actor import Actor

    return Actor(user_id=1, label="tester", role=role, channel=channel, scopes=scopes)


class PolicyMatrixTestCase(unittest.TestCase):
    def test_every_resource_is_covered_by_the_table(self) -> None:
        from app.services.policy import Resource

        self.assertEqual(set(EXPECTED), {r.name for r in Resource})

    def test_matrix_for_every_role_resource_and_action(self) -> None:
        from app.services.policy import Action, Resource, can

        for name, (write_roles, delete_roles) in EXPECTED.items():
            resource = Resource[name]
            for role in RoleEnum:
                actor = make_actor(role)
                for action in Action:
                    allowed_roles = (
                        delete_roles if action is Action.DELETE else write_roles
                    )
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
        ]
        for role, scopes, action, resource, expected in cases:
            with self.subTest(role=role.name, scopes=sorted(scopes), action=action):
                self.assertEqual(
                    expected, can(make_actor(role, scopes=scopes), action, resource)
                )

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

    def test_service_package_does_not_import_flask(self) -> None:
        import pathlib
        import app.services as pkg

        root = pathlib.Path(pkg.__file__).parent
        for path in root.glob("*.py"):
            text = path.read_text()
            with self.subTest(module=path.name):
                self.assertNotIn("import flask", text)
                self.assertNotIn("from flask", text)


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
