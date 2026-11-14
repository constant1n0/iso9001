"""PS3 public service rules; refused writes preserve database and audit bytes."""
from itertools import product
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from test_production import ProductionTests
from test_design import actor
from test_design_io import TODAY
from test_communication_independent import hashes
from app.extensions import db
from app.models import RoleEnum
from app.services import people, design, design_io, design_changes, qms_changes
from app.services.errors import Conflict, PermissionDenied, ValidationError

CHANGE = dict(reason='New tolerance', description='Adjust tooling', actions='Recheck',
              order_ids=[1])


class ChangeCase(ProductionTests):
    """Reuse the PS1 fixture without inheriting its test methods."""

    def setUp(self):
        super().setUp()
        self.order = self.create(owner_id=1)
        person = people.create(db.session, self.who, dict(nombre='Approver',
            user_id=self.ids[RoleEnum.ADMINISTRADOR]))
        db.session.commit()
        self.approver = type(self.who)(person.user_id, 'Approver', RoleEnum.ADMINISTRADOR, 'web')
        from app.services import production_changes
        self.changes = production_changes

    def propose(self, **values):
        row = self.changes.changes.create(db.session, self.who, CHANGE | values)
        db.session.commit()
        return row

    def decide(self, row, reject=False, who=None, **kwargs):
        call = self.changes.reject if reject else self.changes.authorize
        result = call(db.session, who or self.approver, row.id,
                      **({'reason': 'Unsuitable'} if reject else {'today': TODAY}) | kwargs)
        db.session.commit()
        return result


for _name in tuple(ProductionTests.__dict__):
    if _name.startswith('test_'):
        setattr(ChangeCase, _name, None)
del ProductionTests


