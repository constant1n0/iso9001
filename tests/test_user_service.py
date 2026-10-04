"""Users service: administration, guard rails and self-service (UM-2).

Runs on SQLite with the audit flush guard installed (via ``ServiceBase``).
Seeded accounts carry a cheap PBKDF2 hash so checking the current password is
fast; the service itself hashes new passwords at the application's full cost.
The last-administrator race runs on PostgreSQL when ``TEST_POSTGRES_URI`` is
set (CI provides it).
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import ApiToken, RoleEnum, User
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
PASSWORD = "clave-actual-1"
SEED_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
KEY = "unit-test-secret-key"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
CLI_ADMIN = Actor(user_id=None, label="cli:operator", role=ADMIN, channel="cli")
NEW = {
    "username": "maria",
    "email": "maria@example.com",
    "role": "AUDITOR",
    "password": "nueva-clave-1",
}
POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")


def users():
    from app.services import users as service

    return service


def fields():
    from app.services import fields as module

    return module


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands timezone-aware columns back naive (UTC)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class UserServiceBase(ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_user("admin", ADMIN)
        self.admin = Actor.from_user(self.root, channel="web")
        self.luis = self.make_user("luis")

    def make_user(self, username: str, role: RoleEnum = OPERATIVO, *,
                  email: str | None = None, active: bool = True) -> User:
        user = User(username=username, email=email or f"{username}@example.com",
                    password=SEED_HASH, role=role, active=active)
        db.session.add(user)
        db.session.commit()
        return user

    def user_rows(self):
        return [row for row in self.audit_rows() if row.entity_type == "users"]


class FieldValidatorTestCase(unittest.TestCase):
    def test_email_is_trimmed_lower_cased_and_checked(self) -> None:
        data = {"email": "  Ana.Ruiz@Example.COM "}
        self.assertEqual("ana.ruiz@example.com", fields().email(data, "email"))
        for value in ("", "   ", None, 5, "ana", "ana@example", "a b@example.com",
                      "ana@@example.com", "x" * 250 + "@ex.com"):
            with self.subTest(value=value), self.assertRaises(errors().ValidationError):
                fields().email({"email": value}, "email")

    def test_new_password_needs_eight_characters_and_is_kept_verbatim(self) -> None:
        for value in ("12345678", " con espacios "):
            self.assertEqual(value, fields().new_password({"p": value}, "p"))
        for value in ("", "1234567", None, 12345678):
            with self.subTest(value=value), self.assertRaises(errors().ValidationError):
                fields().new_password({"p": value}, "p")


class ReadTestCase(UserServiceBase):
    def test_list_is_ordered_by_username_and_get_finds_one(self) -> None:
        self.make_user("bea")
        names = [user.username for user in users().list_(db.session, self.admin)]
        self.assertEqual(["admin", "bea", "luis"], names)
        self.assertEqual(self.luis, users().get(db.session, self.admin, self.luis.id))
        with self.assertRaises(errors().NotFound):
            users().get(db.session, self.admin, 9999)

    def test_administrators_and_auditors_read_operativos_do_not(self) -> None:
        auditor = actor(AUDITOR)
        self.assertEqual([self.root, self.luis], users().list_(db.session, auditor))
        self.assertEqual(self.luis, users().get(db.session, auditor, self.luis.id))
        with self.assertRaises(errors().PermissionDenied):
            users().list_(db.session, actor(OPERATIVO))
        with self.assertRaises(errors().PermissionDenied):
            users().get(db.session, actor(OPERATIVO), self.luis.id)


class CreateTestCase(UserServiceBase):
    def test_creates_an_active_user_with_a_hashed_password(self) -> None:
        data = NEW | {"username": "  maria  ", "email": " Maria@Example.COM "}
        user = users().create(db.session, self.admin, data)
        db.session.commit()
        self.assertEqual(("maria", "maria@example.com", AUDITOR, True),
                         (user.username, user.email, user.role, user.active))
        self.assertTrue(user.password.startswith("pbkdf2:sha256:"))
        self.assertTrue(check_password_hash(user.password, NEW["password"]))
        (entry,) = self.user_rows()
        self.assertEqual(("create", user.id, "web"), (entry.action, entry.entity_id, entry.channel))
        self.assertEqual({"username": "maria", "email": "maria@example.com",
                          "role": "Auditor", "active": True},
                         {key: entry.after[key] for key in ("username", "email", "role", "active")})
        self.assertNotIn("password", entry.after)

    def test_every_field_is_required_and_nothing_else_is_accepted(self) -> None:
        for key in NEW:
            payload = {k: v for k, v in NEW.items() if k != key}
            with self.subTest(missing=key), self.assertRaises(errors().ValidationError):
                users().create(db.session, self.admin, payload)
        for extra in ({"active": False}, {"id": 50}, {"password_hash": "x"}):
            with self.subTest(extra=extra), self.assertRaises(errors().ValidationError):
                users().create(db.session, self.admin, NEW | extra)
        self.assertEqual(2, db.session.query(User).count())
        self.assertEqual([], self.audit_rows())

    def test_invalid_values_are_rejected(self) -> None:
        invalid = {
            "username": ("abc", "  abc  ", "x" * 151, "", None, 7),
            "email": ("maria", "maria@example", "", None),
            "role": ("ROOT", "Administrador", "", None),
            "password": ("1234567", "", None, 12345678),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), \
                        self.assertRaises(errors().ValidationError):
                    users().create(db.session, self.admin, NEW | {key: value})
        self.assertEqual(2, db.session.query(User).count())

    def test_duplicate_username_or_email_is_a_conflict(self) -> None:
        self.make_user("legacy", email="Legacy@Example.com")
        for clash in ({"username": "admin"}, {"email": " ADMIN@example.com"},
                      {"email": "legacy@example.com"}):
            with self.subTest(clash=clash), self.assertRaises(errors().Conflict):
                users().create(db.session, self.admin, NEW | clash)
        self.assertEqual(3, db.session.query(User).count())

    def test_an_integrity_error_is_a_conflict(self) -> None:
        with patch("app.services.users.audit.record",
                   side_effect=IntegrityError("INSERT", {}, Exception("unique"))), \
                self.assertRaises(errors().Conflict):
            users().create(db.session, self.admin, NEW)

    def test_only_administrators_create(self) -> None:
        for caller in (actor(AUDITOR), actor(OPERATIVO)):
            with self.subTest(role=caller.role), self.assertRaises(errors().PermissionDenied):
                users().create(db.session, caller, NEW)
        self.assertEqual(2, db.session.query(User).count())


class UpdateTestCase(UserServiceBase):
    def test_updates_email_and_role_and_audits_the_change(self) -> None:
        user = users().update(db.session, self.admin, self.luis.id,
                              {"email": " Luis.Nuevo@Example.com", "role": "AUDITOR"})
        db.session.commit()
        self.assertEqual(("luis.nuevo@example.com", AUDITOR), (user.email, user.role))
        (entry,) = self.user_rows()
        self.assertEqual("update", entry.action)
        self.assertEqual({"email": "luis@example.com", "role": "Operativo"}, entry.before)
        self.assertEqual({"email": "luis.nuevo@example.com", "role": "Auditor"}, entry.after)

    def test_only_email_and_role_are_writable(self) -> None:
        for extra in ({"username": "otro"}, {"password": "nueva-clave-1"},
                      {"active": False}, {"id": 99}):
            with self.subTest(extra=extra), self.assertRaises(errors().ValidationError):
                users().update(db.session, self.admin, self.luis.id, extra)
        self.assertEqual(("luis", True), (self.luis.username, self.luis.active))
        self.assertEqual([], self.audit_rows())

    def test_a_no_op_update_writes_nothing(self) -> None:
        for data in ({}, {"email": " LUIS@example.com "}, {"role": "OPERATIVO"}):
            with self.subTest(data=data):
                users().update(db.session, self.admin, self.luis.id, data)
        db.session.commit()
        self.assertEqual([], self.audit_rows())

    def test_another_users_email_is_a_conflict(self) -> None:
        with self.assertRaises(errors().Conflict):
            users().update(db.session, self.admin, self.luis.id, {"email": "Admin@Example.com"})

    def test_an_actor_cannot_change_their_own_role(self) -> None:
        self.make_user("segundo", ADMIN)
        with self.assertRaises(errors().ValidationError):
            users().update(db.session, self.admin, self.root.id, {"role": "AUDITOR"})
        self.assertEqual(ADMIN, self.root.role)
        users().update(db.session, self.admin, self.root.id,
                       {"role": "ADMINISTRADOR", "email": "jefe@example.com"})
        self.assertEqual("jefe@example.com", self.root.email)

    def test_the_last_active_administrator_cannot_be_demoted(self) -> None:
        dormant = self.make_user("dormido", ADMIN, active=False)
        with self.assertRaises(errors().Conflict):
            users().update(db.session, CLI_ADMIN, self.root.id, {"role": "OPERATIVO"})
        self.assertEqual(ADMIN, self.root.role)
        users().update(db.session, CLI_ADMIN, dormant.id, {"role": "OPERATIVO"})
        self.make_user("segundo", ADMIN)
        users().update(db.session, CLI_ADMIN, self.root.id, {"role": "OPERATIVO"})
        self.assertEqual(OPERATIVO, self.root.role)

    def test_only_administrators_update_known_users(self) -> None:
        for caller in (actor(AUDITOR), actor(OPERATIVO)):
            with self.subTest(role=caller.role), self.assertRaises(errors().PermissionDenied):
                users().update(db.session, caller, self.luis.id, {"role": "ADMINISTRADOR"})
        with self.assertRaises(errors().NotFound):
            users().update(db.session, self.admin, 9999, {"role": "AUDITOR"})
        self.assertEqual(OPERATIVO, self.luis.role)


class SetActiveTestCase(UserServiceBase):
    def issue(self, **overrides) -> ApiToken:
        from app.services import api_tokens

        values = dict(secret_key=KEY, user_id=self.luis.id, name="laptop", now=NOW)
        return api_tokens.issue(db.session, CLI_ADMIN, **(values | overrides))[1]

    def test_deactivates_and_reactivates_with_audit_rows(self) -> None:
        users().set_active(db.session, self.admin, self.luis.id, False, now=NOW)
        self.assertFalse(self.luis.active)
        users().set_active(db.session, self.admin, self.luis.id, True, now=NOW)
        db.session.commit()
        self.assertTrue(self.luis.active)
        on, off = {"active": True}, {"active": False}
        self.assertEqual([(on, off), (off, on)],
                         [(row.before, row.after) for row in self.user_rows()])

    def test_a_no_op_writes_nothing(self) -> None:
        users().set_active(db.session, self.admin, self.luis.id, True, now=NOW)
        self.assertEqual([], self.audit_rows())

    def test_the_flag_must_be_a_boolean(self) -> None:
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaises(errors().ValidationError):
                users().set_active(db.session, self.admin, self.luis.id, value)
        self.assertTrue(self.luis.active)

    def test_nobody_can_deactivate_themselves(self) -> None:
        self.make_user("segundo", ADMIN)
        with self.assertRaises(errors().ValidationError):
            users().set_active(db.session, self.admin, self.root.id, False, now=NOW)
        self.assertTrue(self.root.active)

    def test_the_last_active_administrator_cannot_be_deactivated(self) -> None:
        with self.assertRaises(errors().Conflict):
            users().set_active(db.session, CLI_ADMIN, self.root.id, False, now=NOW)
        self.assertTrue(self.root.active)
        self.make_user("segundo", ADMIN)
        users().set_active(db.session, CLI_ADMIN, self.root.id, False, now=NOW)
        self.assertFalse(self.root.active)

    def test_deactivation_revokes_active_tokens_only_and_reactivation_keeps_them(self) -> None:
        first, second = self.issue(), self.issue(name="second")
        expired = self.issue(name="old", now=NOW - timedelta(days=100))
        earlier = NOW - timedelta(hours=1)
        gone = self.issue(name="gone")
        gone.revoked_at = earlier
        foreign = self.issue(user_id=self.root.id, name="foreign")
        db.session.commit()
        users().set_active(db.session, self.admin, self.luis.id, False, now=NOW)
        db.session.commit()
        for row in (first, second):
            self.assertEqual((NOW, "admin"), (aware(row.revoked_at), row.revoked_by_label))
        self.assertEqual((None, earlier, None),
                         (expired.revoked_at, aware(gone.revoked_at), foreign.revoked_at))
        revocations = [row for row in self.audit_rows()
                       if row.entity_type == "api_tokens" and row.action == "update"]
        self.assertEqual({first.id, second.id}, {row.entity_id for row in revocations})
        users().set_active(db.session, self.admin, self.luis.id, True, now=NOW)
        self.assertEqual(NOW, aware(first.revoked_at))

    def test_permissions_and_unknown_users(self) -> None:
        token = self.issue()
        db.session.commit()
        mcp_admin = actor(ADMIN, channel="mcp", scopes={"read", "write"})
        for caller in (actor(AUDITOR), actor(OPERATIVO), mcp_admin):
            with self.subTest(channel=caller.channel, role=caller.role), \
                    self.assertRaises(errors().PermissionDenied):
                users().set_active(db.session, caller, self.luis.id, False, now=NOW)
        self.assertEqual((True, None), (self.luis.active, token.revoked_at))
        with self.assertRaises(errors().NotFound):
            users().set_active(db.session, self.admin, 9999, False, now=NOW)


class SelfServiceTestCase(UserServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.me = Actor.from_user(self.luis, channel="web")

    def change_email(self, caller=None, *, current=PASSWORD, new="luis@nuevo.es"):
        return users().change_own_email(db.session, caller or self.me,
                                        current_password=current, new_email=new)

    def change_password(self, caller=None, *, current=PASSWORD, new="otra-clave-9"):
        return users().change_own_password(db.session, caller or self.me,
                                           current_password=current, new_password=new)

    def test_change_own_email_with_the_current_password(self) -> None:
        user = self.change_email(new=" Luis@Nuevo.ES ")
        db.session.commit()
        self.assertEqual("luis@nuevo.es", user.email)
        (entry,) = self.user_rows()
        self.assertEqual((self.luis.id, self.luis.id, "update"),
                         (entry.entity_id, entry.actor_user_id, entry.action))
        self.assertEqual(({"email": "luis@example.com"}, {"email": "luis@nuevo.es"}),
                         (entry.before, entry.after))

    def test_every_role_may_change_its_own_email(self) -> None:
        auditor = self.make_user("auditora", AUDITOR)
        self.change_email(Actor.from_user(auditor, channel="web"), new="aud@example.com")
        self.change_email(self.admin, new="root@example.com")
        self.assertEqual(("aud@example.com", "root@example.com"), (auditor.email, self.root.email))

    def test_new_email_rules(self) -> None:
        with self.assertRaises(errors().ValidationError):
            self.change_email(new="no-es-un-correo")
        with self.assertRaises(errors().Conflict):
            self.change_email(new="ADMIN@example.com")
        self.change_email(new=" LUIS@example.com ")
        self.assertEqual([], self.audit_rows())

    def test_change_own_password(self) -> None:
        self.change_password()
        db.session.commit()
        self.assertTrue(self.luis.password.startswith("pbkdf2:sha256:"))
        self.assertTrue(check_password_hash(self.luis.password, "otra-clave-9"))
        self.assertFalse(check_password_hash(self.luis.password, PASSWORD))
        (entry,) = self.user_rows()
        self.assertEqual(({}, {"credential_changed": True}), (entry.before, entry.after))

    def test_new_password_rules(self) -> None:
        for new in ("1234567", "", None, PASSWORD):
            with self.subTest(new=new), self.assertRaises(errors().ValidationError):
                self.change_password(new=new)
        self.assertEqual(SEED_HASH, self.luis.password)

    def test_a_wrong_current_password_changes_nothing(self) -> None:
        for current in ("incorrecta", "", None):
            with self.subTest(current=current):
                with self.assertRaises(errors().ValidationError):
                    self.change_email(current=current)
                with self.assertRaises(errors().ValidationError):
                    self.change_password(current=current)
        self.assertEqual(("luis@example.com", SEED_HASH), (self.luis.email, self.luis.password))
        self.assertEqual([], self.audit_rows())

    def test_a_wrong_password_hides_whether_the_new_email_is_taken(self) -> None:
        with self.assertRaises(errors().ValidationError):
            self.change_email(current="incorrecta", new="admin@example.com")

    def test_the_mcp_channel_never_changes_credentials(self) -> None:
        token_actor = Actor.from_user(self.luis, channel="mcp", scopes={"read", "write"})
        with self.assertRaises(errors().PermissionDenied):
            self.change_email(token_actor)
        with self.assertRaises(errors().PermissionDenied):
            self.change_password(token_actor)
        self.assertEqual(("luis@example.com", SEED_HASH), (self.luis.email, self.luis.password))

    def test_self_service_needs_an_active_account(self) -> None:
        dormant = self.make_user("dormido", active=False)
        ghost = actor(OPERATIVO, user_id=9999)
        for caller in (CLI_ADMIN, ghost, Actor.from_user(dormant, channel="web")):
            with self.subTest(caller=caller.label):
                with self.assertRaises(errors().PermissionDenied):
                    self.change_email(caller)
                with self.assertRaises(errors().PermissionDenied):
                    self.change_password(caller)
        self.assertEqual([], self.audit_rows())


class AuditSafetyTestCase(UserServiceBase):
    def test_no_audit_row_holds_a_password_or_its_hash(self) -> None:
        created = users().create(db.session, self.admin, NEW)
        users().update(db.session, self.admin, created.id, {"role": "OPERATIVO"})
        users().set_active(db.session, self.admin, created.id, False, now=NOW)
        me = Actor.from_user(self.luis, channel="web")
        users().change_own_password(db.session, me, current_password=PASSWORD,
                                    new_password="otra-clave-9")
        db.session.commit()
        rows = self.user_rows()
        self.assertEqual(4, len(rows))
        dump = json.dumps([[row.before, row.after] for row in rows])
        for secret in (NEW["password"], "otra-clave-9", PASSWORD, SEED_HASH,
                       created.password, self.luis.password):
            self.assertNotIn(secret, dump)
        for row in rows:
            for side in (row.before or {}, row.after or {}):
                self.assertFalse([key for key in side if "password" in key.lower()])


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class LastAdministratorRaceTestCase(unittest.TestCase):
    """Two demotions race for the last two administrators on PostgreSQL."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        with self.app.app_context():
            db.create_all()
            admins = [User(username=name, email=f"{name}@example.com",
                           password=SEED_HASH, role=ADMIN) for name in ("admin-a", "admin-b")]
            db.session.add_all(admins)
            db.session.commit()
            self.ids = [admin.id for admin in admins]

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()

    def test_the_second_demotion_waits_for_the_lock_and_is_refused(self) -> None:
        flushed, release, outcomes = threading.Event(), threading.Event(), {}

        def demote(user_id: int, name: str, hold: bool) -> None:
            with self.app.app_context():
                try:
                    users().update(db.session, CLI_ADMIN, user_id, {"role": "OPERATIVO"})
                    if hold:
                        flushed.set()
                        release.wait(10)
                    db.session.commit()
                    outcomes[name] = "demoted"
                except errors().Conflict:
                    outcomes[name] = "refused"
                except Exception as error:  # surfaced by the assertion below
                    outcomes[name] = repr(error)
                finally:
                    flushed.set()
                    db.session.rollback()
                    db.session.remove()

        first = threading.Thread(target=demote, args=(self.ids[0], "first", True))
        second = threading.Thread(target=demote, args=(self.ids[1], "second", False))
        try:
            first.start()
            self.assertTrue(flushed.wait(10))
            second.start()
            second.join(0.5)
            waited = second.is_alive()
        finally:
            release.set()
            first.join(10)
            if second.ident is not None:  # started
                second.join(10)
        self.assertEqual({"first": "demoted", "second": "refused"}, outcomes)
        self.assertTrue(waited, "the second demotion must wait for the first one's row lock")
        with self.app.app_context():
            remaining = db.session.scalars(select(User.username).where(User.role == ADMIN))
            self.assertEqual(["admin-b"], list(remaining))


if __name__ == "__main__":
    unittest.main()
