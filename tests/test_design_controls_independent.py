"""Independent DD3 probes: S2 controls and S5 retained evidence; no DD4 gates."""
from datetime import timedelta
from io import BytesIO

from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

from register_routes import RegisterRoutesBase
from test_design import DesignCase, TODAY, actor
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import DesignStage, RoleEnum
from app.services import design, design_controls as controls, people

PLAN = dict(kind='review', description='Prototype review', responsible_id=1)
RECORD = dict(kind='review', date=TODAY, responsible_id=1,
              result='conforming', evidence='Minutes')
BASE = '/diseno/1/controles'


class IndependentServices(DesignCase):
    def setUp(self):
        super().setUp()
        self.project = self.create()
        self.stage_row = self.stage(self.project)
        design.transition(db.session, self.who, self.project.id, 'planificado')
        db.session.commit()

    def plan(self, **changes):
        row = controls.plans.create(db.session, self.who,
            PLAN | dict(project_id=self.project.id) | changes)
        db.session.commit()
        return row

    def record(self, **changes):
        row = controls.records.create(db.session, self.who,
            RECORD | dict(project_id=self.project.id) | changes, today=TODAY)
        db.session.commit()
        return row

    def test_s2_resolution_is_existential_and_project_scoped(self):
        other = self.create()
        foreign = self.plan(project_id=other.id)
        plans = [self.plan(kind=k) for k in ('review', 'verification', 'validation')]
        self.record()  # Unlinked evidence must not resolve a plan.
        for p in plans:
            self.record(kind=p.kind.name, planned_control_id=p.id,
                        result='nonconforming', actions='Repeat test')
            self.assertFalse(controls.is_resolved(db.session, p))
            self.record(kind=p.kind.name, planned_control_id=p.id,
                        result='conforming_with_observations', actions='Monitor')
            self.record(kind=p.kind.name, planned_control_id=p.id,
                        result='nonconforming', actions='New failure')
            self.assertTrue(controls.is_resolved(db.session, p))
            self.reject(lambda: controls.plans.update(db.session, self.who, p.id,
                {'description': 'Rewrite'}), controls.PLAN_RESOLVED)
        self.assertEqual([], controls.unresolved_planned_controls(db.session, self.project.id))
        self.assertEqual([foreign.id], [p.id for p in
            controls.unresolved_planned_controls(db.session, other.id)])
        self.reject(lambda: controls.plans.update(db.session, self.who, foreign.id,
            {'withdrawn': True, 'withdrawal_reason': '\t\n'}), controls.WITHDRAWAL_REQUIRED)
        controls.plans.update(db.session, self.who, foreign.id,
                             {'withdrawn': True, 'withdrawal_reason': 'Not needed'})
        db.session.commit()
        self.assertEqual([], controls.unresolved_planned_controls(db.session, other.id))
        self.reject(lambda: controls.plans.update(db.session, self.who, foreign.id,
            {'withdrawn': False, 'withdrawal_reason': None}), controls.PLAN_RESOLVED)

    def test_s2_rejections_and_s5_immutable_history(self):
        p = self.plan()
        cases = [({'date': TODAY + timedelta(days=1)}, 'La fecha del control no puede ser futura.'),
                 ({'evidence': '\t\n'}, 'El campo «evidence» es obligatorio.'),
                 ({'stage_id': 0}, controls.STAGE_REQUIRED),
                 ({'planned_control_id': 0}, controls.PLAN_REQUIRED),
                 ({'kind': 'validation', 'planned_control_id': p.id}, controls.PLAN_REQUIRED)]
        for result in ('conforming_with_observations', 'nonconforming'):
            cases.append(({'result': result, 'actions': '  '}, controls.ACTIONS_REQUIRED))
        for values, message in cases:
            with self.subTest(values=values):
                self.reject(lambda: self.record(**values), message)
        row = self.record(planned_control_id=p.id, stage_id=self.stage_row.id)
        self.record(planned_control_id=p.id)  # Identical date/kind/plan is repeatable.
        self.reject(lambda: controls.records.update(db.session, self.who, row.id,
            {'evidence': 'Replacement'}, today=TODAY), controls.IMMUTABLE)
        self.reject(lambda: controls.records.delete(db.session, self.who, row.id), controls.IMMUTABLE)
        design.transition(db.session, self.who, self.project.id, 'cancelado',
                          cancellation_reason='Stopped')
        db.session.commit()
        self.reject(lambda: self.record(), design.READ_ONLY)
        self.reject(lambda: self.plan(), design.READ_ONLY)

    def test_s2_stale_citations_refresh_after_parent_lock(self):
        p = self.plan()
        stage_id = self.stage_row.id
        with Session(db.engine) as second:
            controls.plans.update(second, self.who, p.id, {'kind': 'verification'})
            design.stages.delete(second, self.who, stage_id)
            second.commit()
        self.assertEqual('review', p.kind.name)
        self.assertIsNotNone(db.session.get(DesignStage, stage_id))
        self.reject(lambda: self.record(planned_control_id=p.id), controls.PLAN_REQUIRED)
        self.reject(lambda: self.record(stage_id=stage_id), controls.STAGE_REQUIRED)
        self.record(kind='verification', planned_control_id=p.id)


