"""DD3 public-service contracts: separate plans and append-only control evidence."""
from datetime import date, timedelta
from itertools import product
from pathlib import Path
from unittest.mock import patch
import importlib.util

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from test_design import DesignCase, TODAY, actor
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import people
from app.services.errors import Conflict, PermissionDenied, ValidationError

PLAN = dict(kind='review', description='Review the prototype', responsible_id=1)
RECORD = dict(kind='review', date=TODAY, responsible_id=1,
              result='conforming', evidence='Minutes M-1')
KINDS = ('review', 'verification', 'validation')
RESULTS = ('conforming', 'conforming_with_observations', 'nonconforming')


class ControlTests(DesignCase):
    """Every rejected write preserves the complete database and audit digest."""

    def setUp(self):
        super().setUp()
        from app.services import design_controls
        self.controls = design_controls
        self.project = self.create()
        self.stage_row = self.stage(self.project)
        self.other = self.create()
        self.other_stage = self.stage(self.other)

    def plan(self, **values):
        row = self.controls.plans.create(db.session, self.who,
            PLAN | {'project_id': self.project.id} | values)
        db.session.commit()
        return row

    def planned(self):
        if self.project.state.name == 'borrador':
            self.design.transition(db.session, self.who, self.project.id, 'planificado')
            db.session.commit()

    def record(self, **values):
        row = self.controls.records.create(db.session, self.who,
            RECORD | {'project_id': self.project.id} | values, today=TODAY)
        db.session.commit()
        return row

    def test_plan_kind_required_fields_stage_and_active_person(self):
        for key in PLAN:
            self.reject(lambda: self.controls.plans.create(db.session, self.who,
                {k: v for k, v in PLAN.items() if k != key} |
                {'project_id': self.project.id}))
        for data in ({'kind': 'revisión'}, {'description': ' \n '},
                     {'responsible_id': 999}, {'stage_id': self.other_stage.id},
                     {'stage_id': 999}, {'planned_date': 'tomorrow'}):
            self.reject(lambda: self.plan(**data))
        inactive = people.create(db.session, self.who, {'nombre': 'Inactive', 'activo': False})
        db.session.commit()
        self.reject(lambda: self.plan(responsible_id=inactive.id))
        for kind in KINDS:
            row = self.plan(kind=kind, stage_id=self.stage_row.id, planned_date=TODAY)
            self.assertEqual((kind, TODAY, self.stage_row.id),
                             (row.kind.name, row.planned_date, row.stage_id))
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        self.controls.plans.update(db.session, self.who, row.id, PLAN | {'kind': kind})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.controls.plans.update(db.session, self.who, row.id, {'description': 'Changed'})
        db.session.commit()
        self.assertEqual('Changed', row.description)

    def test_withdrawal_requires_justification_and_resolves_permanently(self):
        row = self.plan()
        for reason in (None, '', ' \n '):
            self.reject(lambda: self.controls.plans.update(db.session, self.who, row.id,
                {'withdrawn': True, 'withdrawal_reason': reason}),
                'Justifica por qué se retira el control planificado.')
        self.reject(lambda: self.plan(withdrawn=True, withdrawal_reason=' '))
        self.controls.plans.update(db.session, self.who, row.id,
            {'withdrawn': True, 'withdrawal_reason': '  Not applicable  '})
        db.session.commit()
        self.assertTrue(self.controls.is_resolved(db.session, row))
        self.assertEqual('Not applicable', row.withdrawal_reason)
        self.assertEqual([], self.controls.unresolved_planned_controls(db.session, self.project.id))
        self.reject(lambda: self.controls.plans.update(db.session, self.who, row.id,
            {'withdrawn': False, 'withdrawal_reason': None}))

    def test_resolution_any_conforming_result_and_repeatable_history(self):
        self.planned()
        first, second = self.plan(), self.plan(kind='verification')
        foreign = self.plan(project_id=self.other.id)
        self.assertEqual([first.id, second.id], [r.id for r in
            self.controls.unresolved_planned_controls(db.session, self.project.id)])
        for result in RESULTS[::-1]:
            row = self.record(result=result, actions='Follow up', planned_control_id=first.id)
            self.assertEqual(result != 'nonconforming', self.controls.is_resolved(db.session, first))
        self.assertEqual([second.id], [r.id for r in
            self.controls.unresolved_planned_controls(db.session, self.project.id)])
        self.assertFalse(self.controls.is_resolved(db.session, foreign))
        self.reject(lambda: self.controls.plans.update(db.session, self.who, first.id,
                                                      {'description': 'Rewrite'}))
        self.assertEqual([row.id, row.id - 1, row.id - 2], [r.id for r in
            self.controls.records.list_(db.session, self.who, project_id=self.project.id)])
        for kind in KINDS:
            for result in RESULTS:
                created = self.record(kind=kind, result=result, actions='Follow up')
                self.assertIn(created, self.controls.records.list_(db.session, self.who,
                    project_id=self.project.id, kind=kind, result=result))

    def test_record_kind_date_result_evidence_actions_links_and_person(self):
        self.planned()
        plan = self.plan()
        other_kind = self.plan(kind='validation')
        foreign = self.plan(project_id=self.other.id)
        for key in RECORD:
            self.reject(lambda: self.controls.records.create(db.session, self.who,
                {k: v for k, v in RECORD.items() if k != key} |
                {'project_id': self.project.id}, today=TODAY))
        cases = [({'kind': 'revisión'}, 'El valor del campo «kind» no es válido.'),
                 ({'date': TODAY + timedelta(days=1)}, 'La fecha del control no puede ser futura.'),
                 ({'date': None}, 'El campo «date» es obligatorio y debe ser una fecha.'),
                 ({'result': 'conforme'}, 'El valor del campo «result» no es válido.'),
                 ({'evidence': ' \n '}, 'El campo «evidence» es obligatorio.'),
                 ({'stage_id': self.other_stage.id}, 'Elige una etapa del mismo proyecto.'),
                 ({'planned_control_id': foreign.id}, 'Elige un control planificado del mismo proyecto y tipo.'),
                 ({'planned_control_id': other_kind.id}, 'Elige un control planificado del mismo proyecto y tipo.'),
                 ({'planned_control_id': 999}, 'Elige un control planificado del mismo proyecto y tipo.')]
        for result in RESULTS[1:]:
            for actions in (None, '', ' \n '):
                cases.append(({'result': result, 'actions': actions},
                              'Indica las acciones para un resultado distinto de «conforme».'))
        for data, message in cases:
            with self.subTest(data=data):
                self.reject(lambda: self.record(**data), message)
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        self.reject(lambda: self.record())
        people.update(db.session, self.who, 1, {'activo': True})
        db.session.commit()
        row = self.record(planned_control_id=plan.id, stage_id=self.stage_row.id)
        self.assertEqual((TODAY, 'Minutes M-1', None), (row.date, row.evidence, row.actions))
        self.record(date=TODAY - timedelta(days=1))  # Backdated evidence is retained.

    def test_link_validation_refreshes_a_plan_changed_before_the_parent_lock(self):
        from sqlalchemy.orm import Session
        self.planned()
        plan = self.plan()
        # Retain the old object in this caller while another writer changes it.
        with Session(db.engine) as other_session:
            self.controls.plans.update(other_session, self.who, plan.id,
                                      {'kind': 'verification'})
            other_session.commit()
        self.assertEqual('review', plan.kind.name)
        self.reject(lambda: self.record(planned_control_id=plan.id),
                    'Elige un control planificado del mismo proyecto y tipo.')
        created = self.record(kind='verification', planned_control_id=plan.id)
        self.assertEqual('verification', created.kind.name)

    def test_record_immutable_and_plans_never_deleted(self):
        self.planned()
        plan = self.plan()
        row = self.record(planned_control_id=plan.id)
        for who in (actor(), actor(channel='mcp')):
            self.reject(lambda: self.controls.records.update(db.session, who, row.id,
                {}, today=TODAY), self.controls.IMMUTABLE)
            self.reject(lambda: self.controls.records.delete(db.session, who, row.id),
                        error=PermissionDenied if who.channel == 'mcp' else ValidationError)
            self.reject(lambda: self.controls.plans.delete(db.session, who, plan.id),
                        error=PermissionDenied if who.channel == 'mcp' else ValidationError)

    def test_allowed_project_states_and_cancelled_readonly(self):
        from app.models import DesignState
        for state in DesignState:
            db.session.execute(type(self.project).__table__.update().where(
                type(self.project).id == self.project.id).values(state=state,
                cancellation_reason='Stop' if state.name == 'cancelado' else None))
            db.session.commit()
            if state.name in ('borrador', 'planificado', 'en_curso'):
                row = self.plan()
                self.controls.plans.update(db.session, self.who, row.id, {'description': 'Edit'})
                db.session.commit()
            else:
                self.reject(lambda: self.plan())
                self.reject(lambda: self.controls.plans.update(db.session, self.who, row.id,
                                                              {'description': 'No'}))
            if state.name in ('planificado', 'en_curso'):
                self.record()
            else:
                self.reject(lambda: self.record())

    def test_parent_lock_precedes_validation(self):
        self.planned()
        sql = []
        def capture(state):
            if state.is_select:
                sql.append(str(state.statement.compile(dialect=postgresql.dialect())))
        session = db.session()
        event.listen(session, 'do_orm_execute', capture)
        self.addCleanup(event.remove, session, 'do_orm_execute', capture)
        original = self.controls._values
        def checked(*args, **kwargs):
            self.assertTrue(any('FROM design_projects' in s and 'FOR UPDATE' in s for s in sql), sql)
            return original(*args, **kwargs)
        with patch.object(self.controls, '_values', side_effect=checked):
            row = self.plan()
            for call in (lambda: self.controls.plans.update(db.session, self.who, row.id,
                                                          {'description': 'Changed'}),
                         lambda: self.record()):
                sql.clear()
                call()
                db.session.commit()

    def test_stage_person_project_deletion_protection_and_preserved_empty_deletes(self):
        owner = people.create(db.session, self.who, {'nombre': 'Control owner'})
        db.session.commit()
        plan = self.plan(responsible_id=owner.id, stage_id=self.stage_row.id)
        self.reject(lambda: people.delete(db.session, self.who, owner.id), error=Conflict)
        self.reject(lambda: self.design.stages.delete(db.session, self.who, self.stage_row.id),
                    'No se puede eliminar una etapa citada por controles del diseño.')
        empty = self.create()
        self.plan(project_id=empty.id)
        self.reject(lambda: self.design.projects.delete(db.session, self.who, empty.id),
                    'No se puede eliminar un proyecto con controles del diseño.')
        self.planned()
        record_stage = self.stage(self.project, name='Evidence only')
        record_owner = people.create(db.session, self.who, {'nombre': 'Recorder'})
        db.session.commit()
        self.record(stage_id=record_stage.id, responsible_id=record_owner.id)
        self.reject(lambda: people.delete(db.session, self.who, record_owner.id), error=Conflict)
        self.reject(lambda: self.design.stages.delete(db.session, self.who, record_stage.id),
                    'No se puede eliminar una etapa citada por controles del diseño.')
        unused = self.stage(self.project)
        self.design.stages.delete(db.session, self.who, unused.id)
        people.delete(db.session, self.who, people.create(db.session, self.who,
                                                       {'nombre': 'Unused'}).id)
        self.design.projects.delete(db.session, self.who, self.create().id)
        db.session.commit()
        # An unresolved cited plan may change purpose, but never invalidate cited kind.
        unresolved = self.plan()
        self.record(result='nonconforming', actions='Retry', planned_control_id=unresolved.id)
        self.reject(lambda: self.controls.plans.update(db.session, self.who, unresolved.id,
                                                      {'kind': 'validation'}))

    def test_permission_matrix_web_and_mcp(self):
        self.planned()
        row = self.plan()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, self.controls.plans.get(db.session, who, row.id).id)
            if role is RoleEnum.OPERATIVO:
                for register, data, kwargs in ((self.controls.plans, PLAN, {}),
                                              (self.controls.records, RECORD, {'today': TODAY})):
                    self.reject(lambda: register.create(db.session, who,
                        data | {'project_id': self.project.id}, **kwargs), error=PermissionDenied)
                self.reject(lambda: self.controls.plans.update(db.session, who, row.id,
                    {'description': 'Denied'}), error=PermissionDenied)
            else:
                self.controls.plans.create(db.session, who, PLAN | {'project_id': self.project.id})
                self.controls.records.create(db.session, who, RECORD |
                                              {'project_id': self.project.id}, today=TODAY)
                db.session.commit()

    def test_additive_migration_checks_keys_indexes_and_dd1_dd2_preservation(self):
        from app.models import DesignControlRecord, DesignPlannedControl
        from app.services import design_io
        design_io.inputs.create(db.session, self.who, dict(project_id=self.project.id,
            kind='other', text='Retained input'))
        design_io.outputs.create(db.session, self.who, dict(project_id=self.project.id,
            kind='other', text='Retained output', acceptance_criteria='Works'))
        db.session.commit()
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_design_controls.py'))
        self.assertEqual(1, len(paths))
        spec = importlib.util.spec_from_file_location('dd3_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('d3f5a7b9c1e3', migration.down_revision)
        db.session.remove()
        db.metadata.drop_all(db.engine, tables=[DesignControlRecord.__table__, DesignPlannedControl.__table__])
        with db.engine.begin() as connection:
            legacy = sorted(inspect(connection).get_table_names())
            def snapshot():
                return repr([(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all())
                             for t in legacy])
            before = snapshot()
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before, snapshot())
                for table in ('design_planned_controls', 'design_control_records'):
                    info = inspect(connection)
                    self.assertTrue(info.get_pk_constraint(table)['name'])
                    indexes = [i['column_names'] for i in info.get_indexes(table)]
                    for fk in info.get_foreign_keys(table):
                        self.assertTrue(fk['name'])
                        self.assertIn(fk['constrained_columns'], indexes)
                        if fk['referred_table'] != 'users':
                            self.assertEqual(('RESTRICT', 'RESTRICT'),
                                (fk['options']['ondelete'], fk['options']['onupdate']))
                connection.exec_driver_sql("INSERT INTO design_planned_controls "
                    "(project_id,kind,description,responsible_id) VALUES (1,'review','Purpose',1)")
                connection.exec_driver_sql("INSERT INTO design_control_records "
                    "(project_id,kind,date,responsible_id,result,evidence) "
                    "VALUES (1,'review','2020-01-01',1,'conforming','Minutes')")
                for sql in ("UPDATE design_planned_controls SET kind='bad'",
                            "UPDATE design_planned_controls SET withdrawn=true",
                            "UPDATE design_planned_controls SET withdrawn=true, withdrawal_reason=' '",
                            "UPDATE design_control_records SET kind='bad'",
                            "UPDATE design_control_records SET result='bad'",
                            "UPDATE design_control_records SET result='nonconforming'",
                            "UPDATE design_control_records SET result='conforming_with_observations',actions=' '",
                            "UPDATE design_control_records SET evidence=' '"):
                    with self.assertRaises(IntegrityError):
                        with connection.begin_nested():
                            connection.exec_driver_sql(sql)
                migration.downgrade()
                self.assertEqual(legacy, sorted(inspect(connection).get_table_names()))
                self.assertEqual(before, snapshot())
