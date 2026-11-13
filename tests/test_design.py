"""DD1 service contract: projects, lifecycle, links and permissions."""
from datetime import date
from itertools import product
import unittest

from register_routes import RegisterRoutesBase
from test_communication_independent import hashes
from app.extensions import db
from app.models import AuditLog, Person, RoleEnum
from app.services import clientes, people, process_operations
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError

TODAY = date(2026, 10, 20)
PROJECT = dict(title='New packaging', description='Recyclable container', owner_id=1)


def actor(role=RoleEnum.ADMINISTRADOR, channel='web', scopes=None):
    return Actor(None, 'Designer', role, channel, scopes)


class DesignCase(RegisterRoutesBase):
    """Audited database fixture; all project operations use the public service."""

    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        from app.services import design
        self.design = design
        self.who = actor()

    def create(self, **values):
        row = self.design.projects.create(db.session, self.who, PROJECT | values)
        db.session.commit()
        return row

    def stage(self, project, **values):
        row = self.design.stages.create(db.session, self.who, dict(
            project_id=project.id, name='Prototype', owner_id=1) | values, today=TODAY)
        db.session.commit()
        return row

    def reject(self, operation, message=None, error=ValidationError):
        """A rejection has its exact message and leaves every DB/audit byte unchanged."""
        before = hashes()
        with self.assertRaises(error) as caught:
            operation()
        if message is not None:
            self.assertEqual(message, str(caught.exception))
        self.assertEqual(before, hashes())
        db.session.rollback()


