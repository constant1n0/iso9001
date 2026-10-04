"""API token service: issue, authenticate, revoke, list.

Runs with the audit flush guard installed (via ``ServiceBase``). Plaintext
tokens appear only in assertions about what must never be stored or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_nonconformity_service import VALID, ServiceBase, actor, errors

from app.extensions import db
from app.models import ApiToken, AuditLog, RoleEnum, User

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
KEY = "unit-test-secret-key"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SHAPE = re.compile(r"iso_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


def tokens():
    from app.services import api_tokens

    return api_tokens


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands timezone-aware columns back naive (UTC)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class TokenBase(ServiceBase):
    admin = None

    def setUp(self) -> None:
        super().setUp()
        self.owner = self.make_user("ana", OPERATIVO)
        self.admin = actor(ADMIN, channel="cli", user_id=None)

    def make_user(self, username: str, role: RoleEnum) -> User:
        user = User(username=username, password="x", role=role)
        db.session.add(user)
        db.session.commit()
        return user

    def issue(self, **overrides):
        values = dict(secret_key=KEY, user_id=self.owner.id, name="laptop", now=NOW)
        return tokens().issue(db.session, self.admin, **(values | overrides))

    def authenticate(self, raw, **overrides):
        values = dict(secret_key=KEY, now=NOW + timedelta(minutes=1))
        return tokens().authenticate(db.session, raw, **(values | overrides))


class IssueTestCase(TokenBase):
    def test_returns_the_plaintext_once_and_stores_only_its_hmac(self) -> None:
        plaintext, row = self.issue()
        db.session.commit()
        self.assertRegex(plaintext, SHAPE)
        self.assertEqual(plaintext.split("_", 2)[1], row.prefix)
        key = hmac.new(KEY.encode(), b"iso9001-api-token-v1", hashlib.sha256).digest()
        expected = hmac.new(key, plaintext.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(expected, row.token_hash)
        stored = db.session.execute(
            db.text("SELECT * FROM api_tokens")
        ).mappings().one()
        self.assertNotIn(plaintext, json.dumps(dict(stored), default=str))
        self.assertNotIn(plaintext.split("_", 2)[2], json.dumps(dict(stored), default=str))

    def test_defaults_are_read_scope_and_ninety_days(self) -> None:
        _, row = self.issue()
        self.assertEqual("read", row.scopes)
        self.assertEqual(NOW + timedelta(days=90), aware(row.expires_at))
        self.assertEqual(NOW, aware(row.created_at))
        self.assertEqual(self.admin.label, row.created_by_label)
        self.assertEqual(self.owner.id, row.user_id)

    def test_scopes_are_deduplicated_and_stored_sorted(self) -> None:
        _, row = self.issue(scopes=["write", "read", "write"])
        self.assertEqual("read write", row.scopes)

    def test_invalid_scopes_are_rejected(self) -> None:
        for scopes in ([], ["admin"], ["read", "root"], "read", [1], None):
            with self.subTest(scopes=scopes), self.assertRaises(errors().ValidationError):
                self.issue(scopes=scopes)

    def test_expiry_must_be_between_one_and_365_days(self) -> None:
        _, row = self.issue(days=365)
        self.assertEqual(NOW + timedelta(days=365), aware(row.expires_at))
        for days in (0, -1, 366, True, "30", 1.5, None):
            with self.subTest(days=days), self.assertRaises(errors().ValidationError):
                self.issue(days=days, name=f"n{days}")

    def test_name_is_required_and_bounded(self) -> None:
        for name in ("", "   ", None, 5, "x" * 101):
            with self.subTest(name=name), self.assertRaises(errors().ValidationError):
                self.issue(name=name)
        _, row = self.issue(name="  Claude Code  ")
        self.assertEqual("Claude Code", row.name)

    def test_unknown_user_is_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            self.issue(user_id=9999)

    def test_only_administrators_outside_mcp_may_issue(self) -> None:
        for role in (AUDITOR, OPERATIVO):
            with self.subTest(role=role.name), self.assertRaises(errors().PermissionDenied):
                tokens().issue(db.session, actor(role), secret_key=KEY,
                               user_id=self.owner.id, name="x", now=NOW)
        mcp_admin = actor(ADMIN, channel="mcp", scopes={"read", "write"})
        with self.assertRaises(errors().PermissionDenied):
            tokens().issue(db.session, mcp_admin, secret_key=KEY,
                           user_id=self.owner.id, name="x", now=NOW)
        self.assertEqual(0, db.session.query(ApiToken).count())

    def test_an_empty_secret_key_is_refused(self) -> None:
        for key in ("", b"", None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.issue(secret_key=key)

    def test_issue_writes_an_audit_row_without_secret_or_hash(self) -> None:
        plaintext, row = self.issue(scopes=["read", "write"])
        db.session.commit()
        (entry,) = [r for r in self.audit_rows() if r.entity_type == "api_tokens"]
        self.assertEqual(("create", row.id, "cli"), (entry.action, entry.entity_id, entry.channel))
        self.assertEqual(self.admin.label, entry.actor_label)
        self.assertEqual("laptop", entry.after["name"])
        self.assertEqual("read write", entry.after["scopes"])
        self.assertEqual(row.prefix, entry.after["prefix"])
        self.assertNotIn("token_hash", entry.after)
        dump = json.dumps([entry.before, entry.after])
        self.assertNotIn(row.token_hash, dump)
        self.assertNotIn(plaintext.split("_", 2)[2], dump)

    def test_each_issue_gets_a_distinct_prefix_and_secret(self) -> None:
        (first, _), (second, _) = self.issue(), self.issue(name="other")
        self.assertNotEqual(first, second)

    def test_a_prefix_collision_is_retried(self) -> None:
        _, first = self.issue()
        db.session.commit()
        with patch("app.services.api_tokens.secrets.token_hex",
                   side_effect=[first.prefix, "00000000"]):
            _, second = self.issue(name="second")
        self.assertEqual("00000000", second.prefix)


class IssueFailureTestCase(TokenBase):
    def test_every_prefix_attempt_colliding_is_a_conflict(self) -> None:
        _, row = self.issue()
        db.session.commit()
        with patch("app.services.api_tokens.secrets.token_hex", return_value=row.prefix):
            with self.assertRaises(errors().Conflict):
                self.issue(name="second")
        self.assertEqual(1, db.session.query(ApiToken).count())

    def test_an_integrity_error_while_auditing_is_a_conflict(self) -> None:
        from sqlalchemy.exc import IntegrityError

        failure = IntegrityError("INSERT", {}, Exception("duplicate"))
        with patch("app.services.api_tokens.audit.record", side_effect=failure):
            with self.assertRaises(errors().Conflict) as raised:
                self.issue()
        self.assertIs(failure, raised.exception.__cause__)


class SingleDigestTestCase(TokenBase):
    def test_issue_and_authenticate_share_one_digest_function(self) -> None:
        with patch("app.services.api_tokens._digest", return_value="f" * 64) as digest:
            plaintext, row = self.issue()
            db.session.commit()
            self.assertEqual("f" * 64, row.token_hash)
            self.assertEqual(self.owner.id, self.authenticate(plaintext).user_id)
            self.assertEqual(2, digest.call_count)
        with self.assertRaises(errors().AuthenticationFailed):
            self.authenticate(plaintext)

    def test_the_stored_hash_is_what_the_digest_function_returns(self) -> None:
        plaintext, row = self.issue()
        self.assertEqual(tokens()._digest(KEY, plaintext), row.token_hash)


class StatusTestCase(TokenBase):
    def test_active_expired_and_revoked(self) -> None:
        _, row = self.issue()
        expiry = aware(row.expires_at)
        self.assertEqual(tokens().ACTIVE, tokens().status(row, expiry - timedelta(seconds=1)))
        self.assertEqual(tokens().EXPIRED, tokens().status(row, expiry))
        tokens().revoke(db.session, self.admin, row.prefix, now=NOW)
        self.assertEqual(tokens().REVOKED, tokens().status(row, expiry - timedelta(days=1)))
        self.assertEqual(tokens().REVOKED, tokens().status(row, expiry + timedelta(days=1)))

    def test_a_naive_expiry_is_read_as_utc(self) -> None:
        _, row = self.issue()
        row.expires_at = NOW.replace(tzinfo=None) + timedelta(days=1)
        self.assertEqual(tokens().ACTIVE, tokens().status(row, NOW))
        self.assertEqual(tokens().EXPIRED, tokens().status(row, NOW + timedelta(days=1)))

    def test_authenticate_uses_the_same_rule(self) -> None:
        plaintext, _ = self.issue()
        with patch("app.services.api_tokens.status", return_value="expired"):
            with self.assertRaises(errors().AuthenticationFailed) as raised:
                self.authenticate(plaintext)
        self.assertEqual(errors().AuthFailure.EXPIRED, raised.exception.reason)


class AuthFailureTestCase(TokenBase):
    def test_reasons_are_named_constants_with_stable_log_values(self) -> None:
        reasons = errors().AuthFailure
        self.assertEqual(
            {"malformed", "unknown_prefix", "bad_secret", "revoked", "expired",
             "user_missing", "user_inactive", "invalid"},
            {str(r) for r in reasons},
        )
        self.assertEqual("invalid", errors().AuthenticationFailed().reason)


class AuthenticateTestCase(TokenBase):
    def setUp(self) -> None:
        super().setUp()
        self.plaintext, self.row = self.issue(scopes=["read", "write"])
        db.session.commit()

    def assert_generic_failure(self, raw, **overrides) -> None:
        with self.assertRaises(errors().AuthenticationFailed) as raised:
            self.authenticate(raw, **overrides)
        self.assertEqual("Credenciales no válidas.", raised.exception.message)
        if raw:
            self.assertNotIn(str(raw), str(raised.exception))

    def test_a_valid_token_yields_the_mcp_actor(self) -> None:
        result = self.authenticate(self.plaintext)
        self.assertEqual(
            (self.owner.id, "ana", OPERATIVO, "mcp", frozenset({"read", "write"})),
            (result.user_id, result.label, result.role, result.channel, result.scopes),
        )

    def test_the_owners_current_role_is_used(self) -> None:
        self.owner.role = AUDITOR
        db.session.commit()
        self.assertEqual(AUDITOR, self.authenticate(self.plaintext).role)

    def test_malformed_strings_fail_identically(self) -> None:
        prefix, secret = self.plaintext.split("_", 2)[1:]
        for raw in (None, 123, b"x", "", "garbage", "iso_", "iso__", f"iso_{prefix}",
                    f"iso_{prefix}_", f"iso_{prefix}_{secret[:-1]}", f"iso_{prefix}_{secret}x",
                    f"iso_{prefix}_{secret}\n", f" {self.plaintext}", f"{self.plaintext} ",
                    f"ISO_{prefix}_{secret}", f"iso_{'A' * 8}_{secret}",
                    f"iso_{prefix}_{secret[:-1]}!", "iso_zzzzzzzz_" + secret):
            with self.subTest(raw=raw):
                self.assert_generic_failure(raw)

    def test_unknown_prefix_and_wrong_secret_fail_identically(self) -> None:
        secret = self.plaintext.split("_", 2)[2]
        self.assert_generic_failure(f"iso_00000000_{secret}")
        other = ("A" if secret[0] != "A" else "B") + secret[1:]
        self.assert_generic_failure(f"iso_{self.row.prefix}_{other}")

    def test_a_token_signed_with_another_secret_key_fails(self) -> None:
        self.assert_generic_failure(self.plaintext, secret_key="rotated-secret-key")

    def test_expired_token_fails_at_and_after_expiry(self) -> None:
        expiry = aware(self.row.expires_at)
        self.authenticate(self.plaintext, now=expiry - timedelta(seconds=1))
        self.assert_generic_failure(self.plaintext, now=expiry)
        self.assert_generic_failure(self.plaintext, now=expiry + timedelta(days=1))

    def test_revoked_token_fails(self) -> None:
        tokens().revoke(db.session, self.admin, self.row.prefix, now=NOW)
        db.session.commit()
        self.assert_generic_failure(self.plaintext)

    def test_token_of_a_deleted_user_fails(self) -> None:
        db.session.execute(db.text("DELETE FROM users"))
        db.session.commit()
        self.assert_generic_failure(self.plaintext)

    def test_valid_token_of_an_inactive_user_fails(self) -> None:
        self.owner.active = False
        db.session.commit()
        self.assert_generic_failure(self.plaintext)
        with self.assertRaises(errors().AuthenticationFailed) as raised:
            self.authenticate(self.plaintext)
        self.assertEqual(errors().AuthFailure.USER_INACTIVE, raised.exception.reason)
        self.assertEqual(self.row.prefix, raised.exception.token_prefix)

    def test_the_failure_reason_is_internal_and_not_in_the_message(self) -> None:
        with self.assertRaises(errors().AuthenticationFailed) as raised:
            self.authenticate(f"iso_{self.row.prefix}_" + "A" * 43)
        error = raised.exception
        self.assertEqual(errors().AuthFailure.BAD_SECRET, error.reason)
        self.assertEqual(self.row.prefix, error.token_prefix)
        self.assertNotIn("bad_secret", str(error))
        self.assertIsInstance(error, errors().DomainError)
        with self.assertRaises(errors().AuthenticationFailed) as raised:
            self.authenticate("garbage")
        self.assertEqual((errors().AuthFailure.MALFORMED, None), (raised.exception.reason, raised.exception.token_prefix))

    def test_the_hash_is_compared_in_constant_time(self) -> None:
        with patch("app.services.api_tokens.hmac.compare_digest",
                   wraps=hmac.compare_digest) as compare:
            self.authenticate(self.plaintext)
            self.assertEqual(1, compare.call_count)
            with self.assertRaises(errors().AuthenticationFailed):
                self.authenticate("iso_00000000_" + "A" * 43)
            self.assertEqual(2, compare.call_count)

    def test_last_used_at_is_throttled_to_once_per_five_minutes(self) -> None:
        self.assertIsNone(self.row.last_used_at)
        first = NOW + timedelta(minutes=1)
        self.authenticate(self.plaintext, now=first)
        self.assertEqual(first, aware(self.row.last_used_at))
        self.authenticate(self.plaintext, now=first + timedelta(minutes=4, seconds=59))
        self.assertEqual(first, aware(self.row.last_used_at))
        later = first + timedelta(minutes=5)
        self.authenticate(self.plaintext, now=later)
        self.assertEqual(later, aware(self.row.last_used_at))

    def test_a_failed_authentication_writes_nothing(self) -> None:
        with self.assertRaises(errors().AuthenticationFailed):
            self.authenticate(f"iso_{self.row.prefix}_" + "A" * 43)
        self.assertIsNone(self.row.last_used_at)
        self.assertEqual(1, db.session.query(AuditLog).count())

    def test_scopes_narrow_the_owner_through_the_policy(self) -> None:
        from app.services import nonconformities

        reader = tokens().authenticate(
            db.session, self.issue(scopes=["read"], name="r")[0], secret_key=KEY, now=NOW)
        writer = tokens().authenticate(
            db.session, self.issue(scopes=["write"], name="w")[0], secret_key=KEY, now=NOW)
        full = self.authenticate(self.plaintext)
        with self.assertRaises(errors().PermissionDenied):
            nonconformities.create(db.session, reader, dict(VALID))
        nonconformities.list_(db.session, reader)
        created = nonconformities.create(db.session, writer, dict(VALID))
        with self.assertRaises(errors().PermissionDenied):
            nonconformities.get(db.session, writer, created.id)
        self.assertIs(created, nonconformities.get(db.session, full, created.id))
        with self.assertRaises(errors().PermissionDenied):
            nonconformities.delete(db.session, full, created.id)


class RevokeAndListTestCase(TokenBase):
    def test_revoke_by_prefix_or_id_stamps_and_audits(self) -> None:
        _, by_prefix = self.issue()
        _, by_id = self.issue(name="second")
        db.session.commit()
        later = NOW + timedelta(hours=1)
        tokens().revoke(db.session, self.admin, by_prefix.prefix, now=later)
        tokens().revoke(db.session, self.admin, by_id.id, now=later)
        db.session.commit()
        for row in (by_prefix, by_id):
            self.assertEqual(later, aware(row.revoked_at))
            self.assertEqual(self.admin.label, row.revoked_by_label)
        entries = [r for r in self.audit_rows() if r.action == "update"]
        self.assertEqual(2, len(entries))
        for entry in entries:
            self.assertEqual("api_tokens", entry.entity_type)
            self.assertEqual({"revoked_at", "revoked_by_label"}, set(entry.after))
            self.assertNotIn("token_hash", json.dumps([entry.before, entry.after]))

    def test_revoking_twice_unknown_or_without_permission_fails(self) -> None:
        _, row = self.issue()
        db.session.commit()
        tokens().revoke(db.session, self.admin, row.prefix, now=NOW)
        with self.assertRaises(errors().Conflict):
            tokens().revoke(db.session, self.admin, row.prefix, now=NOW)
        for ref in ("ffffffff", 9999, "x", None):
            with self.subTest(ref=ref), self.assertRaises(errors().NotFound):
                tokens().revoke(db.session, self.admin, ref, now=NOW)
        for caller in (actor(AUDITOR), actor(OPERATIVO),
                       actor(ADMIN, channel="mcp", scopes={"read", "write"})):
            with self.subTest(channel=caller.channel, role=caller.role), \
                    self.assertRaises(errors().PermissionDenied):
                tokens().revoke(db.session, caller, row.prefix, now=NOW)

    def test_list_returns_every_token_and_filters_by_user(self) -> None:
        other = self.make_user("luis", AUDITOR)
        _, first = self.issue()
        _, second = self.issue(user_id=other.id, name="b")
        db.session.commit()
        tokens().revoke(db.session, self.admin, first.prefix, now=NOW)
        self.assertEqual([first, second], tokens().list_(db.session, self.admin))
        self.assertEqual([second], tokens().list_(db.session, self.admin, user_id=other.id))
        self.assertEqual([], tokens().list_(db.session, self.admin, user_id=9999))

    def test_list_is_for_administrators_outside_mcp_only(self) -> None:
        for caller in (actor(AUDITOR), actor(OPERATIVO),
                       actor(ADMIN, channel="mcp", scopes={"read"})):
            with self.subTest(channel=caller.channel, role=caller.role), \
                    self.assertRaises(errors().PermissionDenied):
                tokens().list_(db.session, caller)


if __name__ == "__main__":
    unittest.main()
