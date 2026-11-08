"""Knowledge integrity and actual additive migration on isolated databases."""
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
             'e8a0c2d4f6b8_add_organizational_knowledge.py')


class ConstraintCases:
    def test_type_required_text_and_owner_restrict(self):
        from app.models import KnowledgeItem
        engine = create_engine(self.uri)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    if engine.dialect.name == 'postgresql':
                        schema = 'knowledge_' + uuid4().hex
                        connection.execute(CreateSchema(schema))
                        connection = connection.execution_options(schema_translate_map={None: schema})
                    else:
                        connection.execute(text('PRAGMA foreign_keys=ON'))
                    db.metadata.create_all(connection)
                    owner_id = connection.execute(Person.__table__.insert().values(
                        nombre='Owner')).inserted_primary_key[0]
                    valid = dict(kind='conocimiento', title='Title', summary='Evidence', owner_id=owner_id)
                    for bad in ({'kind': 'other'}, {'kind': None}, {'title': None},
                                {'title': ' '}, {'summary': None}, {'summary': ' '},
                                {'owner_id': None}, {'owner_id': 999}, {'retired': None}):
                        with self.subTest(bad=bad), self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(KnowledgeItem.__table__.insert().values(valid | bad))
                    connection.execute(KnowledgeItem.__table__.insert().values(valid))
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(Person.__table__.delete().where(Person.id == owner_id))
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()


class SqliteConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = 'sqlite:///:memory:'


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URI'), 'TEST_POSTGRES_URI is not set')
class PostgresConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = os.environ.get('TEST_POSTGRES_URI')


class KnowledgeMigrationTestCase(unittest.TestCase):
    def test_upgrade_downgrade_preserve_existing_data_and_match_model(self):
        from app.models import KnowledgeItem
        spec = importlib.util.spec_from_file_location('knowledge_migration', MIGRATION)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('d7f9a1b3c5e7', migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                tables = [table for table in db.metadata.sorted_tables if table is not KnowledgeItem.__table__]
                db.metadata.create_all(connection, tables=tables)
                connection.execute(Person.__table__.insert().values(nombre='Existing owner'))
                before = connection.execute(select(Person.__table__)).all()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    inspector = inspect(connection)
                    self.assertIn('knowledge_items', inspector.get_table_names())
                    self.assertEqual(
                        {c.name: (str(c.type), c.nullable) for c in KnowledgeItem.__table__.columns},
                        {c['name']: (str(c['type']), c['nullable']) for c in inspector.get_columns('knowledge_items')})
                    self.assertEqual(
                        {constraint.name for constraint in KnowledgeItem.__table__.constraints if constraint.name},
                        {c['name'] for c in inspector.get_check_constraints('knowledge_items')} |
                        {f['name'] for f in inspector.get_foreign_keys('knowledge_items')} |
                        {inspector.get_pk_constraint('knowledge_items')['name']} - {None})
                    self.assertEqual({'ix_knowledge_items_owner_id'},
                                     {i['name'] for i in inspector.get_indexes('knowledge_items')})
                    migration.downgrade()
                    self.assertNotIn('knowledge_items', inspect(connection).get_table_names())
                    self.assertEqual(before, connection.execute(select(Person.__table__)).all())
                    migration.upgrade()
                    self.assertIn('knowledge_items', inspect(connection).get_table_names())
                    self.assertEqual(before, connection.execute(select(Person.__table__)).all())
        finally:
            engine.dispose()
