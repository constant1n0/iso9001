"""Additive DD1 migration and database invariant probes, runnable on SQLite."""
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from register_routes import RegisterRoutesBase
from app.extensions import db
from app.services import people


class DesignConstraints(RegisterRoutesBase):
    def test_additive_upgrade_downgrade_preserves_existing_rows(self):
        from app.models import DesignProject, DesignStage, design_processes
        self.seed_with(people, {'nombre': 'Preserved'})
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_design_projects.py'))
        self.assertEqual(1, len(paths))
        spec = importlib.util.spec_from_file_location('design_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('b1d3e5f7a9c1', migration.down_revision)
        with self.app.app_context():
            tables = [design_processes, DesignStage.__table__, DesignProject.__table__]
            db.metadata.drop_all(db.engine, tables=tables)
            with db.engine.begin() as connection:
                legacy = inspect(connection).get_table_names()
                def digest():
                    rows = [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all())
                            for name in sorted(legacy)]
                    return hashlib.sha256(repr(rows).encode()).hexdigest()
                before = digest()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(before, digest())
                    for table in tables:
                        info = inspect(connection)
                        self.assertTrue(info.get_pk_constraint(table.name)['name'])
                        for fk in info.get_foreign_keys(table.name):
                            self.assertTrue(fk['name'])
                            if fk['referred_table'] == 'users':
                                continue
                            self.assertEqual('RESTRICT', fk['options']['ondelete'])
                            self.assertEqual('RESTRICT', fk['options']['onupdate'])
                            indexed = [i['column_names'] for i in info.get_indexes(table.name)]
                            indexed += [info.get_pk_constraint(table.name)['constrained_columns']]
                            self.assertTrue(any(cols[:1] == fk['constrained_columns'] for cols in indexed))
                    connection.exec_driver_sql("INSERT INTO design_projects (title, description, owner_id) VALUES ('Design', 'Product', 1)")
                    connection.exec_driver_sql("INSERT INTO design_stages (project_id, name, owner_id, position) VALUES (1, 'Plan', 1, 1)")
                    for sql in ("UPDATE design_projects SET state='unknown'",
                                "UPDATE design_projects SET state='cancelado'",
                                "UPDATE design_projects SET state='cancelado', cancellation_reason='   '",
                                "UPDATE design_projects SET planned_start='2026-02-01', planned_end='2026-01-01'",
                                "UPDATE design_stages SET done=1",
                                "UPDATE design_stages SET position=0",
                                "INSERT INTO design_stages (project_id,name,owner_id,position) VALUES (1,'Duplicate',1,1)"):
                        with self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.exec_driver_sql(sql)
                    migration.downgrade()
                    self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                    self.assertEqual(before, digest())
