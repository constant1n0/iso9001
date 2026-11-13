"""DD2 public service rules and additive migration contract (S3, S5)."""
import importlib.util
from itertools import product
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from test_design import DesignCase, actor, TODAY
from test_communication_independent import hashes
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import documents, document_revisions, people
from app.services.errors import PermissionDenied

INPUT_TYPES = {
    'functional_performance': 'funcional o de desempeño',
    'legal_regulatory': 'legal o reglamentario',
    'prior_knowledge': 'conocimiento previo',
    'failure_consequences': 'consecuencias de fallo',
    'other': 'otro',
}
OUTPUT_TYPES = {
    'specification': 'especificación', 'drawing': 'plano o dibujo',
    'procedure_instruction': 'procedimiento o instrucción',
    'prototype_sample': 'prototipo o muestra', 'software': 'software', 'other': 'otro',
}
INPUT = dict(kind='functional_performance', text='Needs <A> & B')
OUTPUT = dict(kind='specification', text='Drawing <A> & B',
              acceptance_criteria='Tolerance <= 1 mm')
INVALID_REVISION = 'Elige una revisión aprobada o vigente que puedas consultar.'


def approved_revision(who, code='SECRET-42', publish=False):
    """Create a controlled revision through its public workflow."""
    approver = people.create(db.session, who, {'nombre': code + ' approver'})
    doc = documents.create(db.session, who, dict(
        title='Restricted title ' + code, code=code, category='OTRO',
        owner_id=1, author_id=1, content='Restricted revision content',
        change_summary='Initial issue'))
    revision = document_revisions.list_(db.session, who, doc.id)[0]
    document_revisions.submit(db.session, who, revision.id)
    document_revisions.approve(db.session, who, revision.id,
                               approver_id=approver.id, today=TODAY)
    if publish:
        document_revisions.publish(db.session, who, revision.id, today=TODAY)
    db.session.commit()
    return doc.id, revision.id


