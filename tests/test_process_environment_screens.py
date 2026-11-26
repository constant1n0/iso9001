"""Environment forms, process detail integration, permissions and existing navigation."""
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import process_operations
from app.services.actor import Actor
from app.services.audit import snapshot
from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms

MASTER = dict(proceso_id='1', factor='fisico', descripcion='Temperatura del taller',
              criterio='18 a 25 °C', frecuencia='Mensual', fecha_proxima='2020-01-01', responsable_id='')
ENTRY = dict(fecha='2020-01-01', resultado='conforme', realizado_por='Ana',
             evidencia='Lectura 21 °C', acciones='', fecha_proxima='2099-01-01')
WHO = Actor(None, 'fixture', RoleEnum.ADMINISTRADOR, 'cli')


class ProcessEnvironmentScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.login(RoleEnum.ADMINISTRADOR)
        with self.app.app_context():
            process_operations.create(db.session, WHO, {'proceso': 'Fabricación', 'recursos': 'Original'})
            process_operations.create(db.session, WHO, {'proceso': 'Entrega'})
            db.session.commit()

    def state(self):
        from app.services import process_environment
        with self.app.app_context():
            return ([snapshot(r) for r in process_environment.items.list_(db.session, WHO)],
                    [snapshot(r) for r in process_environment.checks.list_(db.session, WHO)],
                    [snapshot(r) for r in AuditLog.query.order_by(AuditLog.id)])

    def test_forms_filters_history_process_detail_and_preserved_navigation(self):
        self.assertEqual(200, self.client.get('/ambiente/nuevo').status_code)
        response = self.client.post('/ambiente/nuevo', data=MASTER)
        self.assertEqual((302, '/ambiente/1'), (response.status_code, response.location))
        self.assertIn('Condición registrada.', self.client.get(response.location).get_data(as_text=True))
        for query, present in (('', True), ('?factor=social', False), ('?proceso_id=2', False),
                               ('?retirado=1', False), ('?vencida=1', True), ('?vencida=0', False),
                               ('?factor=UNKNOWN&proceso_id=999&retirado=x&vencida=x', True)):
            result = self.client.get('/ambiente/' + query)
            self.assertEqual(200, result.status_code)
            self.assertEqual(present, 'Temperatura del taller' in result.get_data(as_text=True))
        html = self.client.get('/ambiente/').get_data(as_text=True)
        for text in ('Vencida', 'href="/ambiente/"', 'href="/infraestructura/"', 'href="/equipos/"'):
            self.assertIn(text, html)
        html = self.client.get('/procesos/1').get_data(as_text=True)
        for text in ('Temperatura del taller', 'href="/ambiente/1"', 'Original', 'Criterios de operación'):
            self.assertIn(text, html)
        self.assertNotIn('action="/procesos/1/eliminar"', html)
        before = self.state()
        refused = self.client.post('/procesos/1/eliminar', follow_redirects=True)
        self.assertEqual(200, refused.status_code)
        self.assertIn('No se puede eliminar un proceso con condiciones de ambiente.', refused.get_data(as_text=True))
        self.assertEqual(before, self.state())
        self.assertEqual(302, self.client.post('/ambiente/1/comprobaciones/nueva', data=ENTRY).status_code)
        html = self.client.get('/ambiente/1').get_data(as_text=True)
        for value in ('Lectura 21 °C', 'Conforme', '2099-01-01', 'Historial inmutable'):
            self.assertIn(value, html)
        for action in ('editar', 'eliminar'):
            before = self.state()
            self.assertEqual(404, self.client.post('/ambiente/1/comprobaciones/1/' + action).status_code)
            self.assertEqual(before, self.state())
        before = self.state()
        refused = self.client.post('/ambiente/1/eliminar', follow_redirects=True)
        self.assertEqual(200, refused.status_code)
        self.assertIn('retírala en su lugar', refused.get_data(as_text=True))
        self.assertEqual(before, self.state())
        # Previous process detail and uncited deletion remain available.
        html = self.client.get('/procesos/2').get_data(as_text=True)
        self.assertIn('Entrega', html)
        self.assertIn('action="/procesos/2/eliminar"', html)
        self.assertEqual(302, self.client.post('/procesos/2/eliminar').status_code)
        self.assertEqual(404, self.client.get('/procesos/2').status_code)
        self.assertEqual(200, self.client.get('/infraestructura/').status_code)

    def test_rejections_permissions_retirement_and_audit(self):
        self.assertEqual(302, self.client.post('/ambiente/nuevo', data=MASTER).status_code)
        for changes, message in (({'resultado': 'no_conforme'}, 'Un resultado no conforme requiere indicar las acciones realizadas.'),
                                 ({'fecha': '2999-01-01'}, 'La fecha de comprobación no puede ser futura.'),
                                 ({'fecha_proxima': '2020-01-01'}, 'Indica una nueva próxima fecha posterior a la comprobación.')):
            before = self.state()
            result = self.client.post('/ambiente/1/comprobaciones/nueva', data=ENTRY | changes)
            self.assertEqual(200, result.status_code)
            self.assertIn(message, result.get_data(as_text=True))
            self.assertEqual(before, self.state())
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/ambiente/1').status_code)
            before = self.state()
            result = self.client.post('/ambiente/1/editar', data=MASTER | {'descripcion': role.name})
            self.assertEqual(302, result.status_code)
            if role is RoleEnum.OPERATIVO:
                self.assertEqual('/dashboard/', result.location)
                self.assertEqual(before, self.state())
            else:
                self.assertEqual('/ambiente/1', result.location)
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/ambiente/1/editar', data=MASTER | {'retirado': 'y'}).status_code)
        before = self.state()
        result = self.client.post('/ambiente/1/comprobaciones/nueva', data=ENTRY)
        self.assertEqual(200, result.status_code)
        self.assertIn('No se pueden registrar comprobaciones de una condición retirada.', result.get_data(as_text=True))
        self.assertEqual(before, self.state())

    def test_csrf_and_condition_without_history_can_be_deleted(self):
        self.assertEqual(302, self.client.post('/ambiente/nuevo', data=MASTER).status_code)
        self.app.config['WTF_CSRF_ENABLED'] = True
        before = self.state()
        for url, data in (('/ambiente/1/comprobaciones/nueva', ENTRY), ('/ambiente/1/eliminar', {})):
            self.assertEqual(400, self.client.post(url, data=data).status_code)
            self.assertEqual(before, self.state())
        parser = _PostForms()
        parser.feed(self.client.get('/ambiente/1').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        response = self.client.post(form['action'], data=form['fields'])
        self.assertEqual((302, '/ambiente/'), (response.status_code, response.location))
        self.assertEqual([], self.state()[0])
