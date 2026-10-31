"""Customer routes, permissions, validation, paging and deletion forms."""
from datetime import date
from pathlib import Path
import sys

if __package__ == 'tests':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from app.extensions import db
from app.models import Cliente, RoleEnum, SatisfaccionCliente
from mcp_support import survey_values

BASE = '/clientes'
FORM = {'nombre': 'Farmacia Sol', 'nif': 'b-12345678', 'contacto': 'Ana',
        'email': 'SOL@example.com', 'telefono': '123', 'notas': 'Notas'}


class CustomerScreensTestCase(RegisterRoutesBase):
    def create(self, **values):
        self.login(RoleEnum.ADMINISTRADOR)
        response = self.client.post(f'{BASE}/nuevo', data=FORM | values)
        self.assertEqual(302, response.status_code)
        return int(response.headers['Location'].rsplit('/', 1)[-1])

    def test_create_edit_show_and_delete_are_audited(self):
        record_id = self.create()
        page = self.client.get(f'{BASE}/{record_id}').get_data(as_text=True)
        for text in ('Farmacia Sol', 'B12345678', 'sol@example.com', 'Notas'):
            self.assertIn(text, page)
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/editar', data=FORM).status_code)
        self.assertEqual(['create'], self.actions())
        self.client.post(f'{BASE}/{record_id}/editar', data=FORM | {'nombre': 'Sol SL'})
        self.assertEqual(['create', 'update'], self.actions())
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/eliminar').status_code)
        self.assertEqual(['create', 'update', 'delete'], self.actions())
        self.assertEqual(404, self.client.get(f'{BASE}/{record_id}').status_code)

    def test_roles_and_post_only_delete(self):
        record_id = self.create()
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get(f'{BASE}/').status_code)
            self.assertEqual(200, self.client.get(f'{BASE}/{record_id}').status_code)
            writable = role is not RoleEnum.OPERATIVO
            self.assertEqual(200 if writable else 302, self.client.get(f'{BASE}/nuevo').status_code)
            self.assertEqual(200 if writable else 302,
                             self.client.get(f'{BASE}/{record_id}/editar').status_code)
        self.assertEqual(405, self.client.get(f'{BASE}/{record_id}/eliminar').status_code)
        self.login(RoleEnum.AUDITOR)
        self.client.post(f'{BASE}/{record_id}/eliminar')
        self.assertEqual(1, self.count(Cliente))

    def test_invalid_and_duplicate_input_keeps_form_values(self):
        self.create()
        for values, text in (({'nif': 'bad', 'nombre': 'Otra'}, 'El NIF'),
                             ({'email': 'bad', 'nombre': 'Otra'}, 'correo electrónico válida'),
                             ({}, 'Ya existe un cliente')):
            response = self.client.post(f'{BASE}/nuevo', data=FORM | values)
            self.assertEqual(200, response.status_code)
            self.assertIn(text, response.get_data(as_text=True))
            self.assertIn('name="nombre"', response.get_data(as_text=True))
        self.assertEqual(1, self.count(Cliente))

    def test_name_search_pagination_and_navigation(self):
        self.create(nombre='Alfa', nif='')
        self.create(nombre='Zeta', nif='')
        html = self.client.get(f'{BASE}/?nombre=ETA&per_page=1').get_data(as_text=True)
        results = html.split('<tbody>')[1].split('</tbody>')[0]
        self.assertIn('Zeta', results)
        self.assertNotIn('Alfa', results)
        html = self.client.get(f'{BASE}/?per_page=1').get_data(as_text=True)
        self.assertIn('Siguiente', html)
        self.assertLess(html.index('href="/clientes/"'), html.index('href="/reclamaciones/"'))
        self.assertLess(html.index('href="/reclamaciones/"'), html.index('href="/satisfaccion_cliente/"'))

    def test_referenced_customer_is_not_deleted(self):
        with self.app.app_context():
            values = survey_values('Con encuesta')
            record_id = values['cliente_id']
            db.session.execute(SatisfaccionCliente.__table__.insert().values(
                **values, fecha_encuesta=date(2026, 1, 1), puntuacion=8))
            db.session.commit()
        self.login(RoleEnum.ADMINISTRADOR)
        response = self.client.post(f'{BASE}/{record_id}/eliminar', follow_redirects=True)
        self.assertIn('tiene encuestas', response.get_data(as_text=True))
        self.assertEqual(1, self.count(Cliente))

    def test_delete_form_requires_csrf(self):
        record_id = self.create()
        self.app.config['WTF_CSRF_ENABLED'] = True
        page = _PostForms()
        page.feed(self.client.get(f'{BASE}/{record_id}').get_data(as_text=True))
        form, = page.forms
        self.assertTrue(form['fields']['csrf_token'])
        self.assertEqual(400, self.client.post(form['action']).status_code)
        self.assertEqual(302, self.client.post(form['action'], data=form['fields']).status_code)
