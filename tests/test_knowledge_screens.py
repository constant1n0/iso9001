"""Knowledge screens: persistence, validation, filters, access and CSRF."""
from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from app.extensions import db
from app.models import RoleEnum
from app.services import people

FORM = dict(kind='conocimiento', title='Tool set-up', summary='Keep the reference sample.',
            source='Workshop', owner_id='1', next_review_date='')


class KnowledgeScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Ana'})
        self.login(RoleEnum.ADMINISTRADOR)

    def test_create_show_edit_retire_filters_and_navigation(self):
        response = self.client.post('/conocimientos/nuevo', data=FORM)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith('/conocimientos/1'))
        self.assertIn(('success', 'Conocimiento registrado.'), self.flashes())
        html = self.client.get(response.location).get_data(as_text=True)
        for value in ('Tool set-up', 'Keep the reference sample.', 'Workshop', 'Ana',
                      'href="/conocimientos/"'):
            self.assertIn(value, html)
        response = self.client.post('/conocimientos/1/editar', data=FORM | {
            'kind': 'leccion', 'summary': 'New evidence', 'next_review_date': '2000-01-01'})
        self.assertEqual(302, response.status_code)
        self.assertIn(('success', 'Conocimiento actualizado.'), self.flashes())
        html = self.client.get('/conocimientos/?overdue=1&kind=leccion').get_data(as_text=True)
        self.assertIn('Tool set-up', html)
        self.assertIn('Revisión vencida', html)
        self.assertNotIn('Tool set-up', self.client.get(
            '/conocimientos/?kind=conocimiento').get_data(as_text=True))
        response = self.client.post('/conocimientos/1/editar', data=FORM | {'retired': 'y'})
        self.assertEqual(302, response.status_code)
        self.assertNotIn('Tool set-up', self.client.get('/conocimientos/').get_data(as_text=True))
        self.assertIn('Tool set-up', self.client.get(
            '/conocimientos/?retired=1').get_data(as_text=True))
        self.assertIn('Retirado', self.client.get('/conocimientos/1').get_data(as_text=True))
        self.assertEqual(['create', 'create', 'update', 'update'], self.actions())

    def test_required_fields_invalid_type_owner_and_date_do_not_write(self):
        from app.models import KnowledgeItem
        before = self.actions()
        for bad, message in (({'title': ' '}, 'Este campo es obligatorio.'),
                             ({'summary': ''}, 'Este campo es obligatorio.'),
                             ({'owner_id': ''}, 'Este campo es obligatorio.'),
                             ({'owner_id': '999'}, 'Opción inválida.'),
                             ({'kind': 'other'}, 'Opción inválida.'),
                             ({'next_review_date': 'bad'}, 'No es una fecha válida.')):
            with self.subTest(bad=bad):
                response = self.client.post('/conocimientos/nuevo', data=FORM | bad)
                self.assertEqual(200, response.status_code)
                self.assertIn(message, response.get_data(as_text=True))
                self.assertEqual(0, self.count(KnowledgeItem))
                self.assertEqual(before, self.actions())

    def test_every_role_reads_auditor_writes_operator_cannot_and_admin_deletes(self):
        from app.models import KnowledgeItem
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.client.post('/conocimientos/nuevo', data=FORM).status_code)
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/conocimientos/1').status_code)
        before = self.actions()
        for url in ('/conocimientos/nuevo', '/conocimientos/1/editar', '/conocimientos/1/eliminar'):
            response = self.client.post(url, data=FORM)
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.location.endswith('/dashboard/'))
            self.assertIn(('danger', 'No tienes permiso para acceder a esta página.'), self.flashes())
            self.assertEqual(1, self.count(KnowledgeItem))
            self.assertEqual(before, self.actions())
        self.login(RoleEnum.AUDITOR)
        self.assertEqual(302, self.client.post('/conocimientos/1/editar', data=FORM | {'retired': 'y'}).status_code)
        before = self.actions()
        self.assertTrue(self.client.post('/conocimientos/1/eliminar').location.endswith('/dashboard/'))
        self.assertEqual(before, self.actions())
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/conocimientos/1/eliminar').status_code)
        self.assertEqual(0, self.count(KnowledgeItem))
        self.assertEqual('delete', self.actions()[-1])

    def test_csrf_protects_writes_and_delete_form(self):
        from app.models import KnowledgeItem
        self.client.post('/conocimientos/nuevo', data=FORM)
        before = self.actions()
        self.app.config['WTF_CSRF_ENABLED'] = True
        for url in ('/conocimientos/nuevo', '/conocimientos/1/editar', '/conocimientos/1/eliminar'):
            self.assertEqual(400, self.client.post(url, data=FORM).status_code)
        self.assertEqual(before, self.actions())
        parser = _PostForms()
        parser.feed(self.client.get('/conocimientos/1').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        self.assertEqual(302, self.client.post(form['action'], data=form['fields']).status_code)
        self.assertEqual(0, self.count(KnowledgeItem))
