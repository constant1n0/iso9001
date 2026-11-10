"""Communication schema integrity and real isolated additive migration execution."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema

from app.extensions import db
from app.models import Person

MIGRATION = (Path(__file__).resolve().parents[1] / 'migrations' / 'versions' /
             'a0c2e4f6b8d0_add_communication_plan.py')


class ConstraintCases:
    def test_required_columns_enums_unique_links_and_restrict_targets(self):
        from app.models import (COMMUNICATION_ASSOCIATIONS, CommunicationPlan,
                                Cliente, ParteInteresada, Proveedor)
        engine = create_engine(self.uri)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    if engine.dialect.name == 'postgresql':
                        schema = 'communication_' + uuid4().hex
                        connection.execute(CreateSchema(schema))
                        connection = connection.execution_options(schema_translate_map={None: schema})
                    else:
                        connection.execute(text('PRAGMA foreign_keys=ON'))
                    db.metadata.create_all(connection)
                    owner_id = connection.execute(Person.__table__.insert().values(
                        nombre='Owner')).inserted_primary_key[0]
                    valid = dict(title='Results', content='Explain results', scope='ambas',
                                 channel='web', owner_id=owner_id, frequency='Monthly',
                                 next_date=__import__('datetime').date(2026, 1, 1),
                                 audience_text='Staff')
                    for bad in ({'scope': 'other'}, {'channel': 'email'}, {'scope': None},
                                {'channel': None}, {'title': None}, {'title': ' '},
                                {'content': None}, {'content': ' '}, {'frequency': None},
                                {'frequency': ' '}, {'next_date': None}, {'owner_id': None},
                                {'owner_id': 999}, {'retired': None}):
                        with self.subTest(bad=bad), self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(CommunicationPlan.__table__.insert().values(valid | bad))
                    item_id = connection.execute(CommunicationPlan.__table__.insert().values(
                        valid)).inserted_primary_key[0]
                    targets = {'person_ids': (Person, {'nombre': 'Recipient'}),
                               'stakeholder_ids': (ParteInteresada, {'nombre': 'Party'}),
                               'customer_ids': (Cliente, {'nombre': 'Customer'}),
                               'provider_ids': (Proveedor, {'nombre': 'Provider', 'tipo_suministro': 'producto'})}
                    for key, (model, values) in targets.items():
                        target_id = connection.execute(model.__table__.insert().values(
                            values)).inserted_primary_key[0]
                        table = COMMUNICATION_ASSOCIATIONS[key]
                        link = dict(communication_id=item_id, target_id=target_id)
                        connection.execute(table.insert().values(link))
                        with self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(table.insert().values(link))
                        pk = inspect(model).primary_key[0]
                        with self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(model.__table__.delete().where(pk == target_id))
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(Person.__table__.delete().where(Person.id == owner_id))
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()


class SqliteCommunicationConstraints(ConstraintCases, unittest.TestCase):
    uri = 'sqlite:///:memory:'


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URI'), 'TEST_POSTGRES_URI is not set')
class PostgresCommunicationConstraints(ConstraintCases, unittest.TestCase):
    uri = os.environ.get('TEST_POSTGRES_URI')


class CommunicationMigrationTestCase(unittest.TestCase):
    def test_upgrade_downgrade_preserves_existing_data_and_matches_models(self):
        from app.models import COMMUNICATION_ASSOCIATIONS, CommunicationPlan
        spec = importlib.util.spec_from_file_location('communication_migration', MIGRATION)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('f9b1d3e5a7c9', migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        tables = [CommunicationPlan.__table__, *COMMUNICATION_ASSOCIATIONS.values()]
        try:
            with engine.begin() as connection:
                db.metadata.create_all(connection, tables=[t for t in db.metadata.sorted_tables
                                                           if t not in tables])
                connection.execute(Person.__table__.insert().values(nombre='Existing owner'))
                before = connection.execute(select(Person.__table__)).all()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    inspector = inspect(connection)
                    for table in tables:
                        self.assertIn(table.name, inspector.get_table_names())
                        self.assertEqual({c.name: (str(c.type), c.nullable) for c in table.columns},
                                         {c['name']: (str(c['type']), c['nullable'])
                                          for c in inspector.get_columns(table.name)})
                        self.assertEqual({i.name for i in table.indexes},
                                         {i['name'] for i in inspector.get_indexes(table.name)})
                        self.assertEqual({c.name for c in table.constraints if c.name},
                                         {c['name'] for c in inspector.get_check_constraints(table.name)} |
                                         {f['name'] for f in inspector.get_foreign_keys(table.name)} |
                                         {inspector.get_pk_constraint(table.name)['name']} - {None})
                    self.assertEqual(before, connection.execute(select(Person.__table__)).all())
                    migration.downgrade()
                    self.assertFalse({t.name for t in tables} & set(inspect(connection).get_table_names()))
                    self.assertEqual(before, connection.execute(select(Person.__table__)).all())
                    migration.upgrade()
                    self.assertIn('communication_plan', inspect(connection).get_table_names())
        finally:
            engine.dispose()