class IndependentWeb(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)
        for title in ('Design', 'Other'):
            self.client.post('/diseno/nuevo', data=dict(title=title, description='Product',
                                                       owner_id=1, starter_stages='y'))
        self.plan = dict(kind='review', description='Review <A> & B', responsible_id='1',
                         stage_id='1', planned_date='')
        self.assertEqual(302, self.client.post(BASE + '/planes/nuevo', data=self.plan).status_code)
        self.client.post('/diseno/1/transicion', data={'target': 'planificado'})
        self.client.post('/diseno/2/controles/planes/nuevo', data=self.plan | {'stage_id': '8'})
        self.record = dict(kind='review', date='2020-01-01', responsible_id='1',
            result='conforming', evidence='Reference', actions='', stage_id='', planned_control_id='')

    def state(self):
        with self.app.app_context():
            return hashes()

    def test_s2_noop_and_explicit_foreign_duplicate_unknown_fields(self):
        page = self.client.get(BASE + '/planes/1/editar')
        parser = BrowserForm()
        parser.feed(page.text)
        before = self.state()
        self.assertEqual(302, self.client.post(BASE + '/planes/1/editar', data=parser.values).status_code)
        self.assertEqual(before, self.state())
        for extra in ({'project_id': '2'}, {'stage_id': '8'}, {'planned_control_id': '2'}):
            before = self.state()
            response = self.client.post(BASE + '/registro/nuevo', data=self.record | extra)
            self.assertEqual(400 if 'project_id' in extra else 200, response.status_code)
            self.assertEqual(before, self.state())
        data = MultiDict(self.record)
        data.add('kind', 'validation')
        response = self.client.post(BASE + '/registro/nuevo', data=data)
        self.assertEqual(400, response.status_code)
        self.assertIn('No repitas los campos del control.', response.text)
        self.assertEqual(before, self.state())

    def test_s2_s5_multipart_fields_are_not_silently_discarded(self):
        # These forms accept text/reference, not uploads: refuse instead of losing a file.
        paths = ((BASE + '/planes/nuevo', self.plan),
                 (BASE + '/planes/1/editar', self.plan),
                 (BASE + '/registro/nuevo', self.record),
                 (BASE + '/planes/1/retirar', {'withdrawal_reason': 'Not needed'}))
        for path, data in paths:
            with self.subTest(path=path):
                self.client.get('/diseno/1')  # Consume earlier success flashes.
                before = self.state()
                response = self.client.post(path, data=data | {
                    'attachment': (BytesIO(b'Actual evidence'), 'evidence.txt')},
                    content_type='multipart/form-data')
                self.assertEqual(400, response.status_code)
                self.assertIn('Campos no permitidos: attachment.', response.text)
                self.assertEqual(before, self.state())
                self.assertFalse(any(level == 'success' for level, _ in self.flashes()))


class IndependentMcp(McpDbCase):
    async def test_s2_documented_example_and_s5_resolution_is_shared(self):
        self.seed('design_projects', starter_stages=True)
        design.transition(db.session, actor(), 1, 'planificado')
        db.session.commit()
        # Exact DD3 qms_create example from docs/mcp.md.
        example = {'module': 'design_control_records', 'data': {'project_id': 1,
            'kind': 'verification', 'date': '2020-01-01', 'responsible_id': 1,
            'result': 'conforming', 'evidence': 'Informe de ensayo E-1'}}
        response = await self.call(mcp_actor(), 'qms_create', example)
        self.assertFalse(response.is_error, response.content)
        expected = example['data'] | {'kind': 'verificación', 'result': 'conforme'}
        for key, value in expected.items():
            self.assertEqual(value, response.structured_content[key])
        p = self.seed('design_planned_controls')
        data = dict(project_id=1, kind='review', date='2020-01-01', responsible_id=1,
                    result='nonconforming', evidence='Failure', actions='Retry', planned_control_id=p)
        for result, resolved in (('nonconforming', False), ('conforming', True),
                                 ('nonconforming', True)):
            data['result'] = result
            created = await self.call(mcp_actor(), 'qms_create',
                                      {'module': 'design_control_records', 'data': data})
            self.assertFalse(created.is_error, created.content)
            read = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_get',
                                  {'module': 'design_planned_controls', 'id': p})
            self.assertIs(resolved, read.structured_content['resolved'])
            self.assertEqual([] if resolved else [p], [r.id for r in
                controls.unresolved_planned_controls(db.session, 1)])
        for module, record_id, update in (
                ('design_planned_controls', p, {'description': 'Rewrite'}),
                ('design_control_records', created.structured_content['id'], {})):
            before = hashes()
            refused = await self.call(mcp_actor(), 'qms_update',
                {'module': module, 'id': record_id, 'data': update})
            self.assertTrue(refused.is_error)
            message = controls.PLAN_RESOLVED if module.endswith('controls') else controls.IMMUTABLE
            self.assertEqual('Error executing tool qms_update: ' + message, refused.content[0].text)
            self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})