class DesignIOTests(DesignCase):
    def register(self, name):
        from app.services import design_io
        return getattr(design_io, name)

    def add(self, name='inputs', **values):
        base = INPUT if name == 'inputs' else OUTPUT
        row = self.register(name).create(db.session, self.who,
                                         base | {'project_id': self.pid} | values)
        db.session.commit()
        return row

    def setUp(self):
        super().setUp()
        self.pid = self.create().id

    def test_input_closed_enum_and_required_text(self):
        from app.models import DesignInputType
        self.assertEqual(INPUT_TYPES, {e.name: e.value for e in DesignInputType})
        register = self.register('inputs')
        for kind in INPUT_TYPES:
            row = self.add(kind=kind)
            self.assertEqual(kind, register.get(db.session, self.who, row.id).kind.name)
        for key, bad in (('kind', None), ('kind', ''), ('kind', 'unknown'),
                         ('text', ''), ('text', ' \n '), ('text', None)):
            self.reject(lambda: self.add(**{key: bad}))
        for key in ('kind', 'text'):
            self.reject(lambda: register.create(db.session, self.who,
                {k: v for k, v in (INPUT | {'project_id': self.pid}).items() if k != key}),
                f'Faltan campos obligatorios: {key}.')
        row = self.add()
        self.reject(lambda: register.update(db.session, self.who, row.id,
                                            {'text': 'Changed', 'kind': 'invalid'}),
                    'El valor del campo «kind» no es válido.')

    def test_output_closed_enum_text_and_acceptance_required(self):
        from app.models import DesignOutputType
        self.assertEqual(OUTPUT_TYPES, {e.name: e.value for e in DesignOutputType})
        register = self.register('outputs')
        for kind in OUTPUT_TYPES:
            row = self.add('outputs', kind=kind)
            self.assertEqual(kind, register.get(db.session, self.who, row.id).kind.name)
        for key in ('text', 'acceptance_criteria', 'kind'):
            for value in (None, '', ' \n '):
                self.reject(lambda: self.add('outputs', **{key: value}))
            self.reject(lambda: register.create(db.session, self.who,
                {k: v for k, v in (OUTPUT | {'project_id': self.pid}).items() if k != key}),
                f'Faltan campos obligatorios: {key}.')
        row = self.add('outputs')
        self.reject(lambda: register.update(db.session, self.who, row.id,
                    {'text': 'Changed', 'acceptance_criteria': ''}),
                    'El campo «acceptance_criteria» es obligatorio.')
        self.reject(lambda: self.add('outputs', kind='custom'),
                    'El valor del campo «kind» no es válido.')

    def test_revision_policy_new_links_and_retained_obsolete_links(self):
        doc_id, revision_id = approved_revision(self.who)
        for name in ('inputs', 'outputs'):
            row = self.add(name, revision_id=revision_id)
            self.assertEqual(revision_id, self.register(name).get(
                db.session, self.who, row.id).revision_id)
        document_revisions.publish(db.session, self.who, revision_id, today=TODAY)
        draft = document_revisions.start_draft(db.session, self.who, doc_id,
                                               {'author_id': 1})
        db.session.commit()
        for name in ('inputs', 'outputs'):
            for bad in (99999, draft.id):
                self.reject(lambda: self.add(name, revision_id=bad), INVALID_REVISION)
        document_revisions.edit_draft(db.session, self.who, draft.id,
                                       {'change_summary': 'Next version'})
        document_revisions.submit(db.session, self.who, draft.id)
        db.session.commit()
        for name in ('inputs', 'outputs'):
            self.reject(lambda: self.add(name, revision_id=draft.id), INVALID_REVISION)
            row = self.add(name, revision_id=revision_id)
            document_revisions.withdraw(db.session, self.who, doc_id, 'Withdrawn', today=TODAY)
            db.session.commit()
            before = hashes()
            self.register(name).update(db.session, self.who, row.id,
                                      {'revision_id': revision_id})
            db.session.commit()
            self.assertEqual(before, hashes())
            self.register(name).update(db.session, self.who, row.id,
                                      {'text': 'Retained citation', 'revision_id': revision_id})
            db.session.commit()
            self.assertEqual(revision_id, self.register(name).get(
                db.session, self.who, row.id).revision_id)
            self.reject(lambda: self.add(name, revision_id=revision_id), INVALID_REVISION)
            self.register(name).update(db.session, self.who, row.id, {'revision_id': None})
            db.session.commit()
            self.assertIsNone(row.revision_id)
            # A separate controlled document permits the second register's same probe.
            doc_id, revision_id = approved_revision(self.who, code=name, publish=True)

    def test_unreadable_revision_refused_without_data_or_audit_change(self):
        from app.services import design_io
        _, rid = approved_revision(self.who)
        # Keep the real document reader as the authority; simulate a narrowed visibility
        # policy (maintaining roles normally read all revisions).
        with patch.object(document_revisions, 'get', side_effect=PermissionDenied()):
            for name in ('inputs', 'outputs'):
                self.reject(lambda: self.add(name, revision_id=rid), INVALID_REVISION)
        self.assertIsNone(design_io.visible_revision(db.session, actor(RoleEnum.OPERATIVO), rid))

    def test_cancelled_parent_rejects_every_write_and_move(self):
        second = self.create().id
        rows = {name: self.add(name).id for name in ('inputs', 'outputs')}
        for name, rid in rows.items():
            self.reject(lambda: self.register(name).update(db.session, self.who, rid,
                        {'project_id': second}), 'Un elemento no puede pasar a otro proyecto.')
        self.design.transition(db.session, self.who, self.pid, 'cancelado',
                               cancellation_reason='Stopped')
        db.session.commit()
        for name, rid in rows.items():
            for call in (lambda: self.add(name),
                         lambda: self.register(name).update(db.session, self.who, rid,
                                                            {'text': 'Changed'}),
                         lambda: self.register(name).delete(db.session, self.who, rid)):
                self.reject(call, self.design.READ_ONLY)
            before = hashes()
            self.register(name).update(db.session, self.who, rid, {})
            db.session.commit()
            self.assertEqual(before, hashes())

    def test_every_write_locks_parent_with_for_update(self):
        statements = []
        def capture(state):
            if state.is_select:
                sql = str(state.statement.compile(dialect=postgresql.dialect()))
                if 'FROM design_projects' in sql:
                    statements.append(sql)
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            for name in ('inputs', 'outputs'):
                for operation in ('create', 'update', 'delete'):
                    statements.clear()
                    if operation == 'create':
                        row = self.add(name)
                    elif operation == 'update':
                        self.register(name).update(db.session, self.who, row.id,
                                                   {'text': 'Changed'})
                        db.session.commit()
                    else:
                        self.register(name).delete(db.session, self.who, row.id)
                        db.session.commit()
                    self.assertTrue(any('FOR UPDATE' in sql for sql in statements),
                                    (name, operation, statements))
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)

    def test_role_channel_matrix_audit_noops_and_project_delete_guard(self):
        for name in ('inputs', 'outputs'):
            register = self.register(name)
            row = self.add(name)
            for role, channel in product(RoleEnum, ('web', 'mcp')):
                who = actor(role, channel)
                self.assertEqual(row.id, register.get(db.session, who, row.id).id)
                if role is RoleEnum.OPERATIVO:
                    for call in (lambda: register.create(db.session, who,
                                 INPUT | {'project_id': self.pid}),
                                 lambda: register.update(db.session, who, row.id,
                                                         {'text': 'Changed'})):
                        self.reject(call, error=PermissionDenied)
                else:
                    created = register.create(db.session, who,
                        (INPUT if name == 'inputs' else OUTPUT) | {'project_id': self.pid})
                    register.update(db.session, who, created.id, {'text': 'Edited'})
                    db.session.commit()
                    self.assertEqual('Edited', register.get(db.session, who, created.id).text)
                if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                    self.reject(lambda: register.delete(db.session, who, row.id),
                                error=PermissionDenied)
            before = hashes()
            data = (INPUT if name == 'inputs' else OUTPUT) | {'project_id': self.pid,
                                                            'revision_id': None}
            register.update(db.session, self.who, row.id, data)
            db.session.commit()
            self.assertEqual(before, hashes())
            self.reject(lambda: self.design.projects.delete(db.session, self.who, self.pid),
                        'No se puede eliminar un proyecto con entradas o salidas.')
            register.delete(db.session, actor(RoleEnum.AUDITOR), row.id)
            db.session.commit()
            self.assertNotIn(row.id, [r.id for r in register.list_(db.session, self.who)])
            log = AuditLog.query.order_by(AuditLog.id.desc()).first()
            self.assertEqual(('delete', 'web', row.id), (log.action, log.channel, log.entity_id))
            self.assertEqual(data['text'], log.before['text'])

    def test_empty_draft_deletion_still_preserves_its_audit_snapshot(self):
        empty = self.create(title='Unused draft')
        empty_id = empty.id
        self.design.projects.delete(db.session, self.who, empty_id)
        db.session.commit()
        self.assertNotIn(empty_id, [p.id for p in self.design.projects.list_(
            db.session, self.who)])
        log = AuditLog.query.order_by(AuditLog.id.desc()).first()
        self.assertEqual(('delete', 'Unused draft'), (log.action, log.before['title']))

    def test_filters_by_project_and_type(self):
        other = self.create().id
        for name, types in (('inputs', INPUT_TYPES), ('outputs', OUTPUT_TYPES)):
            rows = {kind: self.add(name, kind=kind).id for kind in types}
            self.add(name, project_id=other)
            register = self.register(name)
            for kind, rid in rows.items():
                self.assertEqual([rid], [r.id for r in register.list_(
                    db.session, self.who, project_id=self.pid, kind=kind)])
                page, total = register.list_page(db.session, self.who,
                                                 project_id=self.pid, kind=kind)
                self.assertEqual((1, [rid]), (total, [r.id for r in page]))


