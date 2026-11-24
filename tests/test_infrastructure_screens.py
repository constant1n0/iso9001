"""Infrastructure HTTP forms, immutable history, filters and existing navigation."""
from app.extensions import db
from app.models import AuditLog, RoleEnum
from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms

MASTER = dict(codigo='INF-1', nombre='Prensa', tipo='equipo_proceso', ubicacion='Taller',
              frecuencia='Cada mes', fecha_proxima='2020-01-01', responsable_id='')
ENTRY = dict(fecha='2020-01-01', tipo='preventivo', resultado='conforme', realizado_por='Ana',
             evidencia='Parte M-1', acciones='', no_conformidad_id='', fecha_proxima='2099-01-01')


class InfrastructureScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.login(RoleEnum.ADMINISTRADOR)

    def state(self):
        from app.services import infrastructure
        from app.services.audit import snapshot
        from app.services.actor import Actor
        who = Actor(None, 'reader', RoleEnum.ADMINISTRADOR, 'cli')
        with self.app.app_context():
            return ([snapshot(r) for r in infrastructure.items.list_(db.session, who)],
                    [snapshot(r) for r in infrastructure.maintenance.list_(db.session, who)],
                    [snapshot(r) for r in AuditLog.query.order_by(AuditLog.id)])

    def test_forms_history_list_filters_and_previous_equipment_navigation(self):
        self.assertEqual(200, self.client.get('/infraestructura/nuevo').status_code)
        created = self.client.post('/infraestructura/nuevo', data=MASTER)
        self.assertEqual((302, '/infraestructura/1'), (created.status_code, created.location))
        self.assertIn('Elemento registrado.', self.client.get(created.location).get_data(as_text=True))
        html = self.client.get('/infraestructura/').get_data(as_text=True)
        for text in ('INF-1', 'Vencido', 'href="/equipos/"', 'href="/infraestructura/"'):
            self.assertIn(text, html)
        for query, present in (('?tipo=edificio', False), ('?codigo=OTHER', False),
                               ('?tipo=UNKNOWN&retirado=UNKNOWN', True)):
            response = self.client.get('/infraestructura/' + query)
            self.assertEqual(200, response.status_code)
            self.assertEqual(present, 'INF-1' in response.get_data(as_text=True))
        self.assertEqual(302, self.client.post('/infraestructura/1/mantenimientos/nuevo', data=ENTRY).status_code)
        html = self.client.get('/infraestructura/1').get_data(as_text=True)
        for value in ('Parte M-1', 'Preventivo', 'Conforme', '2099-01-01', 'Historial inmutable'):
            self.assertIn(value, html)
        self.assertEqual(404, self.client.post('/infraestructura/1/mantenimientos/1/editar').status_code)
        self.assertEqual(404, self.client.post('/infraestructura/1/mantenimientos/1/eliminar').status_code)
        before = self.state()
        response = self.client.post('/infraestructura/1/eliminar', follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('retíralo en su lugar', response.get_data(as_text=True))
        self.assertEqual(before, self.state())
        # The existing register still renders and accepts its previous payload.
        response = self.client.post('/equipos/nuevo', data=dict(codigo='EQ-1', nombre='Balanza', responsable_id=''))
        self.assertEqual((302, '/equipos/1'), (response.status_code, response.location))
        self.assertIn('Balanza', self.client.get(response.location).get_data(as_text=True))

    def test_rejection_messages_roles_retirement_and_unchanged_audit(self):
        self.assertEqual(302, self.client.post('/infraestructura/nuevo', data=MASTER).status_code)
        for changes, text in ((dict(resultado='no_conforme'), 'requiere indicar las acciones'),
                              (dict(fecha='2999-01-01'), 'no puede ser futura'),
                              (dict(fecha_proxima='2020-01-01'), 'posterior al mantenimiento')):
            before = self.state()
            response = self.client.post('/infraestructura/1/mantenimientos/nuevo', data=ENTRY | changes)
            self.assertEqual(200, response.status_code)
            self.assertIn(text, response.get_data(as_text=True))
            self.assertEqual(before, self.state())
        for role in RoleEnum:
            self.login(role)
            self.assertEqual(200, self.client.get('/infraestructura/1').status_code)
            before = self.state()
            response = self.client.post('/infraestructura/1/editar', data=MASTER | dict(nombre=role.name))
            self.assertEqual(302, response.status_code)
            if role is RoleEnum.OPERATIVO:
                self.assertEqual('/dashboard/', response.location)
                self.assertEqual(before, self.state())
            else:
                self.assertEqual('/infraestructura/1', response.location)
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertEqual(302, self.client.post('/infraestructura/1/editar', data=MASTER | dict(retirado='y')).status_code)
        before = self.state()
        response = self.client.post('/infraestructura/1/mantenimientos/nuevo', data=ENTRY)
        self.assertEqual(200, response.status_code)
        self.assertIn('elemento retirado', response.get_data(as_text=True))
        self.assertEqual(before, self.state())

    def test_csrf_and_uncited_deletion(self):
        self.assertEqual(302, self.client.post('/infraestructura/nuevo', data=MASTER).status_code)
        self.app.config['WTF_CSRF_ENABLED'] = True
        before = self.state()
        self.assertEqual(400, self.client.post('/infraestructura/1/mantenimientos/nuevo', data=ENTRY).status_code)
        self.assertEqual(400, self.client.post('/infraestructura/1/eliminar').status_code)
        self.assertEqual(before, self.state())
        parser = _PostForms()
        parser.feed(self.client.get('/infraestructura/1').get_data(as_text=True))
        form, = parser.forms
        self.assertTrue(form['fields']['csrf_token'])
        response = self.client.post(form['action'], data=form['fields'])
        self.assertEqual((302, '/infraestructura/'), (response.status_code, response.location))
        self.assertEqual([], self.state()[0])
