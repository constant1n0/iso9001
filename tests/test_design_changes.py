"""DD4 public contracts: approved output history and authorized design changes."""
import importlib.util
from itertools import product
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_design import DesignCase, TODAY, actor
from test_design_io import OUTPUT
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import design_io, people
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError

CHANGE = dict(reason='New requirement', impact='Dimensions change',
              review='Reviewed for safety', actions='Revise the drawing')
FROZEN = 'Una salida aprobada es de solo lectura; propone un cambio autorizado.'
SEPARATION = 'La aprobación requiere una persona distinta del responsable del proyecto.'
NO_PERSON = 'Tu usuario debe tener una persona vinculada para aprobar.'


class ChangeCase(DesignCase):
    def setUp(self):
        super().setUp()
        from app.services import design_changes
        self.changes = design_changes
        self.project = self.create()
        person = people.create(db.session, self.who, {
            'nombre': 'Approver', 'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        db.session.commit()
        self.approver_id = person.id
        self.admin = Actor(self.ids[RoleEnum.ADMINISTRADOR], 'Admin',
                           RoleEnum.ADMINISTRADOR, 'web')

    def output(self, **values):
        row = design_io.outputs.create(db.session, self.who,
            OUTPUT | {'project_id': self.project.id} | values)
        db.session.commit()
        return row

    def approve(self, output):
        self.changes.approve_output(db.session, self.admin, output.id, today=TODAY)
        db.session.commit()
        return output

    def propose(self, **values):
        if 'output_ids' not in values:
            values['output_ids'] = [self.approve(self.output()).id]
        row = self.changes.changes.create(db.session, self.who,
            CHANGE | {'project_id': self.project.id} | values)
        db.session.commit()
        return row

    def authorize(self, change):
        self.changes.authorize(db.session, self.admin, change.id, today=TODAY)
        db.session.commit()
        return change


class DesignChangeTests(ChangeCase):
    def test_approval_freezes_every_web_mcp_edit_and_delete_even_noop(self):
        output = self.output()
        self.assertEqual((1, 'draft'), (output.version, output.approval_state.name))
        design_io.outputs.update(db.session, self.who, output.id, {'text': 'Draft edit'})
        db.session.commit()
        self.approve(output)
        self.assertEqual(('approved', TODAY, self.approver_id),
            (output.approval_state.name, output.approved_at, output.approved_by_id))
        for channel in ('web', 'mcp'):
            for data in ({}, {'text': 'Rewrite'}, {'text': 'Draft edit'}):
                self.reject(lambda: design_io.outputs.update(
                    db.session, actor(channel=channel), output.id, data), FROZEN)
            self.reject(lambda: design_io.outputs.delete(
                db.session, actor(channel=channel), output.id),
                FROZEN if channel == 'web' else None,
                error=PermissionDenied if channel == 'mcp' else ValidationError)
        self.reject(lambda: self.approve(output), FROZEN)
        self.reject(lambda: people.delete(db.session, self.who, self.approver_id),
                    people.STILL_REFERENCED, error=Conflict)
        # Inputs retain their previous mutable, unversioned behavior.
        row = design_io.inputs.create(db.session, self.who, dict(
            project_id=self.project.id, kind='other', text='Input'))
        design_io.inputs.update(db.session, self.who, row.id, {'text': 'Edited'})
        db.session.commit()
        self.assertEqual('Edited', row.text)
        self.assertFalse(hasattr(row, 'version'))

    def test_approval_authorization_rejection_separation_role_and_channel(self):
        output = self.output()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            if role is RoleEnum.ADMINISTRADOR and channel == 'web':
                continue
            who = Actor(self.admin.user_id, 'Actor', role, channel)
            self.reject(lambda: self.changes.approve_output(
                db.session, who, output.id, today=TODAY), error=PermissionDenied)
        self.reject(lambda: self.changes.approve_output(
            db.session, self.who, output.id, today=TODAY), NO_PERSON)
        owner_project = self.create(owner_id=self.approver_id)
        owner_output = self.output(project_id=owner_project.id)
        self.reject(lambda: self.approve(owner_output), SEPARATION)
        change = self.propose(output_ids=[self.approve(output).id])
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            if role is RoleEnum.ADMINISTRADOR and channel == 'web':
                continue
            who = Actor(self.admin.user_id, 'Actor', role, channel)
            for call in (lambda: self.changes.authorize(db.session, who, change.id, today=TODAY),
                         lambda: self.changes.reject(db.session, who, change.id, reason='No')):
                self.reject(call, error=PermissionDenied)
        self.design.projects.update(db.session, self.who, self.project.id,
                                    {'owner_id': self.approver_id})
        db.session.commit()
        self.reject(lambda: self.authorize(change), SEPARATION)
        self.reject(lambda: self.changes.reject(db.session, self.admin, change.id, reason='No'),
                    SEPARATION)

    def test_proposals_validate_text_approved_same_project_links_and_review(self):
        approved = self.approve(self.output())
        draft = self.output()
        foreign = self.approve(self.output(project_id=self.create().id))
        base = CHANGE | {'project_id': self.project.id, 'output_ids': [approved.id]}
        for key in ('reason', 'impact', 'actions', 'output_ids'):
            self.reject(lambda: self.changes.changes.create(db.session, self.who,
                {k: v for k, v in base.items() if k != key}))
        for key in ('reason', 'impact', 'actions'):
            for value in (None, '', ' \n '):
                self.reject(lambda: self.propose(**{key: value, 'output_ids': [approved.id]}),
                            f'El campo «{key}» debe ser texto.' if value is None
                            else f'El campo «{key}» es obligatorio.')
        for ids in ([], [draft.id], [foreign.id], [99999], [True]):
            self.reject(lambda: self.propose(output_ids=ids),
                        'Elige al menos una salida aprobada actual del mismo proyecto.')
        # Review may be pending at proposal, but must be present to authorize.
        change = self.propose(output_ids=[approved.id], review=None)
        self.reject(lambda: self.authorize(change), 'Revisa el cambio antes de autorizarlo.')
        self.changes.changes.update(db.session, self.who, change.id, {'review': 'Checked'})
        db.session.commit()
        before = hashes()
        self.changes.changes.update(db.session, self.who, change.id, {'review': 'Checked'})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.authorize(change)
        self.assertEqual(('authorized', TODAY, self.approver_id),
            (change.state.name, change.authorized_at, change.authorized_by_id))

    def test_rejection_requires_reason_and_decided_changes_are_immutable(self):
        change = self.propose()
        for reason in (None, '', ' \n '):
            self.reject(lambda: self.changes.reject(db.session, self.admin,
                change.id, reason=reason), 'Indica el motivo del rechazo.')
        self.changes.reject(db.session, self.admin, change.id, reason='  Unsafe  ')
        db.session.commit()
        self.assertEqual(('rejected', 'Unsafe', None, None),
            (change.state.name, change.rejection_reason,
             change.authorized_by_id, change.authorized_at))
        for row in (change, self.authorize(self.propose())):
            for data in ({}, {'reason': 'Rewrite'}, {'state': 'proposed'}):
                self.reject(lambda: self.changes.changes.update(db.session, self.who,
                    row.id, data))
            self.reject(lambda: self.changes.changes.delete(db.session, self.who, row.id))
            self.reject(lambda: self.changes.reject(db.session, self.admin, row.id, reason='No'))
            self.reject(lambda: self.authorize(row))

    def test_apply_is_atomic_once_new_drafts_keep_original_content_and_history(self):
        originals = [self.approve(self.output()), self.approve(self.output(kind='software'))]
        change = self.propose(output_ids=[row.id for row in originals])
        self.reject(lambda: self.changes.apply(db.session, self.who, change.id),
                    'Solo puedes aplicar un cambio autorizado una vez.')
        self.authorize(change)
        before_change = self.changes.snapshot(change)
        versions = self.changes.apply(db.session, actor(RoleEnum.AUDITOR), change.id)
        db.session.commit()
        self.assertEqual(before_change, self.changes.snapshot(change))
        self.assertEqual(2, len(versions))
        for old, new in zip(originals, versions):
            self.assertNotEqual(old.id, new.id)
            self.assertEqual((2, old.id, 'draft', old.text, old.acceptance_criteria),
                (new.version, new.lineage_id, new.approval_state.name,
                 new.text, new.acceptance_criteria))
            self.assertEqual([old.id, new.id], [r.id for r in
                self.changes.history(db.session, self.who, old.id)])
            self.reject(lambda: design_io.outputs.update(db.session, self.who,
                old.id, {'text': 'Rewrite'}), FROZEN)
            design_io.outputs.update(db.session, self.who, new.id, {'text': 'New content'})
            db.session.commit()
            self.assertEqual('Drawing <A> & B', old.text)
        self.reject(lambda: self.changes.apply(db.session, self.who, change.id),
                    'Solo puedes aplicar un cambio autorizado una vez.')
        self.reject(lambda: self.changes.apply(db.session, actor(channel='mcp'), change.id),
                    error=PermissionDenied)
        self.reject(lambda: self.propose(output_ids=[originals[0].id]),
                    'Elige al menos una salida aprobada actual del mismo proyecto.')
        # Chaining another authorized change increments within the same lineage.
        self.approve(versions[0])
        next_change = self.authorize(self.propose(output_ids=[versions[0].id]))
        third = self.changes.apply(db.session, self.who, next_change.id)[0]
        db.session.commit()
        self.assertEqual((3, originals[0].id), (third.version, third.lineage_id))

    def test_downgrade_preserves_all_content_including_later_draft_versions(self):
        original = self.approve(self.output())
        change = self.authorize(self.propose(output_ids=[original.id]))
        new = self.changes.apply(db.session, self.who, change.id)[0]
        design_io.outputs.update(db.session, self.who, new.id, {'text': 'Retained new draft'})
        db.session.commit()
        self.reject(lambda: design_io.outputs.delete(db.session, self.who, new.id),
                    'Las versiones creadas por cambios no se eliminan.')
        path = next((Path(__file__).resolve().parents[1] / 'migrations/versions').glob(
            '*_add_design_changes.py'))
        spec = importlib.util.spec_from_file_location('dd4_content_downgrade', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        db.session.remove()
        with db.engine.begin() as connection:
            before = connection.exec_driver_sql('SELECT * FROM design_outputs ORDER BY id').all()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.downgrade()
                self.assertEqual(before, connection.exec_driver_sql(
                    'SELECT * FROM design_outputs ORDER BY id').all())
                migration.upgrade()
                self.assertEqual(before, connection.exec_driver_sql(
                    'SELECT * FROM design_outputs ORDER BY id').all())

    def test_stale_output_and_change_are_refreshed_after_project_lock(self):
        output = self.output()
        with Session(db.engine) as other:
            self.changes.approve_output(other, self.admin, output.id, today=TODAY)
            other.commit()
        self.reject(lambda: design_io.outputs.update(db.session, self.who,
            output.id, {'text': 'Stale overwrite'}), FROZEN)
        change = self.propose(output_ids=[output.id])
        with Session(db.engine) as other:
            self.changes.authorize(other, self.admin, change.id, today=TODAY)
            other.commit()
        self.reject(lambda: self.changes.changes.update(db.session, self.who,
            change.id, {'reason': 'Stale overwrite'}))

    def test_every_dd4_writer_locks_project_before_refreshing_citations(self):
        sql = []
        def capture(state):
            if state.is_select:
                sql.append(str(state.statement.compile(dialect=postgresql.dialect())))
        session = db.session()
        event.listen(session, 'do_orm_execute', capture)
        self.addCleanup(event.remove, session, 'do_orm_execute', capture)
        output = self.output()
        def locked(call):
            sql.clear()
            value = call()
            db.session.commit()
            parent = next(i for i, s in enumerate(sql)
                          if 'FROM design_projects' in s and 'FOR UPDATE' in s)
            refreshed = [i for i, s in enumerate(sql) if
                         'FROM design_output_versions' in s or 'FROM design_changes' in s]
            self.assertTrue(any(i > parent for i in refreshed), sql)
            return value
        locked(lambda: self.changes.approve_output(db.session, self.admin, output.id, today=TODAY))
        change = locked(lambda: self.changes.changes.create(db.session, self.who,
            CHANGE | {'project_id': self.project.id, 'output_ids': [output.id]}))
        locked(lambda: self.changes.changes.update(db.session, self.who, change.id,
                                                  {'impact': 'Updated'}))
        locked(lambda: self.changes.authorize(db.session, self.admin, change.id, today=TODAY))
        locked(lambda: self.changes.apply(db.session, self.who, change.id))
        second = self.propose(output_ids=[self.approve(self.output()).id])
        locked(lambda: self.changes.reject(db.session, self.admin, second.id, reason='Unsafe'))


class DesignChangesMigrationTests(DesignCase):
    def test_additive_backfill_checks_restrict_and_reverse_preserves_dd2_content(self):
        from app.models import DesignOutputVersion, DesignChange, design_change_outputs
        project = self.create()
        output = design_io.outputs.create(db.session, self.who,
                                          OUTPUT | {'project_id': project.id})
        db.session.commit()
        output_id = output.id
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob(
            '*_add_design_changes.py'))
        self.assertEqual(1, len(paths))
        spec = importlib.util.spec_from_file_location('dd4_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('e4a6b8c0d2f4', migration.down_revision)
        db.session.remove()
        with db.engine.begin() as connection:
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.downgrade()
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return repr([(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all())
                             for t in legacy])
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                row = connection.exec_driver_sql('SELECT output_id,lineage_id,version,state '
                    'FROM design_output_versions').one()
                self.assertEqual((output_id, output_id, 1, 'draft'), tuple(row))
                for table in ('design_changes', 'design_change_outputs', 'design_output_versions'):
                    info = inspect(connection)
                    self.assertTrue(info.get_pk_constraint(table)['name'])
                    indexes = [i['column_names'] for i in info.get_indexes(table)]
                    indexes += [info.get_pk_constraint(table)['constrained_columns']]
                    for fk in info.get_foreign_keys(table):
                        self.assertTrue(fk['name'])
                        self.assertTrue(any(cols[:1] == fk['constrained_columns'] for cols in indexes))
                        if fk['referred_table'] != 'users':
                            self.assertEqual(('RESTRICT', 'RESTRICT'),
                                (fk['options']['ondelete'], fk['options']['onupdate']))
                for sql in ("UPDATE design_output_versions SET version=0",
                            "UPDATE design_output_versions SET state='invalid'",
                            "UPDATE design_output_versions SET state='approved'",
                            "UPDATE design_output_versions SET approved_by_id=1",
                            "INSERT INTO design_changes (project_id,reason,impact,actions,state) "
                            "VALUES (1,'Why','Impact','Action','authorized')",
                            "INSERT INTO design_changes (project_id,reason,impact,actions,state) "
                            "VALUES (1,'Why','Impact','Action','rejected')"):
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.exec_driver_sql(sql)
                migration.downgrade()
                self.assertEqual(before, snapshot())
                self.assertEqual(legacy, sorted(inspect(connection).get_table_names()))
