"""Independent CM2 regressions: S2-S5 and CM1 rendered no-op compatibility."""
from unittest.mock import patch

from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import BrowserForm, FORM, hashes
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import communication, people
from app.services.errors import Conflict

ENTRY = dict(date='2020-01-02', communicator_id='1', channel='telefono',
             evidence='Call reference C-2', next_date='2020-07-19')
EXAMPLE = dict(plan_id=1, date='2020-01-02', communicator_id=1,
               channel='reunion', audience_text='Personal del taller',
               evidence='Acta M-1', next_date='2020-07-19')


class IndependentLogWeb(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Plan owner'})
        self.seed_with(people, {'nombre': 'Actual communicator'})
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM).status_code)

    def state(self):
        with self.app.app_context():
            return hashes()

    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_cm1_noop_and_existing_validation(self):
        # S2-S5: identical public behavior at baseline and candidate.
        values = self.form('/comunicaciones/1/editar')
        self.assertEqual(['reunion'], values.getlist('channel'))
        before = self.state()
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=values).status_code)
        self.assertIn(('success', 'Comunicación planificada actualizada.'), self.flashes())
        self.assertEqual(before, self.state())
        values['title'] = ''
        response = self.client.post('/comunicaciones/1/editar', data=values)
        self.assertEqual(200, response.status_code)
        self.assertIn('Este campo es obligatorio.', response.get_data(as_text=True))
        self.assertEqual(before, self.state())

    def test_s2_s3_defaults_actual_values_and_immutable_history(self):
        from app.models import CommunicationLog
        for channel in ('correo', 'reunion', 'telefono', 'tablon_intranet', 'web', 'otro'):
            self.assertEqual(302, self.client.post('/comunicaciones/1/editar',
                data=FORM | {'channel': channel}).status_code)
            values = self.form('/comunicaciones/1/registrar')
            for key, value in (('communicator_id', '1'), ('channel', channel),
                               ('audience_text', FORM['audience_text']), ('next_date', '')):
                self.assertEqual([value], values.getlist(key))
        self.assertEqual(0, self.count(CommunicationLog))
        for key, value in (ENTRY | {'communicator_id': '2', 'audience_text': 'Actual audience',
                                    'summary': 'Actual message', 'channel_detail': 'Extension 4'}).items():
            values[key] = value
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar', data=values).status_code)
        row = self.fetch(CommunicationLog, 1)
        self.assertEqual((2, 'telefono', 'Actual audience', 'Actual message', 'Extension 4'),
            (row.communicator_id, row.channel.name, row.audience_text, row.summary, row.channel_detail))
        before = self.state()
        values = self.form('/comunicaciones/1/editar')
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=values).status_code)
        self.assertEqual(before, self.state())
        for key, value in {'channel': 'web', 'audience_text': 'New planned audience', 'retired': 'y'}.items():
            values[key] = value
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=values).status_code)
        for role in RoleEnum:
            self.login(role)
            before = self.state()
            for path in ('/comunicaciones/1', '/comunicaciones/registro?communicator_id=2'):
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                for text in ('Actual communicator', 'Actual audience', 'Call reference C-2', 'Extension 4'):
                    self.assertIn(text, html)
            self.assertEqual(before, self.state())
        self.login(RoleEnum.ADMINISTRADOR)
        before = self.state()
        for path in ('/comunicaciones/registro/1/editar', '/comunicaciones/registro/1/eliminar'):
            self.assertEqual(404, self.client.post(path, data=ENTRY).status_code)
            self.assertEqual(before, self.state())
        for path, message in (('/comunicaciones/1/eliminar', communication.STILL_REFERENCED),
                              ('/personas/2/eliminar', people.STILL_REFERENCED)):
            self.assertEqual(302, self.client.post(path).status_code)
            self.assertIn(('danger', message), self.flashes())
            self.assertEqual(before, self.state())

    def test_s2_s4_s5_rejected_web_writes_preserve_hashes(self):
        before = self.state()
        for change, message in (({'date': ''}, 'Este campo es obligatorio.'),
            ({'date': '2020-02-30'}, 'Este campo es obligatorio.'),
            ({'date': '2999-01-01', 'next_date': '2999-01-02'}, 'La fecha de comunicación no puede ser futura.'),
            ({'evidence': '   '}, 'Este campo es obligatorio.'),
            ({'next_date': '2020-01-02'}, 'Indica una nueva próxima fecha posterior a la comunicación.')):
            response = self.client.post('/comunicaciones/1/registrar', data=ENTRY | change)
            self.assertEqual(200, response.status_code)
            self.assertIn(message, response.get_data(as_text=True))
            self.assertEqual(before, self.state())
        self.login(RoleEnum.OPERATIVO)
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar', data=ENTRY).status_code)
        self.assertEqual(before, self.state())


