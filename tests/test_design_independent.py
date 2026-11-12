"""Independent DD1 probes: S1, S2 stages and S5 non-completion lifecycle."""
from datetime import date, timedelta
from itertools import permutations
import unittest

from register_routes import RegisterRoutesBase
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people, qms_scope
from app.services.actor import Actor

FORM = dict(title='Packaging', description='Container <A> & lid', owner_id='1')
DAY = date(2026, 1, 15)


class IndependentWeb(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner', 'user_id': self.ids[RoleEnum.ADMINISTRADOR]})
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        with self.app.app_context():
            return hashes()

    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def project(self, **data):
        response = self.client.post('/diseno/nuevo', data=FORM | data)
        self.assertEqual(302, response.status_code)
        return int(response.location.rsplit('/', 1)[1])

    def test_s1_approved_scope_is_displayed_but_never_changed(self):
        who = Actor(self.ids[RoleEnum.ADMINISTRADOR], 'Owner', RoleEnum.ADMINISTRADOR, 'web')
        with self.app.app_context():
            scope = qms_scope.new_draft(db.session, who, {
                'declaracion': 'Manufacturing', 'aplicabilidad': {
                    '8.3': {'aplica': False, 'justificacion': 'Customer supplies drawings.'}}})
            qms_scope.approve(db.session, who, scope.id, today=DAY)
            db.session.commit()
            def scope_rows():
                return repr([db.session.execute(db.text('SELECT * FROM ' + table)).all()
                             for table in ('alcances_sgc', 'aplicabilidad_alcance')])
            before = scope_rows()
        pid = self.project(starter_stages='y')
        for role in RoleEnum:
            self.login(role)
            for path in ('/diseno/', f'/diseno/{pid}'):
                digest = self.state()
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertIn('No aplica', response.text)
                self.assertIn('Customer supplies drawings.', response.text)
                self.assertNotIn('name="aplica-8.3"', response.text)
                self.assertEqual(digest, self.state())
        self.login(RoleEnum.ADMINISTRADOR)
        self.client.post(f'/diseno/{pid}/transicion', data={
            'target': 'cancelado', 'cancellation_reason': 'Stopped'})
        with self.app.app_context():
            self.assertEqual(before, scope_rows())

    def test_s2_s5_rendered_noops_and_cancelled_stage_writes(self):
        pid = self.project()
        stage_path = f'/diseno/{pid}/etapas'
        self.assertEqual(302, self.client.post(stage_path + '/nueva', data={
            'name': 'Prototype', 'owner_id': '1'}).status_code)
        for path in (f'/diseno/{pid}/editar', stage_path + '/1/editar'):
            form = self.form(path)
            before = self.state()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, self.state())
        self.assertEqual(302, self.client.post(f'/diseno/{pid}/transicion', data={
            'target': 'cancelado', 'cancellation_reason': 'Stopped'}).status_code)
        for suffix, data in (('/nueva', {'name': 'Added', 'owner_id': 1}),
                             ('/1/editar', {'name': 'Changed', 'owner_id': 1}),
                             ('/1/eliminar', {})):
            before = self.state()
            response = self.client.post(stage_path + suffix, data=data)
            self.assertIn(response.status_code, (200, 302))
            self.assertEqual(before, self.state())
            message = 'Un proyecto cancelado es de solo lectura.'
            self.assertTrue(message in response.text or
                            ('danger', message) in self.flashes())

    def test_s1_s5_create_only_starter_option_must_not_be_silently_ignored(self):
        pid = self.project()
        before = self.state()
        response = self.client.post(f'/diseno/{pid}/editar', data=FORM | {'starter_stages': 'y'})
        self.assertEqual(before, self.state())
        self.assertFalse(response.status_code == 302 and
                         ('success', 'Proyecto de diseño actualizado.') in self.flashes(),
                         'Explicit starter_stages was silently ignored with success')


