"""Read confirmation screens: permissions, pickers, validation and person history."""
from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from app.extensions import db
from app.models import ConfirmacionLectura, Document, DocumentCategory, DocumentRevision, Person, RoleEnum

BASE = '/confirmaciones'
FORM = dict(persona_id='1', tipo='objetivos', document_revision_id='',
            fecha_confirmacion='2020-01-01', notas='Evidence')


class ConfirmationScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            db.session.execute(Person.__table__.insert(), [dict(nombre='Ana', activo=True),
                                                           dict(nombre='Inactive', activo=False)])
            document = db.session.execute(Document.__table__.insert().values(
                title='Policy', code='POL-1', category=DocumentCategory.POLITICA_CALIDAD)).inserted_primary_key[0]
            self.revision = db.session.execute(DocumentRevision.__table__.insert().values(
                document_id=document, numero=1, content='Private draft text')).inserted_primary_key[0]
            db.session.commit()
        self.login()

    def create(self, **values):
        response = self.client.post(BASE + '/nueva', data=FORM | values)
        self.assertEqual(302, response.status_code)
        return int(response.headers['Location'].rsplit('/', 1)[-1])

    def test_create_show_edit_noop_and_admin_delete_are_audited(self):
        record_id = self.create()
        html = self.client.get(f'{BASE}/{record_id}').get_data(as_text=True)
        for value in ('Ana', 'Objetivos de calidad', '01/01/2020', 'Evidence'):
            self.assertIn(value, html)
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/editar', data=FORM).status_code)
        self.assertEqual(['create'], self.actions())
        self.client.post(f'{BASE}/{record_id}/editar', data=FORM | {'notas': 'Changed'})
        self.assertEqual(['create', 'update'], self.actions())
        for role in (RoleEnum.AUDITOR, RoleEnum.OPERATIVO):
            self.login(role)
            self.client.post(f'{BASE}/{record_id}/eliminar')
            self.assertEqual(1, self.count(ConfirmacionLectura))
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(405, self.client.get(f'{BASE}/{record_id}/eliminar').status_code)
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/eliminar').status_code)
        self.assertEqual(['create', 'update', 'delete'], self.actions())
        self.assertEqual(404, self.client.get(f'{BASE}/{record_id}').status_code)

    def test_all_roles_read_create_edit_and_navigation(self):
        record_id = self.create()
        for role in RoleEnum:
            self.login(role)
            for url in (BASE + '/', BASE + '/nueva', f'{BASE}/{record_id}', f'{BASE}/{record_id}/editar'):
                response = self.client.get(url)
                self.assertEqual(200, response.status_code)
                self.assertIn('href="/confirmaciones/"', response.get_data(as_text=True))

    def test_invalid_forms_preserve_values_and_do_not_write(self):
        for values, message in [({'tipo': 'documento'}, 'requiere una revisión'),
                                ({'document_revision_id': str(self.revision)}, 'Solo una confirmación de documento'),
                                ({'fecha_confirmacion': '2999-01-01'}, 'no puede ser futura'),
                                ({'persona_id': '2'}, 'Opción inválida.'),
                                ({'persona_id': '999'}, 'Opción inválida.'),
                                ({'document_revision_id': '999'}, 'Opción inválida.')]:
            with self.subTest(values=values):
                response = self.client.post(BASE + '/nueva', data=FORM | values)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn(message, html)
                self.assertIn('Evidence', html)
                self.assertEqual(0, self.count(ConfirmacionLectura))
                self.assertEqual([], self.actions())

    def test_picker_keeps_inactive_person_on_edit(self):
        record_id = self.create()
        with self.app.app_context():
            db.session.execute(Person.__table__.update().where(Person.id == 1).values(activo=False))
            db.session.commit()
        html = self.client.get(f'{BASE}/{record_id}/editar').get_data(as_text=True)
        self.assertIn('Ana (desactivada)', html)
        self.assertNotIn('Inactive', html)
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/editar', data=FORM).status_code)

    def test_filters_paging_and_person_history(self):
        first = self.create()
        second = self.create(tipo='documento', document_revision_id=str(self.revision))
        html = self.client.get(BASE + '/?tipo=documento&persona_id=1&document_revision_id=' +
                               str(self.revision)).get_data(as_text=True)
        body = html.split('<tbody>')[1].split('</tbody>')[0]
        self.assertIn(f'{BASE}/{second}', body)
        self.assertNotIn(f'{BASE}/{first}"', body)
        html = self.client.get(BASE + '/?per_page=1').get_data(as_text=True)
        self.assertIn('Siguiente', html)
        html = self.client.get('/personas/1').get_data(as_text=True)
        self.assertIn('Confirmaciones de lectura', html)
        self.assertIn(f'{BASE}/{first}', html)
        self.assertIn(f'{BASE}/{second}', html)
        self.assertNotIn('Private draft text', html)

    def test_csrf_protects_delete(self):
        record_id = self.create()
        self.login(RoleEnum.ADMINISTRADOR)
        self.app.config['WTF_CSRF_ENABLED'] = True
        parser = _PostForms()
        parser.feed(self.client.get(f'{BASE}/{record_id}').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        self.assertEqual(400, self.client.post(form['action']).status_code)
        self.assertEqual(302, self.client.post(form['action'], data=form['fields']).status_code)
