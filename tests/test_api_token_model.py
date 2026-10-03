"""ApiToken model: shape, constraints and its place outside the audited registry."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.exc import IntegrityError

from test_audit import AuditDbBase

from app.extensions import db
from app.models import RoleEnum, User


def model():
    from app.models import ApiToken

    return ApiToken


class ApiTokenShapeTestCase(unittest.TestCase):
    def test_columns_and_nullability(self) -> None:
        table = model().__table__
        self.assertEqual("api_tokens", table.name)
        required = {"id", "user_id", "name", "prefix", "token_hash", "scopes",
                    "created_at", "expires_at"}
        optional = {"created_by_label", "revoked_at", "revoked_by_label",
                    "last_used_at"}
        self.assertEqual(required | optional, set(table.c.keys()))
        for name in required:
            self.assertFalse(table.c[name].nullable, name)
        for name in optional:
            self.assertTrue(table.c[name].nullable, name)

    def test_timestamps_are_timezone_aware(self) -> None:
        table = model().__table__
        for name in ("created_at", "expires_at", "revoked_at", "last_used_at"):
            self.assertIsInstance(table.c[name].type, DateTime, name)
            self.assertTrue(table.c[name].type.timezone, name)

    def test_constraints_and_indexes_have_explicit_names(self) -> None:
        table = model().__table__
        self.assertEqual("pk_api_tokens", table.primary_key.name)
        (fk,) = table.foreign_key_constraints
        self.assertEqual("fk_api_tokens_user_id_users", fk.name)
        self.assertEqual("CASCADE", fk.ondelete)
        self.assertEqual("users", fk.referred_table.name)
        unique = {c.name: [col.name for col in c.columns]
                  for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"}
        self.assertEqual({"uq_api_tokens_prefix": ["prefix"]}, unique)
        self.assertEqual(
            {"ix_api_tokens_user_id"}, {index.name for index in table.indexes}
        )

    def test_the_token_table_is_not_in_the_audited_registry(self) -> None:
        from app.services.audit import AUDITED_MODELS

        self.assertNotIn(model(), AUDITED_MODELS)


class ApiTokenPersistenceTestCase(AuditDbBase):
    def _user(self) -> User:
        user = User(username="ana", password="x", role=RoleEnum.OPERATIVO)
        db.session.add(user)
        db.session.flush()
        return user

    def _token(self, user: User, **overrides):
        values = dict(
            user_id=user.id, name="laptop", prefix="a1b2c3d4",
            token_hash="0" * 64, scopes="read",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        ) | overrides
        token = model()(**values)
        db.session.add(token)
        return token

    def test_prefix_is_unique(self) -> None:
        user = self._user()
        self._token(user)
        db.session.flush()
        self._token(user, name="other")
        with self.assertRaises(IntegrityError):
            db.session.flush()


if __name__ == "__main__":
    unittest.main()
