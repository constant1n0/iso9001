"""Independent PS1 probes: S1/S2 conditions and S3 lifecycle boundaries."""
import ast
import inspect
from datetime import date
from io import BytesIO
from unittest.mock import patch

from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import hashes
from test_design import actor
from test_design_io import approved_revision
from app.extensions import db
from app.models import RoleEnum
from app.services import people, process_operations, production
from app.services.errors import ValidationError

DATA = dict(code='IND-1', kind='service', title='Independent', process_id=1)


class IndependentProduction(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.seed_with(process_operations, {'proceso': 'Service'})
        self.login(RoleEnum.ADMINISTRADOR)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.who = actor()

    def create(self, **extra):
        row = production.orders.create(db.session, self.who, DATA | extra)
        db.session.commit()
        return row

    def refused(self, operation, message):
        before = hashes()
        with self.assertRaises(ValidationError) as caught:
            operation()
        self.assertEqual(message, str(caught.exception))
        db.session.rollback()
        self.assertEqual(before, hashes())

    def test_s1_every_production_route_has_permission_decorator(self):
        from app.routes import production_routes
        tree = ast.parse(inspect.getsource(production_routes))
        missing = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(d.startswith('bp.route(') for d in decorators):
                if not any(d.startswith('require_permission(') for d in decorators):
                    missing.append(node.name)
        self.assertEqual([], missing)

    def test_s3_start_must_not_silently_drop_explicit_cancel_reason(self):
        row = self.create()
        before = hashes()
        response = self.client.post(f'/produccion/{row.id}/transicion', data={
            'target': 'in_progress', 'cancellation_reason': 'Customer withdrew'})
        db.session.expire_all()
        state = production.orders.get(db.session, self.who, row.id).state.name
        self.assertIn(response.status_code, (302, 400))
        self.assertEqual(('planned', before), (state, hashes()))

    def test_s2_exact_history_and_unapproved_new_version(self):
        from app.services import design, design_io, design_changes
        project = design.projects.create(db.session, self.who, dict(
            title='Design', description='Spec', owner_id=1))
        output = design_io.outputs.create(db.session, self.who, dict(
            project_id=project.id, kind='other', text='Exact v1', acceptance_criteria='Fits'))
        people.create(db.session, self.who, dict(
            nombre='Approver', user_id=self.ids[RoleEnum.ADMINISTRADOR]))
        approver = type(self.who)(self.ids[RoleEnum.ADMINISTRADOR], 'Admin',
                                  RoleEnum.ADMINISTRADOR, 'web')
        design_changes.approve_output(db.session, approver,
                                     output.id, today=date(2020, 1, 1))
        change = design_changes.changes.create(db.session, self.who, dict(
            project_id=project.id, reason='Revise', impact='Fit', review='Checked',
            actions='New version', output_ids=[output.id]))
        design_changes.authorize(db.session, approver,
                                 change.id, today=date(2020, 1, 1))
        draft = design_changes.apply(db.session, self.who, change.id)[0]
        db.session.commit()
        row = self.create(design_output_id=output.id)
        before = hashes()
        production.orders.update(db.session, self.who, row.id,
                                 {'design_output_id': output.id})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.refused(lambda: production.orders.update(db.session, self.who, row.id,
            {'design_output_id': draft.id}),
            'Elige una versión aprobada de una salida del diseño.')
        self.assertIn('Exact v1', self.client.get(f'/produccion/{row.id}').text)

    def test_s2_retained_equipment_expiry_and_noop(self):
        from app.services import equipment
        item = equipment.equipment.create(db.session, self.who,
                                          {'codigo': 'IND-E', 'nombre': 'Gauge'})
        equipment.checks.create(db.session, self.who, dict(equipo_id=item.id,
            fecha=date(2020, 1, 1), tipo='calibracion', resultado='apto',
            realizado_por='Lab', referencia='Cert', fecha_proxima=date(2020, 2, 1)),
            today=date(2020, 1, 1))
        db.session.commit()
        with patch('app.services.production.local_today', return_value=date(2020, 2, 1)):
            row = self.create(equipment_ids=[item.id], person_ids=[1], owner_id=1)
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        before = hashes()
        production.orders.update(db.session, self.who, row.id,
            {'equipment_ids': [item.id, item.id], 'person_ids': [1], 'owner_id': 1})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.refused(lambda: self.create(code='IND-2', equipment_ids=[item.id]),
                     'Elige un equipo apto.')
        self.assertIn('vencido', self.client.get(f'/produccion/{row.id}').text)
        response = self.client.post(f'/produccion/{row.id}/editar', data={
            'title': (BytesIO(b'not allowed'), 'title.txt')})
        self.assertEqual(400, response.status_code)
        self.assertEqual(before, hashes())


class IndependentProductionMcp(McpDbCase):
    async def test_s2_s3_restricted_citation_terminal_noop_refused(self):
        self.seed('procesos')
        self.seed('personas')
        _, revision = approved_revision(actor(), code='IND-SECRET')
        rid = self.seed('production_orders', revision_ids=[revision])
        result = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_get',
                                {'module': 'production_orders', 'id': rid})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual([revision], result.structured_content['revision_ids'])
        self.assertNotIn('IND-SECRET', str(result))
        production.transition(db.session, actor(), rid, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        before = hashes()
        result = await self.call(mcp_actor(), 'qms_update',
            {'module': 'production_orders', 'id': rid, 'data': {}})
        self.assertTrue(result.is_error)
        self.assertEqual('Error executing tool qms_update: ' + production.READ_ONLY,
                         result.content[0].text)
        self.assertEqual(before, hashes())
