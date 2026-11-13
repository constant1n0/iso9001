"""S5 completion gates and read-only completed projects through public services."""
from itertools import product

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from test_design_changes import ChangeCase, NO_PERSON, SEPARATION
from test_design_controls import PLAN, RECORD
from test_design_io import INPUT, OUTPUT
from test_design import TODAY, actor
from app.extensions import db
from app.models import RoleEnum
from app.services import design_controls, design_io
from app.services.actor import Actor
from app.services.errors import PermissionDenied


class DesignCompletionTests(ChangeCase):
    def started(self):
        stage = self.stage(self.project)
        self.design.transition(db.session, self.who, self.project.id, 'planificado')
        self.design.transition(db.session, self.who, self.project.id, 'en_curso')
        db.session.commit()
        return stage

    def complete(self, who=None):
        row = self.design.complete(db.session, who or self.admin,
                                   self.project.id, today=TODAY)
        db.session.commit()
        return row

    def test_each_completion_gate_has_its_own_message_and_preserves_data(self):
        self.reject(lambda: self.complete(), 'Solo puedes completar un proyecto en curso.')
        self.started()
        self.reject(lambda: self.complete(self.who), NO_PERSON)
        self.reject(lambda: self.complete(), 'Para completar el proyecto, registra al menos una salida.')
        output = self.output()
        self.reject(lambda: self.complete(), 'Aprueba todas las versiones actuales de las salidas antes de completar.')
        self.approve(output)
        plan = design_controls.plans.create(db.session, self.who,
            PLAN | {'project_id': self.project.id})
        db.session.commit()
        self.reject(lambda: self.complete(), 'Resuelve todos los controles planificados antes de completar.')
        design_controls.records.create(db.session, self.who,
            RECORD | {'project_id': self.project.id, 'planned_control_id': plan.id}, today=TODAY)
        db.session.commit()
        change = self.propose(output_ids=[output.id])
        self.reject(lambda: self.complete(), 'Resuelve todos los cambios propuestos antes de completar.')
        self.changes.reject(db.session, self.admin, change.id, reason='Not needed')
        db.session.commit()
        row = self.complete()
        self.assertEqual(('completado', TODAY, self.approver_id),
                         (row.state.name, row.completed_at, row.completed_by_id))

    def test_completion_permissions_actor_mapping_and_lock(self):
        self.started()
        self.approve(self.output())
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            if role is RoleEnum.ADMINISTRADOR and channel == 'web':
                continue
            who = Actor(self.admin.user_id, 'Actor', role, channel)
            self.reject(lambda: self.complete(who), error=PermissionDenied)
        self.design.projects.update(db.session, self.who, self.project.id,
                                     {'owner_id': self.approver_id})
        db.session.commit()
        self.reject(lambda: self.complete(), SEPARATION)
        self.design.projects.update(db.session, self.who, self.project.id, {'owner_id': 1})
        db.session.commit()
        sql = []
        def capture(state):
            if state.is_select:
                sql.append(str(state.statement.compile(dialect=postgresql.dialect())))
        session = db.session()
        event.listen(session, 'do_orm_execute', capture)
        self.addCleanup(event.remove, session, 'do_orm_execute', capture)
        self.complete()
        parent = next(i for i, s in enumerate(sql) if
                      'FROM design_projects' in s and 'FOR UPDATE' in s)
        for table in ('design_planned_controls', 'design_output_versions', 'design_changes'):
            self.assertTrue(any(i > parent and 'FROM ' + table in s
                                for i, s in enumerate(sql)), (table, sql))

    def test_completed_projects_refuse_every_dd1_dd4_write_even_noops(self):
        stage = self.started()
        input_row = design_io.inputs.create(db.session, self.who,
            INPUT | {'project_id': self.project.id})
        plan = design_controls.plans.create(db.session, self.who,
            PLAN | {'project_id': self.project.id})
        record = design_controls.records.create(db.session, self.who,
            RECORD | {'project_id': self.project.id, 'planned_control_id': plan.id}, today=TODAY)
        db.session.commit()
        output = self.approve(self.output())
        change = self.authorize(self.propose(output_ids=[output.id]))
        # Completion requires authorized changes to be applied and their versions approved.
        new_version = self.changes.apply(db.session, self.admin, change.id)[0]
        db.session.commit()
        self.approve(new_version)
        self.complete()
        for channel in ('web', 'mcp'):
            who = actor(channel=channel)
            calls = [
                lambda: self.design.projects.update(db.session, who, self.project.id, {}),
                lambda: self.design.projects.delete(db.session, who, self.project.id),
                lambda: self.design.transition(db.session, who, self.project.id, 'cancelado', cancellation_reason='No'),
                lambda: self.design.stages.create(db.session, who, dict(project_id=self.project.id, name='No', owner_id=1), today=TODAY),
                lambda: self.design.stages.update(db.session, who, stage.id, {}, today=TODAY),
                lambda: self.design.stages.delete(db.session, who, stage.id),
                lambda: design_io.inputs.create(db.session, who, INPUT | {'project_id': self.project.id}),
                lambda: design_io.inputs.update(db.session, who, input_row.id, {}),
                lambda: design_io.inputs.delete(db.session, who, input_row.id),
                lambda: design_io.outputs.create(db.session, who, OUTPUT | {'project_id': self.project.id}),
                lambda: design_io.outputs.update(db.session, who, output.id, {}),
                lambda: design_io.outputs.delete(db.session, who, output.id),
                lambda: design_controls.plans.create(db.session, who, PLAN | {'project_id': self.project.id}),
                lambda: design_controls.plans.update(db.session, who, plan.id, {}),
                lambda: design_controls.plans.delete(db.session, who, plan.id),
                lambda: design_controls.records.create(db.session, who, RECORD | {'project_id': self.project.id}, today=TODAY),
                lambda: design_controls.records.update(db.session, who, record.id, {}, today=TODAY),
                lambda: design_controls.records.delete(db.session, who, record.id),
                lambda: self.changes.changes.create(db.session, who, dict(project_id=self.project.id, output_ids=[output.id], reason='No', impact='No', actions='No')),
                lambda: self.changes.changes.update(db.session, who, change.id, {}),
                lambda: self.changes.changes.delete(db.session, who, change.id),
                lambda: self.changes.apply(db.session, who, change.id),
            ]
            for call in calls:
                # A channel permission refusal is also a refusal, with no changes.
                from app.services.errors import ValidationError
                self.reject(call, error=(ValidationError, PermissionDenied))
        for call in (lambda: self.changes.approve_output(db.session, self.admin, output.id, today=TODAY),
                     lambda: self.changes.authorize(db.session, self.admin, change.id, today=TODAY),
                     lambda: self.changes.reject(db.session, self.admin, change.id, reason='No'),
                     lambda: self.complete()):
            self.reject(call)
        self.assertEqual([output.id, new_version.id], [r.id for r in design_io.outputs.list_(
            db.session, self.who, project_id=self.project.id)])
