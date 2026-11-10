"""Communication screens: browser preservation, permissions and target refusals."""
from html.parser import HTMLParser

from werkzeug.datastructures import MultiDict
from sqlalchemy import event

from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from app.extensions import db
from app.models import RoleEnum
from app.services import clientes, people, providers, stakeholders

FORM = dict(title='Quality results', content='Explain performance', scope='ambas',
            channel='tablon_intranet', channel_detail='Workshop board', owner_id='1',
            frequency='Every month', next_date='2000-01-01', audience_text='All staff')


class BrowserForm(HTMLParser):
    """Successful text, select, textarea and checkbox controls from a rendered form."""
    def __init__(self):
        super().__init__()
        self.values = MultiDict()
        self.select = None
        self.options = []
        self.option = None
        self.textarea = None
        self.text = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.select = attrs
            self.options = []
        elif tag == 'option' and self.select:
            self.option = attrs
            self.options.append(attrs)
        elif tag == 'textarea':
            self.textarea = attrs['name']
            self.text = ''
        elif tag == 'input' and attrs.get('name'):
            kind = attrs.get('type', 'text')
            if kind not in ('submit', 'button', 'reset') and (
                    kind not in ('checkbox', 'radio') or 'checked' in attrs):
                self.values.add(attrs['name'], attrs.get('value', ''))

    def handle_data(self, text):
        if self.textarea:
            self.text += text

    def handle_endtag(self, tag):
        if tag == 'select':
            selected = [o for o in self.options if 'selected' in o]
            if not selected and 'multiple' not in self.select and self.options:
                selected = self.options[:1]
            for option in selected:
                self.values.add(self.select['name'], option.get('value', ''))
            self.select = None
        elif tag == 'textarea':
            # HTML ignores the first newline immediately after a textarea start tag.
            value = self.text.removeprefix('\r\n').removeprefix('\n')
            self.values.add(self.textarea, value)
            self.textarea = None


class CommunicationScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Owner'})
        self.login(RoleEnum.ADMINISTRADOR)

    def test_create_show_edit_filters_retire_and_navigation(self):
        response = self.client.post('/comunicaciones/nuevo', data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith('/comunicaciones/1'))
        self.assertIn(('success', 'Comunicación planificada registrada.'), self.flashes())
        html = self.client.get(response.location).get_data(as_text=True)
        for text in ('Quality results', 'Explain performance', 'Ambas', 'tablón o intranet',
                     'Workshop board', 'Owner', 'Every month', 'All staff',
                     'href="/comunicaciones/"', 'Vencida'):
            self.assertIn(text, html)
        for query, present in (('?scope=ambas&channel=tablon_intranet&overdue=1', True),
                               ('?scope=interna', False), ('?channel=web', False),
                               ('?overdue=0', False)):
            self.assertEqual(present, 'Quality results' in self.client.get(
                '/comunicaciones/' + query).get_data(as_text=True))
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar',
                                              data=FORM | {'retired': 'y'}).status_code)
        self.assertNotIn('Quality results', self.client.get('/comunicaciones/').get_data(as_text=True))
        self.assertIn('Quality results', self.client.get('/comunicaciones/?retired=1').get_data(as_text=True))
        self.assertIn('Retirada', self.client.get('/comunicaciones/1').get_data(as_text=True))
        self.assertEqual(['create', 'create', 'update'], self.actions())

    def test_noop_browser_roundtrip_preselects_all_enums_and_audiences(self):
        party = self.seed_with(stakeholders, {'nombre': 'Workers'})
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        provider = self.seed_with(providers.providers,
                                   {'nombre': 'Provider', 'tipo_suministro': 'producto'})
        person = self.seed_with(people, {'nombre': 'Recipient'})
        data = FORM | dict(stakeholder_ids=[str(party)], customer_ids=[str(customer)],
                           provider_ids=[str(provider)], person_ids=[str(person)])
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=data).status_code)
        with self.app.app_context():
            from app.services.actor import Actor
            actor = Actor(1, 'admin', RoleEnum.ADMINISTRADOR, 'cli')
            people.update(db.session, actor, 1, {'activo': False})
            people.update(db.session, actor, person, {'activo': False})
            from datetime import date
            providers.block(db.session, actor, provider, 'Inactive supplier', today=date(2026, 1, 1))
            db.session.commit()
        before = self.actions()
        parser = BrowserForm()
        response = self.client.get('/comunicaciones/1/editar')
        self.assertEqual(200, response.status_code)
        parser.feed(response.get_data(as_text=True))
        for name, expected in data.items():
            self.assertEqual(expected if isinstance(expected, list) else [expected],
                             parser.values.getlist(name), name)
        response = self.client.post('/comunicaciones/1/editar', data=parser.values)
        self.assertEqual(302, response.status_code)
        self.assertEqual(before, self.actions())
        parser2 = BrowserForm()
        parser2.feed(self.client.get('/comunicaciones/1/editar').get_data(as_text=True))
        self.assertEqual(parser.values, parser2.values)

    def test_invalid_fields_and_missing_audience_do_not_write(self):
        from app.models import CommunicationPlan
        before = self.actions()
        for bad, message in (({'title': ''}, 'Este campo es obligatorio.'),
                             ({'content': ' '}, 'Este campo es obligatorio.'),
                             ({'frequency': ''}, 'Este campo es obligatorio.'),
                             ({'owner_id': ''}, 'Este campo es obligatorio.'),
                             ({'owner_id': '999'}, 'Opción inválida.'),
                             ({'scope': 'other'}, 'Opción inválida.'),
                             ({'channel': 'email'}, 'Opción inválida.'),
                             ({'next_date': 'bad'}, 'Este campo es obligatorio.'),
                             ({'audience_text': ''}, 'Indica al menos un destinatario o una descripción de destinatarios.')):
            with self.subTest(bad=bad):
                response = self.client.post('/comunicaciones/nuevo', data=FORM | bad)
                self.assertEqual(200, response.status_code)
                self.assertIn(message, response.get_data(as_text=True))
                self.assertEqual(0, self.count(CommunicationPlan))
                self.assertEqual(before, self.actions())

    def test_role_matrix_web_and_csrf_delete(self):
        from app.models import CommunicationPlan
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM).status_code)
        self.assertEqual(302, self.client.post('/comunicaciones/1/editar', data=FORM).status_code)
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/comunicaciones/1').status_code)
            self.assertIn('href="/comunicaciones/"', self.client.get('/comunicaciones/').get_data(as_text=True))
        before = self.actions()
        for url in ('/comunicaciones/nuevo', '/comunicaciones/1/editar', '/comunicaciones/1/eliminar'):
            response = self.client.post(url, data=FORM)
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.location.endswith('/dashboard/'))
            self.assertEqual(before, self.actions())
        self.login(RoleEnum.AUDITOR)
        self.assertTrue(self.client.post('/comunicaciones/1/eliminar').location.endswith('/dashboard/'))
        self.assertEqual(before, self.actions())
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        for url in ('/comunicaciones/nuevo', '/comunicaciones/1/editar', '/comunicaciones/1/eliminar'):
            self.assertEqual(400, self.client.post(url, data=FORM).status_code)
        self.assertEqual(before, self.actions())
        parser = _PostForms()
        parser.feed(self.client.get('/comunicaciones/1').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        self.assertEqual(302, self.client.post(form['action'], data=form['fields']).status_code)
        self.assertEqual(0, self.count(CommunicationPlan))

    def test_cited_targets_delete_routes_refuse_without_500(self):
        from app.models import Cliente, ParteInteresada, Person, Proveedor
        party = self.seed_with(stakeholders, {'nombre': 'Workers'})
        customer = self.seed_with(clientes, {'nombre': 'Customer'})
        provider = self.seed_with(providers.providers,
                                   {'nombre': 'Provider', 'tipo_suministro': 'producto'})
        person = self.seed_with(people, {'nombre': 'Recipient'})
        # SQLite requires explicit FK enforcement; production PostgreSQL always enforces it.
        with self.app.app_context():
            def foreign_keys(connection, record):
                connection.execute('PRAGMA foreign_keys=ON')
            event.listen(db.engine, 'connect', foreign_keys)
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            db.session.commit()
        self.assertEqual(302, self.client.post('/comunicaciones/nuevo', data=FORM | dict(
            stakeholder_ids=[str(party)], customer_ids=[str(customer)],
            provider_ids=[str(provider)], person_ids=[str(person)])).status_code)
        before = self.actions()
        cases = ((f'/partes_interesadas/eliminar/{party}', ParteInteresada,
                  'La operación entra en conflicto con datos existentes.'),
                 (f'/clientes/{customer}/eliminar', Cliente, clientes.STILL_REFERENCED),
                 (f'/proveedores/{provider}/eliminar', Proveedor, providers.DELETE_CONFLICT),
                 ('/personas/1/eliminar', Person, people.STILL_REFERENCED),
                 (f'/personas/{person}/eliminar', Person, people.STILL_REFERENCED))
        for url, model, message in cases:
            with self.subTest(url=url):
                count = self.count(model)
                response = self.client.post(url)
                self.assertEqual(302, response.status_code)
                self.assertIn(('danger', message), self.flashes())
                self.assertEqual(count, self.count(model))
                self.assertEqual(before, self.actions())
