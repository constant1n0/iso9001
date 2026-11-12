"""DD1 ordered stages: writes serialize on the parent and preserve evidence."""
from datetime import timedelta
from itertools import product

import test_design as fixtures
from test_design import TODAY, actor
from test_communication_independent import hashes
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import people
from app.services.errors import PermissionDenied


class StageTests(fixtures.DesignCase):
    """Stage contract through the public register."""

    def test_add_edit_reorder_and_delete(self):
        project = self.create()
        first = self.stage(project, name='First')
        second = self.stage(project, name='Second')
        third = self.stage(project, name='Third')
        self.assertEqual([1, 2, 3], [s.position for s in self.design.stages.list_(
            db.session, self.who, project_id=project.id)])
        self.design.stages.update(db.session, self.who, third.id, {'position': 1, 'name': 'Moved'}, today=TODAY)
        db.session.commit()
        self.assertEqual([third.id, first.id, second.id], [s.id for s in self.design.stages.list_(
            db.session, self.who, project_id=project.id)])
        self.design.stages.update(db.session, self.who, third.id, {'position': 3}, today=TODAY)
        db.session.commit()
        self.assertEqual([first.id, second.id, third.id], [s.id for s in self.design.stages.list_(
            db.session, self.who, project_id=project.id)])
        self.design.stages.delete(db.session, actor(RoleEnum.AUDITOR), second.id)
        db.session.commit()
        self.assertEqual([1, 2], [s.position for s in self.design.stages.list_(
            db.session, self.who, project_id=project.id)])
        self.assertEqual('delete', AuditLog.query.filter_by(entity_type='design_stages', entity_id=second.id)
                         .order_by(AuditLog.id.desc()).first().action)
        for bad in (0, -1, 4, True):
            self.reject(lambda: self.design.stages.update(db.session, self.who, first.id,
                        {'position': bad}, today=TODAY))
        other = self.create()
        self.reject(lambda: self.design.stages.update(db.session, self.who, first.id,
                    {'project_id': other.id}, today=TODAY))

    def test_done_date_rules_and_done_stage_frozen(self):
        project = self.create()
        for data in ({'done': True}, {'done': True, 'done_date': TODAY + timedelta(days=1)},
                     {'done_date': TODAY}, {'name': ''}, {'owner_id': None}):
            self.reject(lambda: self.stage(project, **data))
        stage = self.stage(project)
        self.design.stages.update(db.session, self.who, stage.id,
                                  {'done': True, 'done_date': TODAY}, today=TODAY)
        db.session.commit()
        self.assertTrue(stage.done)
        self.assertEqual(TODAY, stage.done_date)
        for data in ({'name': 'Changed'}, {'done': False, 'done_date': None}, {'position': 2}):
            self.reject(lambda: self.design.stages.update(db.session, self.who, stage.id, data, today=TODAY))
        self.reject(lambda: self.design.stages.delete(db.session, self.who, stage.id))
        before = hashes()
        self.design.stages.update(db.session, self.who, stage.id,
                                  {'done': True, 'done_date': TODAY}, today=TODAY)
        db.session.commit()
        self.assertEqual(before, hashes())

    def test_stage_owner_active_when_set_existing_inactive_retained(self):
        project = self.create()
        inactive = people.create(db.session, self.who, {'nombre': 'Inactive', 'activo': False})
        db.session.commit()
        self.reject(lambda: self.stage(project, owner_id=inactive.id))
        stage = self.stage(project)
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        self.design.stages.update(db.session, self.who, stage.id,
                                  {'owner_id': 1, 'name': 'Retained'}, today=TODAY)
        db.session.commit()
        self.assertEqual(1, stage.owner_id)
        self.reject(lambda: self.design.stages.update(db.session, self.who, stage.id,
                    {'owner_id': inactive.id}, today=TODAY))

    def test_cancelled_parent_freezes_all_stage_writes(self):
        project = self.create()
        stage = self.stage(project)
        self.design.transition(db.session, self.who, project.id, 'cancelado', cancellation_reason='Stopped')
        db.session.commit()
        for call in (lambda: self.stage(project),
                     lambda: self.design.stages.update(db.session, self.who, stage.id, {'name': 'Changed'}, today=TODAY),
                     lambda: self.design.stages.delete(db.session, self.who, stage.id)):
            self.reject(call, 'Un proyecto cancelado es de solo lectura.')

    def test_starter_sequence_is_opt_in(self):
        plain = self.create()
        self.assertEqual([], self.design.stages.list_(db.session, self.who, project_id=plain.id))
        started = self.create(starter_stages=True)
        stages = self.design.stages.list_(db.session, self.who, project_id=started.id)
        self.assertEqual(['Planificación', 'Entradas', 'Diseño', 'Revisión', 'Verificación', 'Validación', 'Cierre'],
                         [s.name for s in stages])
        self.assertEqual(list(range(1, 8)), [s.position for s in stages])
        self.assertTrue(all(s.owner_id == 1 and not s.done for s in stages))
        self.assertEqual(7, AuditLog.query.filter_by(entity_type='design_stages', action='create').count())
        self.reject(lambda: self.design.projects.update(db.session, self.who, started.id, {'starter_stages': True}))

    def test_rejected_done_stage_insertion_preserves_all_rows(self):
        project = self.create()
        self.stage(project)
        self.reject(lambda: self.stage(project, name='Done insertion', position=1,
                                       done=True, done_date=TODAY))

    def test_write_scope_updates_stage_without_read_scope(self):
        project = self.create()
        stage = self.stage(project)
        who = actor(RoleEnum.AUDITOR, 'mcp', frozenset({'write'}))
        self.design.stages.update(db.session, who, stage.id, {'name': 'Changed'}, today=TODAY)
        db.session.commit()
        self.assertEqual('Changed', self.design.stages.get(db.session, self.who, stage.id).name)

    def test_stage_permission_matrix(self):
        project = self.create()
        stage = self.stage(project)
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(stage.id, self.design.stages.get(db.session, who, stage.id).id)
            if role is RoleEnum.OPERATIVO:
                for call in (lambda: self.design.stages.create(db.session, who, dict(
                                project_id=project.id, name='Added', owner_id=1), today=TODAY),
                             lambda: self.design.stages.update(db.session, who, stage.id, {'name': 'Changed'}, today=TODAY)):
                    self.reject(call, error=PermissionDenied)
            else:
                added = self.design.stages.create(db.session, who, dict(
                    project_id=project.id, name='Added', owner_id=1), today=TODAY)
                self.design.stages.update(db.session, who, added.id, {'name': 'Edited'}, today=TODAY)
                db.session.commit()
                if channel == 'web':
                    self.design.stages.delete(db.session, who, added.id)
                    db.session.commit()
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.design.stages.delete(db.session, who, stage.id), error=PermissionDenied)