class IndependentLogMcp(McpDbCase):
    async def create_log(self, data, actor=None):
        return await self.call(actor or mcp_actor(), 'qms_create',
                               {'module': 'communication_log', 'data': data})

    async def test_s2_s3_s4_documented_example_optional_fields_and_order(self):
        self.seed('communication')
        result = await self.create_log(EXAMPLE, mcp_actor(RoleEnum.AUDITOR))
        self.assertFalse(result.is_error, result.content)
        row = result.structured_content
        for key in ('plan_id', 'date', 'communicator_id', 'audience_text', 'evidence', 'next_date'):
            self.assertEqual(EXAMPLE[key], row[key])
        self.assertEqual('reunión', row['channel'])
        self.assertIsNone(row['summary'])
        self.assertIsNone(row['channel_detail'])
        result = await self.create_log(EXAMPLE | {'evidence': 'Same-day correction',
            'channel': 'otro', 'audience_text': None, 'next_date': '2021-03-17'})
        self.assertFalse(result.is_error, result.content)
        second = result.structured_content['id']
        plan = await self.call(mcp_actor(), 'qms_get', {'module': 'communication', 'id': 1})
        self.assertEqual('2021-03-17', plan.structured_content['next_date'])
        self.assertEqual('2020-01-02', plan.structured_content['latest_date'])
        before = hashes()
        for role in RoleEnum:
            result = await self.call(mcp_actor(role), 'qms_list', {'module': 'communication_log',
                'filters': {'plan_id': 1, 'date_from': '2020-01-02', 'date_to': '2020-01-02'}})
            self.assertEqual(2, result.structured_content['total'])
            self.assertEqual([second, row['id']], [r['id'] for r in result.structured_content['items']])
        self.assertEqual(before, hashes())

    async def test_s4_fault_after_either_audit_rolls_back_both(self):
        from app.services import audit
        self.seed('communication')
        record = audit.record
        for entity in ('communication_plan', 'communication_log'):
            before = hashes()
            visited = []
            def fail_after_flush(session, actor, action, item, *args, **kwargs):
                result = record(session, actor, action, item, *args, **kwargs)
                visited.append(item.__tablename__)
                if item.__tablename__ == entity:
                    raise Conflict('Injected transaction failure')
                return result
            with patch.object(audit, 'record', side_effect=fail_after_flush):
                result = await self.create_log(EXAMPLE)
            self.assertIn(entity, visited)
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_create: Injected transaction failure', result.content[0].text)
            self.assertEqual(before, hashes())
        result = await self.create_log(EXAMPLE)
        self.assertFalse(result.is_error, result.content)
        audits = db.session.query(AuditLog).order_by(AuditLog.id).all()[-2:]
        self.assertEqual([('communication_plan', 'update'), ('communication_log', 'create')],
                         [(r.entity_type, r.action) for r in audits])

    async def test_s2_s4_s5_mcp_rejections_preserve_hashes(self):
        self.seed('communication')
        result = await self.create_log(EXAMPLE)
        self.assertFalse(result.is_error, result.content)
        for changes, message in (({'date': '2020-01-01'}, 'La comunicación no puede ser anterior a la última registrada.'),
            ({'date': 'bad'}, 'El campo «date» debe ser una fecha AAAA-MM-DD.'),
            ({'communicator_id': 0}, 'El campo «communicator_id» no corresponde a ninguna persona.'),
            ({'extra': True}, 'Campos no permitidos: extra.'),
            ({'next_date': None}, 'El campo «next_date» es obligatorio y debe ser una fecha.')):
            before = hashes()
            result = await self.create_log(EXAMPLE | changes)
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_create: ' + message, result.content[0].text)
            self.assertEqual(before, hashes())
        for actor in (mcp_actor(RoleEnum.OPERATIVO), mcp_actor(scopes=('read',))):
            before = hashes()
            result = await self.create_log(EXAMPLE, actor)
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_create: No tienes permiso para realizar esta acción.', result.content[0].text)
            self.assertEqual(before, hashes())
        before = hashes()
        result = await self.call(mcp_actor(), 'qms_update',
            {'module': 'communication_log', 'id': 1, 'data': {'evidence': 'Replacement'}})
        self.assertTrue(result.is_error)
        self.assertEqual('Error executing tool qms_update: Las comunicaciones registradas no se modifican ni se eliminan; registra una nueva.', result.content[0].text)
        self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})