class ProductionChanges(ChangeCase):
    def test_required_texts(self):
        for key in ('reason', 'description', 'actions'):
            self.reject(lambda: self.changes.changes.create(db.session, self.who,
                {k: v for k, v in CHANGE.items() if k != key}),
                f'Faltan campos obligatorios: {key}.')
            self.reject(lambda: self.propose(**{key: ' \n '}))
        row = self.propose()
        self.assertEqual(('proposed', None), (row.state.name, row.review))

    def test_at_least_one_known_order_and_normalized_set(self):
        for bad in ([], None, [True], [999], '1'):
            self.reject(lambda: self.propose(order_ids=bad))
        second = self.create()
        row = self.propose(order_ids=[second.id, 1, second.id])
        self.assertEqual([1, second.id], row.order_ids)
        self.changes.changes.update(db.session, self.who, row.id, {'order_ids': [second.id]})
        db.session.commit()
        self.assertEqual([second.id], self.changes.changes.get(db.session, self.who, row.id).order_ids)

    def test_cancelled_refused_finished_allowed_without_order_gating(self):
        row = self.propose(review='Checked')
        for target in ('in_progress', 'finished'):
            self.service.transition(db.session, self.who, 1, target)
        db.session.commit()
        self.decide(row)
        self.propose()
        other = self.create()
        pending = self.propose(order_ids=[other.id], review='Checked')
        self.service.transition(db.session, self.who, other.id, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        self.reject(lambda: self.propose(order_ids=[other.id]), self.changes.INVALID_ORDERS)
        self.reject(lambda: self.decide(pending), self.changes.INVALID_ORDERS)
        self.assertEqual('finished', self.service.orders.get(db.session, self.who, 1).state.name)

    def test_citations_design_authorized_only_qms_any_state(self):
        project = design.projects.create(db.session, self.who,
            dict(title='Design', description='Product', owner_id=1))
        output = design_io.outputs.create(db.session, self.who,
            dict(project_id=project.id, kind='other', text='Spec', acceptance_criteria='Fits'))
        db.session.commit()
        design_changes.approve_output(db.session, self.approver, output.id, today=TODAY)
        citation = design_changes.changes.create(db.session, self.who, dict(project_id=project.id,
            reason='Reason', impact='Impact', actions='Act', review='Checked', output_ids=[output.id]))
        qms = qms_changes.changes.create(db.session, self.who, dict(titulo='ERP', descripcion='ERP',
            origen='otro', proposito='Trace', responsable_id=1, fecha_propuesta=TODAY), today=TODAY)
        db.session.commit()
        self.reject(lambda: self.propose(design_change_id=citation.id), self.changes.INVALID_DESIGN)
        design_changes.authorize(db.session, self.approver, citation.id, today=TODAY)
        db.session.commit()
        row = self.propose(design_change_id=citation.id, qms_change_id=qms.id, review='Checked')
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        self.reject(lambda: qms_changes.changes.delete(db.session, self.who, qms.id),
                    qms_changes.DELETE_CONFLICT, error=Conflict)
        self.reject(lambda: design_changes.changes.delete(db.session, self.who, citation.id))
        from app.models import DesignChange, DesignChangeState
        original = self.service._lock
        def invalidate(session, record_id):
            locked = original(session, record_id)
            session.execute(DesignChange.__table__.update().where(DesignChange.id == citation.id)
                .values(state=DesignChangeState.rejected, authorized_at=None, authorized_by_id=None,
                        rejection_reason='No').execution_options(synchronize_session=False))
            return locked
        with patch.object(self.service, '_lock', side_effect=invalidate):
            self.reject(lambda: self.decide(row), self.changes.INVALID_DESIGN)
        qms_changes.cancel(db.session, self.who, qms.id, motivo='Stop', today=TODAY)
        db.session.commit()
        self.propose(qms_change_id=qms.id)
        for key in ('design_change_id', 'qms_change_id'):
            self.reject(lambda: self.propose(**{key: 999}))
        self.changes.changes.update(db.session, self.who, row.id,
            {'design_change_id': None, 'qms_change_id': None})
        db.session.commit()
        self.assertIsNone(row.design_change_id)

    def test_review_required_before_authorization(self):
        row = self.propose()
        self.reject(lambda: self.decide(row), 'Revisa el cambio antes de autorizarlo.')
        self.changes.changes.update(db.session, self.who, row.id, {'review': 'Results checked'})
        db.session.commit()
        self.decide(row)
        self.assertEqual(('authorized', TODAY, 2),
                         (row.state.name, row.authorized_at, row.authorized_by_id))

    def test_rejection_reason(self):
        row = self.propose()
        for reason in (None, '', ' \n '):
            self.reject(lambda: self.decide(row, reject=True, reason=reason),
                        'Indica el motivo del rechazo.')
        self.decide(row, reject=True)
        self.assertEqual(('rejected', 'Unsuitable', None),
                         (row.state.name, row.rejection_reason, row.authorized_by_id))

    def test_linked_person_and_separation_every_order_for_both_decisions(self):
        second = self.create(owner_id=2)
        row = self.propose(order_ids=[1, second.id], review='Checked')
        for rejecting in (False, True):
            self.reject(lambda: self.decide(row, rejecting, who=self.who), design_changes.NO_PERSON)
            self.reject(lambda: self.decide(row, rejecting), self.changes.SEPARATION)
        self.service.orders.update(db.session, self.who, second.id, {'owner_id': 1})
        db.session.commit()
        self.decide(row)

    def test_authority_and_readonly_fields(self):
        row = self.propose(review='Checked')
        for role, channel in product(RoleEnum, ('web', 'mcp', 'cli')):
            who = actor(role, channel)
            if role is RoleEnum.ADMINISTRADOR and channel == 'web':
                continue
            for rejecting in (False, True):
                self.reject(lambda: self.decide(row, rejecting, who=who), error=PermissionDenied)
        for key in ('state', 'authorized_by_id', 'authorized_at', 'rejection_reason', 'id'):
            self.reject(lambda: self.changes.changes.update(db.session, self.who, row.id, {key: 1}))
        self.reject(lambda: self.changes.changes.create(db.session, actor(RoleEnum.OPERATIVO), CHANGE),
                    error=PermissionDenied)

    def test_decided_immutable_and_never_deleted(self):
        for rejecting in (False, True):
            row = self.propose(review='Checked')
            self.decide(row, rejecting)
            for who in (self.who, actor(channel='mcp')):
                for data in ({}, {'reason': row.reason}, {'order_ids': [1]}):
                    self.reject(lambda: self.changes.changes.update(db.session, who, row.id, data),
                                design_changes.IMMUTABLE)
            self.reject(lambda: self.decide(row), design_changes.IMMUTABLE)
        row = self.propose()
        self.reject(lambda: self.changes.changes.delete(db.session, self.who, row.id),
                    self.changes.NO_DELETE)

    def test_proposed_mcp_updates_filters_and_noop_audit(self):
        who = actor(channel='mcp')
        row = self.propose()
        before = hashes()
        self.changes.changes.update(db.session, who, row.id, CHANGE)
        db.session.commit()
        self.assertEqual(before, hashes())
        self.changes.changes.update(db.session, who, row.id, {'review': 'Checked'})
        db.session.commit()
        self.assertEqual([row.id], [r.id for r in self.changes.changes.list_(
            db.session, who, order_id=1, state='proposed')])
        self.decide(row)
        self.assertEqual([], self.changes.changes.list_(db.session, who, state='authorized'))
        self.reject(lambda: self.changes.changes.get(db.session, who, row.id), error=PermissionDenied)
        self.assertEqual([row.id], [r.id for r in self.changes.changes.list_(
            db.session, self.who, state='authorized', order_id=1)])

    def test_deletion_guards_orders_and_authorizer(self):
        row = self.propose(review='Checked')
        self.reject(lambda: self.service.orders.delete(db.session, self.who, 1),
                    'No puedes eliminar una orden citada por cambios.', error=Conflict)
        self.decide(row)
        self.reject(lambda: people.delete(db.session, self.who, 2), people.STILL_REFERENCED, error=Conflict)
        unused = people.create(db.session, self.who, {'nombre': 'Unused'})
        people.delete(db.session, self.who, unused.id)
        db.session.commit()
        other = self.create()
        self.service.orders.delete(db.session, self.who, other.id)
        db.session.commit()

    def test_sorted_locks_then_refreshed_citations_and_owners(self):
        from app.models import ProductionOrder
        second = self.create()
        row = self.propose(order_ids=[second.id, 1], review='Checked')
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.statement.compile().params,
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            self.decide(row)
            locks = [entry for entry in seen if 'FROM production_orders' in entry[0]]
            self.assertEqual(2, len(locks), seen)
            self.assertTrue(all('FOR UPDATE' in sql and refresh for sql, _, refresh in locks))
            self.assertEqual([1, second.id], [next(iter(params.values())) for _, params, _ in locks])
            self.assertIn('FROM personas', seen[-1][0])
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        row = self.propose(review='Checked')
        original = self.service._lock
        def reassign(session, record_id):
            locked = original(session, record_id)
            session.execute(ProductionOrder.__table__.update().where(ProductionOrder.id == 1)
                            .values(owner_id=2))
            session.expire(locked)
            return locked
        with patch.object(self.service, '_lock', side_effect=reassign):
            self.reject(lambda: self.decide(row), self.changes.SEPARATION)