class DesignTests(DesignCase):
    """Project rules, including every DD1 lifecycle edge."""

    def test_required_fields_and_date_order(self):
        for key in PROJECT:
            for value in (None, '' if key != 'owner_id' else 0):
                self.reject(lambda: self.create(**{key: value}))
            self.reject(lambda: self.design.projects.create(
                db.session, self.who, {k: v for k, v in PROJECT.items() if k != key}))
        self.reject(lambda: self.create(planned_start=TODAY, planned_end=date(2026, 10, 19)),
                    'La fecha final prevista no puede ser anterior a la fecha inicial.')
        row = self.create(planned_start=TODAY, planned_end=TODAY)
        self.assertEqual('borrador', row.state.name)
        self.assertEqual([], self.design.stages.list_(db.session, self.who, project_id=row.id))
        self.reject(lambda: self.design.projects.update(
            db.session, self.who, row.id, {'planned_start': date(2026, 10, 21)}))

    def test_owner_active_when_set_and_existing_inactive_kept(self):
        inactive = people.create(db.session, self.who, {'nombre': 'Inactive', 'activo': False})
        db.session.commit()
        self.reject(lambda: self.create(owner_id=inactive.id))
        row = self.create()
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        self.design.projects.update(db.session, self.who, row.id, PROJECT | {'title': 'Edited'})
        db.session.commit()
        self.assertEqual(1, row.owner_id)
        self.reject(lambda: self.design.projects.update(
            db.session, self.who, row.id, {'owner_id': inactive.id}))

    def test_customer_and_process_sets_replace_clear_and_audit(self):
        customer_ids = []
        process_ids = []
        for n in (1, 2):
            customer_ids.append(clientes.create(db.session, self.who, {'nombre': f'C{n}'}).id)
            process_ids.append(process_operations.create(
                db.session, self.who, {'proceso': f'P{n}'}).id_proceso)
        db.session.commit()
        row = self.create(customer_id=customer_ids[0], process_ids=process_ids)
        self.assertEqual(process_ids, row.process_ids)
        log = AuditLog.query.filter_by(entity_type='design_projects', action='create').one()
        self.assertEqual(process_ids, log.after['process_ids'])
        for ids, customer in ((process_ids[1:], customer_ids[1]), ([], None)):
            self.design.projects.update(db.session, self.who, row.id,
                                        {'process_ids': ids, 'customer_id': customer})
            db.session.commit()
            self.assertEqual(ids, row.process_ids)
            self.assertEqual(customer, row.customer_id)
            log = AuditLog.query.filter_by(entity_type='design_projects').order_by(AuditLog.id.desc()).first()
            self.assertEqual(ids, log.after['process_ids'])
        for data in ({'process_ids': [999]}, {'process_ids': [True]}, {'customer_id': 999},
                     {'title': 'Must not persist', 'process_ids': [999]}):
            self.reject(lambda: self.design.projects.update(db.session, self.who, row.id, data))
        before = hashes()
        self.design.projects.update(db.session, self.who, row.id, {'process_ids': [], 'customer_id': None})
        db.session.commit()
        self.assertEqual(before, hashes())

    def test_each_transition_and_gated_completion(self):
        from app.models import DesignState
        from app.services import design_changes, design_io
        person = people.create(db.session, self.who, {'nombre': 'Approver',
            'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        db.session.commit()
        approver = Actor(self.ids[RoleEnum.ADMINISTRADOR], 'Approver',
                         RoleEnum.ADMINISTRADOR, 'web')
        allowed = {('borrador', 'planificado'), ('planificado', 'en_curso'),
                   ('borrador', 'cancelado'), ('planificado', 'cancelado'),
                   ('en_curso', 'cancelado'), ('en_curso', 'completado')}
        for source, target in product(DesignState, repeat=2):
            row = self.create()
            self.stage(row)
            # Fixture state injection is not an application write path.
            db.session.execute(type(row).__table__.update().where(type(row).id == row.id).values(
                state=source, cancellation_reason='Cancelled fixture' if source.name == 'cancelado' else None))
            db.session.commit()
            if (source.name, target.name) in allowed:
                if target is DesignState.completado:
                    output = design_io.outputs.create(db.session, self.who, dict(
                        project_id=row.id, kind='other', text='Output', acceptance_criteria='Works'))
                    design_changes.approve_output(db.session, approver, output.id, today=TODAY)
                    db.session.commit()
                before = AuditLog.query.count()
                self.design.transition(db.session, approver, row.id, target.name,
                                       cancellation_reason='  Duplicate project  ', today=TODAY)
                db.session.commit()
                self.assertEqual(target, row.state)
                self.assertEqual(before + 1, AuditLog.query.count())
                if target.name == 'cancelado':
                    self.assertEqual('Duplicate project', row.cancellation_reason)
            else:
                self.reject(lambda: self.design.transition(
                    db.session, self.who, row.id, target.name, cancellation_reason='Reason'))

    def test_planning_requires_stage_and_cancel_requires_nonblank_reason(self):
        row = self.create()
        self.reject(lambda: self.design.transition(db.session, self.who, row.id, 'planificado'),
                    'Para planificar el proyecto, registra al menos una etapa.')
        for reason in (None, '', ' \n '):
            self.reject(lambda: self.design.transition(
                db.session, self.who, row.id, 'cancelado', cancellation_reason=reason),
                'Indica el motivo de la cancelación.')
        self.design.transition(db.session, self.who, row.id, 'cancelado', cancellation_reason='Stopped')
        db.session.commit()
        self.reject(lambda: self.design.projects.update(db.session, self.who, row.id, {'title': 'Changed'}),
                    'Un proyecto cancelado es de solo lectura.')
        before = hashes()
        self.design.projects.update(db.session, self.who, row.id, PROJECT)
        db.session.commit()
        self.assertEqual(before, hashes())

    def test_permissions_web_mcp_and_draft_only_deletion(self):
        row = self.create()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, self.design.projects.get(db.session, who, row.id).id)
            self.assertIn(row, self.design.projects.list_(db.session, who))
            if role is RoleEnum.OPERATIVO:
                for call in (lambda: self.design.projects.create(db.session, who, PROJECT),
                             lambda: self.design.projects.update(db.session, who, row.id, {'title': 'Changed'})):
                    self.reject(call, error=PermissionDenied)
            else:
                created = self.design.projects.create(db.session, who, PROJECT)
                self.design.projects.update(db.session, who, created.id, {'title': 'Changed'})
                db.session.commit()
                self.assertEqual('Changed', created.title)
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.design.transition(db.session, who, row.id, 'cancelado',
                                                          cancellation_reason='Stop'), error=PermissionDenied)
            if channel == 'mcp' or role is not RoleEnum.ADMINISTRADOR:
                self.reject(lambda: self.design.projects.delete(db.session, who, row.id), error=PermissionDenied)
        for key in ('state', 'cancellation_reason'):
            self.reject(lambda: self.design.projects.update(db.session, self.who, row.id, {key: 'completado'}))
        self.stage(row)
        self.reject(lambda: self.design.projects.delete(db.session, self.who, row.id))
        self.design.transition(db.session, self.who, row.id, 'planificado')
        db.session.commit()
        self.reject(lambda: self.design.projects.delete(db.session, self.who, row.id))
        draft = self.create()
        self.design.projects.delete(db.session, self.who, draft.id)
        db.session.commit()
        self.assertEqual('delete', AuditLog.query.order_by(AuditLog.id.desc()).first().action)

    def test_filters(self):
        customer = clientes.create(db.session, self.who, {'nombre': 'Customer'})
        db.session.commit()
        first = self.create(customer_id=customer.id)
        second = self.create()
        for filters, expected in (({'state': 'borrador'}, [first.id, second.id]),
                                  ({'owner_id': 1}, [first.id, second.id]),
                                  ({'customer_id': customer.id}, [first.id])):
            self.assertEqual(expected, [p.id for p in self.design.projects.list_(db.session, self.who, **filters)])


if __name__ == '__main__':
    unittest.main()