class IndependentStages(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        from app.services import design
        self.design = design
        self.who = Actor(None, 'Owner', RoleEnum.ADMINISTRADOR, 'web')
        self.project = design.projects.create(db.session, self.who, FORM | {'owner_id': 1})
        db.session.commit()

    def stage(self, **data):
        row = self.design.stages.create(db.session, self.who, {
            'project_id': self.project.id, 'name': 'Stage', 'owner_id': 1} | data, today=DAY)
        db.session.commit()
        return row.id

    def reject(self, call, message):
        from app.services.errors import ValidationError
        before = hashes()
        with self.assertRaises(ValidationError) as caught:
            call()
        self.assertEqual(message, str(caught.exception))
        self.assertEqual(before, hashes())
        db.session.rollback()

    def test_s2_sqlite_all_permutations_insert_and_delete_remain_contiguous(self):
        ids = [self.stage(name=str(i)) for i in range(4)]
        for order in permutations(ids):
            for position, sid in enumerate(order, 1):
                self.design.stages.update(db.session, self.who, sid,
                                          {'position': position}, today=DAY)
                db.session.commit()
            rows = self.design.stages.list_(db.session, self.who, project_id=self.project.id)
            self.assertEqual(list(order), [row.id for row in rows])
            self.assertEqual([1, 2, 3, 4], [row.position for row in rows])
        added = self.stage(name='Inserted', position=2)
        self.design.stages.delete(db.session, self.who, added)
        db.session.commit()
        self.assertEqual([1, 2, 3, 4], [row.position for row in
            self.design.stages.list_(db.session, self.who, project_id=self.project.id)])

    def test_s2_done_anchor_rejections_and_completion_date_boundaries(self):
        first, anchor, last = [self.stage() for _ in range(3)]
        for data, message in (({'done': True},
                'Indica la fecha de finalización solo para una etapa finalizada.'),
                ({'done': True, 'done_date': DAY + timedelta(days=1)},
                 'La fecha de finalización no puede ser futura.')):
            self.reject(lambda: self.design.stages.update(
                db.session, self.who, anchor, data, today=DAY), message)
        self.design.stages.update(db.session, self.who, anchor,
                                  {'done': True, 'done_date': DAY}, today=DAY)
        db.session.commit()
        operations = [lambda: self.stage(position=1),
                      lambda: self.design.stages.delete(db.session, self.who, first),
                      lambda: self.design.stages.update(db.session, self.who, last,
                                                        {'position': 1}, today=DAY)]
        for operation in operations:
            self.reject(operation, 'Una etapa finalizada es de solo lectura.')
        self.design.stages.update(db.session, self.who, first, {'name': 'Editable'}, today=DAY)
        db.session.commit()
        self.assertIsInstance(self.stage(), int)


class IndependentMcp(McpDbCase):
    async def test_s1_s2_s5_documented_example_and_cancelled_readonly(self):
        from app.services import design
        self.seed('personas')
        args = {'module': 'design_projects', 'data': {'title': 'Nuevo envase',
            'description': 'Envase reciclable', 'owner_id': 1, 'starter_stages': True}}
        result = await self.call(mcp_actor(), 'qms_create', args)
        self.assertFalse(result.is_error, result.content)
        pid = result.structured_content['id']
        self.assertEqual('borrador', result.structured_content['state'])
        stages = await self.call(mcp_actor(), 'qms_list', {
            'module': 'design_stages', 'filters': {'project_id': pid}})
        self.assertEqual(7, stages.structured_content['total'])
        for target in ('planificado', 'en_curso', 'cancelado', 'completado'):
            before = hashes()
            result = await self.call(mcp_actor(), 'qms_update', {
                'module': 'design_projects', 'id': pid, 'data': {'state': target}})
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_update: Campos no permitidos: state.',
                             result.content[0].text)
            self.assertEqual(before, hashes())
        who = Actor(None, 'Owner', RoleEnum.ADMINISTRADOR, 'web')
        design.transition(db.session, who, pid, 'cancelado', cancellation_reason='Stopped')
        db.session.commit()
        for tool, args in (('qms_update', {'module': 'design_projects', 'id': pid,
                                          'data': {'title': 'Changed'}}),
                           ('qms_create', {'module': 'design_stages', 'data': {
                               'project_id': pid, 'name': 'New', 'owner_id': 1}}),
                           ('qms_update', {'module': 'design_stages', 'id': 1,
                                           'data': {'name': 'Changed'}})):
            before = hashes()
            result = await self.call(mcp_actor(), tool, args)
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool ' + tool +
                             ': Un proyecto cancelado es de solo lectura.', result.content[0].text)
            self.assertEqual(before, hashes())


if __name__ == '__main__':
    unittest.main()