class DesignIOMigrationTests(DesignCase):
    def test_upgrade_checks_restrict_and_downgrade_preserve_dd1_rows(self):
        from app.models import DesignInput, DesignOutput
        project = self.create()
        self.stage(project)
        _, rid = approved_revision(self.who, publish=True)
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob(
            '*_add_design_inputs_outputs.py'))
        self.assertEqual(1, len(paths))
        spec = importlib.util.spec_from_file_location('design_io_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('c2e4f6a8b0d2', migration.down_revision)
        tables = [DesignOutput.__table__, DesignInput.__table__]
        project_id = project.id
        db.session.remove()
        db.metadata.drop_all(db.engine, tables=tables)
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return [(name, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all())
                        for name in legacy]
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                for model, values in ((DesignInput, INPUT), (DesignOutput, OUTPUT)):
                    table = model.__table__
                    info = inspect(connection)
                    self.assertTrue(info.get_pk_constraint(table.name)['name'])
                    checks = info.get_check_constraints(table.name)
                    self.assertIn(f'ck_{table.name}_kind', {c['name'] for c in checks})
                    for fk in info.get_foreign_keys(table.name):
                        self.assertTrue(fk['name'])
                        self.assertIn(fk['constrained_columns'],
                                      [i['column_names'] for i in info.get_indexes(table.name)])
                        if fk['referred_table'] != 'users':
                            self.assertEqual('RESTRICT', fk['options']['ondelete'])
                            self.assertEqual('RESTRICT', fk['options']['onupdate'])
                    connection.execute(table.insert().values(
                        **values, project_id=project_id, revision_id=rid))
                    for key in values:
                        for bad in (None, '', 'invalid' if key == 'kind' else '   '):
                            with self.assertRaises(IntegrityError):
                                with connection.begin_nested():
                                    connection.execute(table.update().values(**{key: bad}))
                migration.downgrade()
                self.assertEqual(set(legacy), set(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())

    def test_cited_revision_and_parent_cannot_be_deleted(self):
        from app.models import DocumentRevision, DesignProject
        from app.services import design_io
        self.pid = self.create().id
        _, rid = approved_revision(self.who, publish=True)
        for name, values in (('inputs', INPUT), ('outputs', OUTPUT)):
            getattr(design_io, name).create(db.session, self.who,
                values | {'project_id': self.pid, 'revision_id': rid})
        db.session.commit()
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        for model, key in ((DocumentRevision, rid), (DesignProject, self.pid)):
            with self.assertRaises(IntegrityError):
                db.session.execute(model.__table__.delete().where(model.id == key))
            db.session.rollback()
