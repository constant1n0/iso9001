"""Links from trainings, nonconformities and audits to people (QP-2, decision Q4).

Each register keeps its legacy free-text name and gains a nullable reference
to ``personas``: an unknown person is refused, an inactive one only when the
reference changes, and a person still cited cannot be deleted. The SQLite
cases run with the audit flush guard (``ServiceBase``). The foreign key itself
is exercised on SQLite with ``PRAGMA foreign_keys=ON`` and on PostgreSQL when
``TEST_POSTGRES_URI`` is set (CI provides it).
"""

from __future__ import annotations

import importlib
import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, errors

from app.extensions import db
from app.models import RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
ADMIN = Actor(user_id=None, label="cli", role=RoleEnum.ADMINISTRADOR, channel="cli")

# Service module, reference field, legacy text field and minimal valid data.
LINKS = (
    ("training", "persona_id", "personal",
     {"tema": "Seguridad", "fecha": date(2026, 10, 1), "personal": "Ana"}),
    ("nonconformities", "responsable_id", "responsable",
     {"descripcion": "Pieza fuera de tolerancia", "fecha_detectada": date(2026, 10, 1),
      "responsable": "Ana"}),
    ("audits", "auditor_id", "auditor",
     {"area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Ana",
      "resultado": "Sin hallazgos"}),
)


def service(name: str):
    return importlib.import_module(f"app.services.{name}")


def person_model():
    from app.models import Person

    return Person


class LinkHelpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        """Core insert: these tests must not depend on the people write API."""
        table = person_model().__table__
        result = db.session.execute(table.insert().values(nombre=nombre, activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def deactivate(self, person_id: int) -> None:
        table = person_model().__table__
        db.session.execute(table.update().where(table.c.id == person_id).values(activo=False))
        db.session.commit()

    def create(self, name: str, data: dict):
        record = service(name).create(db.session, ADMIN, data)
        db.session.commit()
        return record

    def update(self, name: str, record_id: int, data: dict):
        record = service(name).update(db.session, ADMIN, record_id, data)
        db.session.commit()
        return record

    def refused(self, call, *args):
        """The ``ValidationError`` message of a refused write; the session is rolled back."""
        with self.assertRaises(errors().ValidationError) as caught:
            call(db.session, ADMIN, *args)
        db.session.rollback()
        return caught.exception.message


class LinkTestCase(LinkHelpers, ServiceBase):
    def test_records_accept_return_and_audit_the_person(self) -> None:
        ana = self.person()
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, valid | {key: ana})
                self.assertEqual((ana, "Ana"), (getattr(record, key), getattr(record, legacy)))
                fetched = service(name).get(db.session, ADMIN, record.id)
                self.assertEqual(ana, getattr(fetched, key))
                self.assertEqual(ana, self.audit_rows()[-1].after[key])

    def test_the_person_can_be_changed_and_cleared(self) -> None:
        ana, eva = self.person(), self.person("Eva")
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                record_id = self.create(name, valid | {key: ana}).id
                self.assertEqual(eva, getattr(self.update(name, record_id, {key: eva}), key))
                row = self.audit_rows()[-1]
                self.assertEqual((ana, eva), (row.before[key], row.after[key]))
                self.assertIsNone(getattr(self.update(name, record_id, {key: None}), key))

    def test_an_unknown_or_malformed_person_is_refused(self) -> None:
        cases = (
            (999, "no corresponde a ninguna persona"),
            (0, "no corresponde a ninguna persona"),
            (2**40, "no corresponde a ninguna persona"),  # beyond a PostgreSQL integer
            ("1", "debe ser un número entero"),
            (True, "debe ser un número entero"),
        )
        for name, key, _legacy, valid in LINKS:
            record_id = self.create(name, valid).id
            for value, expected in cases:
                with self.subTest(service=name, value=value):
                    message = self.refused(service(name).create, valid | {key: value})
                    self.assertIn(expected, message)
                    self.assertIn(f"«{key}»", message)
                    message = self.refused(service(name).update, record_id, {key: value})
                    self.assertIn(expected, message)

    def test_an_inactive_person_is_refused_when_chosen(self) -> None:
        ana, luis = self.person(), self.person("Luis", activo=False)
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                message = self.refused(service(name).create, valid | {key: luis})
                self.assertIn("está desactivada", message)
                record_id = self.create(name, valid | {key: ana}).id
                message = self.refused(service(name).update, record_id, {key: luis})
                self.assertIn("está desactivada", message)

    def test_a_record_citing_a_person_later_deactivated_stays_editable(self) -> None:
        ana = self.person()
        records = {name: self.create(name, valid | {key: ana}).id
                   for name, key, _legacy, valid in LINKS}
        self.deactivate(ana)
        for name, key, legacy, _valid in LINKS:
            with self.subTest(service=name):
                updated = self.update(name, records[name], {legacy: "Ana P.", key: ana})
                self.assertEqual((ana, "Ana P."), (getattr(updated, key), getattr(updated, legacy)))
                updated = self.update(name, records[name], {legacy: "Ana Pérez"})
                self.assertEqual((ana, "Ana Pérez"),
                                 (getattr(updated, key), getattr(updated, legacy)))

    def test_the_legacy_text_is_kept_and_never_matched_to_a_person(self) -> None:
        ana = self.person("Ana")  # the same name the legacy text holds
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, valid)
                self.assertEqual(("Ana", None), (getattr(record, legacy), getattr(record, key)))
                updated = self.update(name, record.id, {key: ana})
                self.assertEqual(("Ana", ana), (getattr(updated, legacy), getattr(updated, key)))


class DeleteCases:
    """Deleting a cited person; shared by the SQLite and PostgreSQL cases below."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_a_person_cited_by_any_record_cannot_be_deleted(self) -> None:
        from app.services import people

        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                ana = self.person()
                record_id = self.create(name, valid | {key: ana}).id
                with self.assertRaises(errors().Conflict) as caught:
                    people.delete(db.session, ADMIN, ana)
                self.assertIn("desactívala en su lugar", caught.exception.message)
                db.session.rollback()
                self.assertIsNotNone(db.session.get(person_model(), ana))
                self.update(name, record_id, {key: None})
                people.delete(db.session, ADMIN, ana)
                db.session.commit()
                self.assertIsNone(db.session.get(person_model(), ana))

    def test_the_foreign_key_refuses_a_reference_the_check_did_not_see(self) -> None:
        from app.services import people

        self.enforce_foreign_keys()
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                ana = self.person()
                record_id = self.create(name, valid | {key: ana}).id
                # As if the reference were committed after the explicit check ran.
                with patch.object(people, "_is_referenced", return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        people.delete(db.session, ADMIN, ana)
                    self.assertIn("desactívala en su lugar", caught.exception.message)
                    self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                    db.session.rollback()
                    self.assertIsNotNone(db.session.get(person_model(), ana))
                    self.update(name, record_id, {key: None})  # control: no reference left
                    people.delete(db.session, ADMIN, ana)
                    db.session.commit()
                self.assertIsNone(db.session.get(person_model(), ana))


class SqliteDeleteTestCase(LinkHelpers, DeleteCases, ServiceBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDeleteTestCase(LinkHelpers, DeleteCases, unittest.TestCase):
    """The same cases against PostgreSQL, whose foreign keys are always enforced."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
