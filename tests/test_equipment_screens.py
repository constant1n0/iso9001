"""Public equipment screens, role boundaries, CSRF and automatic suitability."""
from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from app.extensions import db
from app.models import ComprobacionEquipo, EquipoMedicion, RoleEnum

MASTER = dict(codigo='EQ-1', nombre='Balanza', responsable_id='')
CHECK = dict(fecha='2020-01-01', tipo='calibracion', resultado='apto',
             realizado_por='Lab', referencia='Patrón C-1', certificado='C-1',
             fecha_proxima='2099-01-01', evaluacion_impacto='', acciones='')


class EquipmentScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/equipos/nuevo', data=MASTER).status_code)

    def test_public_read_write_boundaries_and_existing_register_navigation(self):
        self.assertEqual(200, self.client.get('/equipos/').status_code)
        for role in RoleEnum:
            self.login(role)
            response = self.client.get('/equipos/1')
            self.assertEqual(200, response.status_code)
            self.assertIn('href="/equipos/"', response.get_data(as_text=True))
            response = self.client.post('/equipos/1/editar', data=MASTER | dict(nombre=role.name))
            self.assertEqual(302, response.status_code)
            with self.app.app_context():
                name = db.session.get(EquipoMedicion, 1).nombre
            self.assertEqual(role.name if role is not RoleEnum.OPERATIVO else 'AUDITOR', name)

    def test_failed_check_renders_impact_validation_and_can_recover(self):
        response = self.client.post('/equipos/1/comprobaciones/nueva',
                                    data=CHECK | dict(resultado='no_apto', fecha_proxima=''))
        self.assertEqual(200, response.status_code)
        self.assertIn('requiere evaluar', response.get_data(as_text=True))
        self.assertEqual(0, self.count(ComprobacionEquipo))
        self.assertEqual(302, self.client.post('/equipos/1/comprobaciones/nueva',
            data=CHECK | dict(resultado='no_apto', fecha_proxima='',
                             evaluacion_impacto='Lotes A/B', acciones='Revisar A/B')).status_code)
        html = self.client.get('/equipos/1').get_data(as_text=True)
        for value in ('No apto · fuera de servicio', 'Lotes A/B', 'Revisar A/B'):
            self.assertIn(value, html)
        self.assertEqual(302, self.client.post('/equipos/1/comprobaciones/nueva', data=CHECK).status_code)
        self.assertIn('<h2>Apto</h2>', self.client.get('/equipos/1').get_data(as_text=True))
        self.assertEqual(2, self.count(ComprobacionEquipo))
        # Histories have no edit/delete endpoints.
        self.assertEqual(404, self.client.post('/equipos/1/comprobaciones/1/eliminar').status_code)

    def test_csrf_protects_check_recording_and_deletion(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        response = self.client.post('/equipos/1/comprobaciones/nueva', data=CHECK)
        self.assertEqual(400, response.status_code)
        self.assertEqual(0, self.count(ComprobacionEquipo))
        parser = _PostForms()
        parser.feed(self.client.get('/equipos/1').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        self.assertEqual(400, self.client.post(form['action']).status_code)
        self.assertEqual(302, self.client.post(form['action'], data=form['fields']).status_code)
        self.assertEqual(0, self.count(EquipoMedicion))
