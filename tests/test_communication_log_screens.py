"""CM2 browser defaults, evidence history, filters and role boundary."""
from datetime import date

from register_routes import RegisterRoutesBase
from test_communication_screens import BrowserForm, FORM
from test_communication_independent import hashes
from app.extensions import db
from app.models import CommunicationPlan, RoleEnum
from app.services import communication, people
from app.services.actor import Actor

LOG_FORM = dict(date='2020-01-02', communicator_id='1', channel='tablon_intranet',
                channel_detail='Actual board', audience_text='Actual staff',
                summary='Results explained', evidence='Minutes W-1', next_date='2020-04-17')


class CommunicationLogScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM).status_code)

    def state(self):
        with self.app.app_context():
            return hashes()

    def test_form_defaults_and_rendered_enum_roundtrip(self):
        for channel in ('correo', 'reunion', 'telefono', 'tablon_intranet', 'web', 'otro'):
            self.assertEqual(302, self.client.post('/comunicaciones/1/editar',
                                                  data=FORM | {'channel': channel}).status_code)
            response = self.client.get('/comunicaciones/1/registrar')
            self.assertEqual(200, response.status_code)
            parser = BrowserForm()
            parser.feed(response.get_data(as_text=True))
            for key, expected in (('channel', channel), ('communicator_id', '1'),
                                  ('audience_text', 'All staff'), ('channel_detail', 'Workshop board')):
                self.assertEqual([expected], parser.values.getlist(key))
            self.assertEqual([''], parser.values.getlist('next_date'))
            for key, value in {'date': '2020-01-02', 'evidence': 'Minutes ' + channel,
                               'next_date': '2020-03-11'}.items():
                parser.values[key] = value
            response = self.client.post('/comunicaciones/1/registrar', data=parser.values)
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.location.endswith('/comunicaciones/1'))
            self.assertEqual(date(2020, 3, 11), self.fetch(CommunicationPlan, 1).next_date)

    def test_record_show_history_newest_first_and_retirement_readable(self):
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar', data=LOG_FORM).status_code)
        self.assertIn(('success', 'Comunicación registrada.'), self.flashes())
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar',
                                              data=LOG_FORM | {'evidence': 'Minutes W-2'}).status_code)
        html = self.client.get('/comunicaciones/1').get_data(as_text=True)
        for text in ('Registrar comunicación', 'Historial de comunicaciones', 'Actual board',
                     'Actual staff', 'Results explained', 'Minutes W-1', '17/04/2020', 'Owner'):
            self.assertIn(text, html)
        self.assertLess(html.index('Minutes W-2'), html.index('Minutes W-1'))
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=FORM | {'retired': 'y'}).status_code)
        html = self.client.get('/comunicaciones/1').get_data(as_text=True)
        self.assertIn('Minutes W-1', html)
        self.assertNotIn('href="/comunicaciones/1/registrar"', html)
        before = self.state()
        response = self.client.post('/comunicaciones/1/registrar', data=LOG_FORM)
        self.assertEqual(200, response.status_code)
        self.assertIn('No se pueden registrar comunicaciones de una entrada retirada.', response.get_data(as_text=True))
        self.assertEqual(before, self.state())
        self.assertEqual(302, self.client.post('/comunicaciones/1/eliminar').status_code)
        self.assertEqual(before, self.state())

    def test_invalid_form_and_service_validation_preserve_both_records_and_audit(self):
        before = self.state()
        for changes, message in (({'evidence': ''}, 'Este campo es obligatorio.'),
                                 ({'communicator_id': ''}, 'Este campo es obligatorio.'),
                                 ({'next_date': ''}, 'Este campo es obligatorio.'),
                                 ({'date': '2999-01-01', 'next_date': '2999-02-01'},
                                  'La fecha de comunicación no puede ser futura.'),
                                 ({'next_date': '2020-01-02'},
                                  'Indica una nueva próxima fecha posterior a la comunicación.')):
            response = self.client.post('/comunicaciones/1/registrar', data=LOG_FORM | changes)
            self.assertEqual(200, response.status_code)
            self.assertIn(message, response.get_data(as_text=True))
            self.assertEqual(before, self.state())

    def test_global_filters_and_paging_preserve_selections(self):
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar', data=LOG_FORM).status_code)
        self.seed_with(people, {'nombre': 'Other communicator'})
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM | {'title': 'Other plan'}).status_code)
        self.assertEqual(302, self.client.post('/comunicaciones/2/registrar',
            data=LOG_FORM | {'communicator_id': '2', 'date': '2020-01-03', 'evidence': 'Other evidence'}).status_code)
        for query, first, second in (('', True, True), ('?plan_id=1', True, False),
                                     ('?communicator_id=2', False, True),
                                     ('?date_from=2020-01-03', False, True),
                                     ('?date_to=2020-01-02', True, False),
                                     ('?plan_id=1&communicator_id=2', False, False)):
            response = self.client.get('/comunicaciones/registro' + query)
            self.assertEqual(200, response.status_code)
            html = response.get_data(as_text=True)
            self.assertEqual(first, 'Minutes W-1' in html)
            self.assertEqual(second, 'Other evidence' in html)
        html = self.client.get('/comunicaciones/registro?plan_id=1&communicator_id=1&date_from=2020-01-02&date_to=2020-01-02').get_data(as_text=True)
        parser = BrowserForm()
        parser.feed(html)
        for key, value in (('plan_id', '1'), ('communicator_id', '1'),
                           ('date_from', '2020-01-02'), ('date_to', '2020-01-02')):
            self.assertEqual([value], parser.values.getlist(key))
        self.assertIn('href="/comunicaciones/registro"', self.client.get('/comunicaciones/').get_data(as_text=True))

    def test_roles_csrf_no_web_edit_or_delete(self):
        before = self.state()
        self.login(RoleEnum.OPERATIVO)
        response = self.client.post('/comunicaciones/1/registrar', data=LOG_FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith('/dashboard/'))
        self.assertEqual(before, self.state())
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/comunicaciones/registro').status_code)
            html = self.client.get('/comunicaciones/1').get_data(as_text=True)
            self.assertEqual(role is not RoleEnum.OPERATIVO, 'href="/comunicaciones/1/registrar"' in html)
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.client.post('/comunicaciones/1/registrar', data=LOG_FORM).status_code)
        before = self.state()
        for path in ('/comunicaciones/registro/1/editar', '/comunicaciones/registro/1/eliminar'):
            self.assertEqual(404, self.client.post(path, data=LOG_FORM).status_code)
            self.assertEqual(before, self.state())
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(400, self.client.post('/comunicaciones/1/registrar', data=LOG_FORM).status_code)
        self.assertEqual(before, self.state())
