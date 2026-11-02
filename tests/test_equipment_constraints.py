"""The same equipment integrity probes on SQLite and isolated PostgreSQL schema."""
import os
import unittest
from datetime import date, timedelta
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema

from app.extensions import db
from app.models import EquipoMedicion, ComprobacionEquipo

TODAY = date(2026, 11, 2)


class ConstraintCases:
    def test_failure_evidence_due_date_and_restrict_parent(self):
        engine = create_engine(self.uri)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if engine.dialect.name == 'postgresql':
                    schema = 'equipment_' + uuid4().hex
                    connection.execute(CreateSchema(schema))
                    connection = connection.execution_options(schema_translate_map={None: schema})
                else:
                    connection.execute(text('PRAGMA foreign_keys=ON'))
                db.metadata.create_all(connection)
                parent = connection.execute(EquipoMedicion.__table__.insert().values(
                    codigo='EQ-1', nombre='Balanza')).inserted_primary_key[0]
                valid = dict(equipo_id=parent, fecha=TODAY, tipo='calibracion',
                             resultado='apto', realizado_por='Lab', referencia='Patrón',
                             fecha_proxima=TODAY + timedelta(days=1))
                for changes in [
                    dict(resultado='no_apto', fecha_proxima=None),
                    dict(resultado='no_apto', fecha_proxima=None, evaluacion_impacto=' ', acciones='X'),
                    dict(resultado='no_apto', fecha_proxima=None, evaluacion_impacto='X', acciones=' '),
                    dict(fecha_proxima=TODAY), dict(fecha_proxima=None),
                    dict(resultado=None), dict(tipo='unknown'), dict(equipo_id=999),
                ]:
                    with self.subTest(changes=changes):
                        savepoint = connection.begin_nested()
                        try:
                            with self.assertRaises(IntegrityError):
                                connection.execute(ComprobacionEquipo.__table__.insert().values(valid | changes))
                        finally:
                            savepoint.rollback()
                connection.execute(ComprobacionEquipo.__table__.insert().values(valid))
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(EquipoMedicion.__table__.delete().where(EquipoMedicion.id == parent))
            finally:
                transaction.rollback()
        engine.dispose()


class SqliteConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = 'sqlite:///:memory:'


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URI'), 'TEST_POSTGRES_URI is not set')
class PostgresConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = os.environ.get('TEST_POSTGRES_URI')
