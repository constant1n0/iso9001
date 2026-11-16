"""Independent T2 regressions: S3-S5 and S1/S2 order invariants."""
from datetime import timedelta
from unittest.mock import patch
from flask import g
import test_product_release_decision as support
from test_communication_independent import hashes
from app.extensions import db
from app.models import Person, ProductionOrder, ReleaseVerification
from app.services import audit, people, product_release as service


class IndependentDecision(support.DecisionCase):
    def test_s3_empty_and_mixed_evidence_exact_refusals(self):
        row = self.release(self.finished(owner_id=1))
        self.reject(lambda: self.authorize(row),
                    'Añade al menos una verificación antes de autorizar.')
        self.verification(row)
        self.verification(row, result='nonconforming')
        self.reject(lambda: self.authorize(row),
                    'Hay verificaciones no conformes; registra una concesión para autorizar.')

    def test_s3_warm_verification_changed_after_release_lock(self):
        row = self.checked()
        check = service.verifications.list_(db.session, self.who, release_id=row.id)[0]
        self.assertEqual('conforming', check.result.name)
        original = service._lock
        def changed(session, record_id):
            locked = original(session, record_id)
            session.execute(ReleaseVerification.__table__.update().where(
                ReleaseVerification.id == check.id).values(result='nonconforming')
                .execution_options(synchronize_session=False))
            self.assertEqual('conforming', check.result.name)
            return locked
        with patch.object(service, '_lock', side_effect=changed):
            self.reject(lambda: self.authorize(row),
                        'Hay verificaciones no conformes; registra una concesión para autorizar.')

    def test_s5_owner_and_user_link_rechecked_after_lock(self):
        row = self.checked()
        original = service._lock
        def owner_changed(session, record_id):
            locked = original(session, record_id)
            session.execute(ProductionOrder.__table__.update().where(
                ProductionOrder.id == row.order_id).values(owner_id=self.decider.id)
                .execution_options(synchronize_session=False))
            return locked
        def link_changed(session, record_id):
            locked = original(session, record_id)
            session.execute(Person.__table__.update().where(Person.id == self.decider.id)
                .values(user_id=None).execution_options(synchronize_session=False))
            session.execute(Person.__table__.update().where(Person.id == 1)
                .values(user_id=self.authority.user_id)
                .execution_options(synchronize_session=False))
            return locked
        for mutation in (owner_changed, link_changed):
            for operation in (lambda: self.authorize(row), lambda: self.deny(row)):
                with patch.object(service, '_lock', side_effect=mutation):
                    self.reject(operation,
                        'La decisión requiere una persona distinta del responsable de la orden.')
        lookup = people.of_user
        def moved_during_lookup(session, user_id):
            found = lookup(session, user_id)
            session.execute(Person.__table__.update().where(Person.id == found)
                .values(user_id=None).execution_options(synchronize_session=False))
            return found
        with patch.object(people, 'of_user', side_effect=moved_during_lookup):
            self.reject(lambda: self.authorize(row),
                        'Tu usuario debe tener una persona vinculada para aprobar.')

    def test_s4_customer_pair_and_future_are_atomic(self):
        row = self.checked()
        paired = 'Indica la fecha y el medio de aprobación del cliente, o deja ambos vacíos.'
        future = 'La fecha de aprobación del cliente no puede ser futura.'
        cases = [({'customer_approval_date': support.TODAY}, paired),
                 ({'customer_approval_means': 'Phone'}, paired),
                 ({'customer_approval_date': support.TODAY + timedelta(days=1),
                   'customer_approval_means': 'Phone'}, future)]
        for values, message in cases:
            self.reject(lambda: self.authorize(row, **(self.concession() | values)), message)

    def test_s1_s2_s4_s5_success_and_valid_payload_immutability(self):
        row = self.checked()
        child = service.verifications.list_(db.session, self.who, release_id=row.id)[0]
        order_before = audit.snapshot(row.order)
        self.authorize(row)
        self.assertEqual(order_before, audit.snapshot(row.order))
        self.assertEqual((False, None, None),
                         (row.concession, row.concession_reason, row.concession_approver_id))
        for operation in (
            lambda: service.releases.update(db.session, self.who, row.id, {'notes': 'Altered'}),
            lambda: service.verifications.update(db.session, self.who, child.id,
                                                 {'result': 'nonconforming'}, today=support.TODAY),
            lambda: service.verifications.delete(db.session, self.who, child.id)):
            self.reject(operation, service.READ_ONLY)
        rejected = self.release(self.finished(owner_id=1))
        order_before = audit.snapshot(rejected.order)
        self.deny(rejected)
        self.assertEqual(order_before, audit.snapshot(rejected.order))
        self.assertNotEqual(rejected.id, self.release(rejected.order).id)


class IndependentDecisionWeb(support.DecisionCase):
    def test_s4_explicit_concession_is_not_discarded(self):
        row = self.checked()
        self.login(support.RoleEnum.ADMINISTRADOR)
        g.pop('_login_user', None)
        form = dict(concession_reason='', concession_approver_id='',
                    customer_approval_date='', customer_approval_means='')
        url = f'/liberacion/{row.id}/autorizar'
        for key, value in (('concession_reason', 'Deviation'),
                           ('concession_approver_id', str(self.decider.id)),
                           ('customer_approval_date', support.TODAY.isoformat()),
                           ('customer_approval_means', 'Phone')):
            before = hashes()
            response = self.client.post(url, data=form | {key: value})
            self.assertEqual(200, response.status_code)
            self.assertEqual(before, hashes())
            self.assertIn('Marca «Con concesión» para registrar sus datos.',
                          response.get_data(as_text=True))
        form.update(concession='y', concession_reason='Retained deviation',
                    concession_approver_id=str(self.decider.id))
        self.assertEqual(302, self.client.post(url, data=form).status_code)
        fresh = service.releases.get(db.session, self.who, row.id)
        self.assertTrue(fresh.concession)
        self.assertEqual('Retained deviation', fresh.concession_reason)
        marker = f'#{row.id} · {row.order.code}'
        for path in (f'/liberacion/{row.id}', f'/produccion/{row.order_id}'):
            text = self.client.get(path).get_data(as_text=True)
            self.assertIn('Retained deviation', text)
            self.assertIn('Release authority', text)
        self.assertIn(marker, self.client.get('/liberacion/?concession=true').get_data(as_text=True))
        self.assertNotIn(marker, self.client.get('/liberacion/?concession=false').get_data(as_text=True))
