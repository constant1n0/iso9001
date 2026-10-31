"""Database contract for nonconforming outputs (ISO 9001 clause 8.7)."""

import os
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase
from app.extensions import db
from app.models import NoConformidad, Person, ProcesoOperacion, TratamientoSalidaNoConforme

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")


class DatabaseCases:
    def test_concession_authority_and_date_are_present_iff_treatment_is_concession(self):
        person = db.session.execute(Person.__table__.insert().values(nombre="Authority"))
        authority = person.inserted_primary_key[0]
        process = db.session.execute(ProcesoOperacion.__table__.insert().values(proceso="Production"))
        process_id = process.inserted_primary_key[0]
        db.session.commit()
        for treatment in (None, *TratamientoSalidaNoConforme):
            for authority_id, concession_date in ((None, None), (authority, None),
                                                   (None, date(2026, 10, 1)),
                                                   (authority, date(2026, 10, 1))):
                valid = ((treatment is TratamientoSalidaNoConforme.concesion)
                         == (authority_id is not None and concession_date is not None)
                         and ((authority_id is None) == (concession_date is None)))
                with self.subTest(treatment=treatment, authority=authority_id,
                                  concession_date=concession_date):
                    statement = NoConformidad.__table__.insert().values(
                        descripcion="Output", fecha_detectada=date(2026, 10, 1),
                        proceso_id=process_id, tratamiento=treatment,
                        concesion_autorizada_por_id=authority_id, fecha_concesion=concession_date)
                    if valid:
                        db.session.execute(statement)
                        db.session.commit()
                    else:
                        with self.assertRaises(IntegrityError) as caught:
                            db.session.execute(statement)
                            db.session.commit()
                        self.assertIn("ck_no_conformidades_concesion", str(caught.exception))
                        db.session.rollback()
        self.assertEqual(7, db.session.query(NoConformidad).count())


class SqliteDatabaseTestCase(DatabaseCases, ServiceBase):
    pass


@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDatabaseTestCase(DatabaseCases, unittest.TestCase):
    def setUp(self):
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self._reset_database()

    @staticmethod
    def _reset_database():
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
