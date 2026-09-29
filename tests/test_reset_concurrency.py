"""Single-use reset tokens under real concurrency on PostgreSQL.

The SQLite tests prove the conditional-update contract, not how PostgreSQL
behaves when several transactions race for the same row. These tests run
the race against a disposable database in ``TEST_POSTGRES_URI`` (provided
by CI; skipped locally without it). The database is wiped.
"""

from __future__ import annotations

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import RoleEnum, User


POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
RACERS = 8
ORIGINAL_PASSWORD = "OriginalPassword123!"


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(
    POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set"
)
class ResetTokenRaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(
            SQLALCHEMY_DATABASE_URI=POSTGRES_URI,
            SQLALCHEMY_ENGINE_OPTIONS={"pool_size": RACERS, "max_overflow": RACERS},
            PASSWORD_RESET_BASE_URL="https://qms.example.invalid",
        )
        with self.app.app_context():
            db.create_all()
            user = User(
                username="racer",
                email="racer@example.com",
                password=generate_password_hash(ORIGINAL_PASSWORD),
                role=RoleEnum.OPERATIVO,
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
            self.original_hash = user.password
            self.token = user.get_reset_token()

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

    def _stored_hash(self) -> str:
        with self.app.app_context():
            return db.session.get(User, self.user_id).password

    def test_conditional_update_lets_exactly_one_transaction_win(self) -> None:
        barrier = threading.Barrier(RACERS)
        candidates = [generate_password_hash(f"Racer-{i}-Pass!") for i in range(RACERS)]

        def race(new_hash: str) -> bool:
            with self.app.app_context():
                barrier.wait()
                try:
                    return User.update_password_from_reset(
                        self.user_id, self.original_hash, new_hash
                    )
                finally:
                    db.session.remove()

        with ThreadPoolExecutor(RACERS) as pool:
            results = list(pool.map(race, candidates))

        self.assertEqual(1, results.count(True), results)
        self.assertEqual(candidates[results.index(True)], self._stored_hash())

    def test_simultaneous_reset_requests_consume_the_link_once(self) -> None:
        barrier = threading.Barrier(RACERS)
        passwords = [f"NewPassword-{i}-Secure!" for i in range(RACERS)]
        path = f"/reset_password/{self.token}"

        def submit(password: str) -> str:
            client = self.app.test_client()
            barrier.wait()
            response = client.post(
                path, data={"password": password, "confirm_password": password}
            )
            self.assertEqual(302, response.status_code)
            return urlsplit(response.location).path

        with ThreadPoolExecutor(RACERS) as pool:
            destinations = list(pool.map(submit, passwords))

        self.assertEqual(1, destinations.count("/login"), destinations)
        self.assertEqual(RACERS - 1, destinations.count("/reset_password_request"),
                         destinations)
        winner = passwords[destinations.index("/login")]
        stored = self._stored_hash()
        self.assertTrue(check_password_hash(stored, winner))
        self.assertFalse(check_password_hash(stored, ORIGINAL_PASSWORD))

        # The link is spent: a later attempt is rejected and changes nothing.
        late = self.app.test_client().post(
            path, data={"password": "LatePassword-9!", "confirm_password": "LatePassword-9!"}
        )
        self.assertEqual("/reset_password_request", urlsplit(late.location).path)
        self.assertEqual(stored, self._stored_hash())


if __name__ == "__main__":
    unittest.main()
