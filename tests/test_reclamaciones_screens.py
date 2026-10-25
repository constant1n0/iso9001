"""Complaint screens: lifecycle forms, CSRF, state guards and permissions."""
from datetime import date
from pathlib import Path
import sys
from unittest.mock import patch

if __package__ == 'tests':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from register_routes import RegisterRoutesBase
from test_delete_forms import _PostForms
from mcp_support import survey_values
from app.extensions import db
from app.models import EstadoReclamacion as E, NoConformidad, Reclamacion, RoleEnum

BASE = '/reclamaciones'
TODAY = date(2026, 10, 18)
FORM = {'cliente_id': '1', 'fecha_recepcion': TODAY.isoformat(),
        'descripcion': 'Pedido incompleto', 'canal': 'web', 'no_conformidad_id': ''}


class ComplaintScreensTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            survey_values('Farmacia Sol')
            db.session.commit()
        self.today = patch('app.routes.reclamacion_routes.local_today', return_value=TODAY)
        self.today.start()
        self.addCleanup(self.today.stop)

    def create(self, **values):
        self.login()
        response = self.client.post(f'{BASE}/nueva', data=FORM | values)
        self.assertEqual(302, response.status_code)
        return int(response.headers['Location'].rsplit('/', 1)[-1])

    def html(self, record_id):
        return self.client.get(f'{BASE}/{record_id}').get_data(as_text=True)

    def test_received_responded_closed_and_admin_reopen(self):
        record_id = self.create()
        html = self.html(record_id)
        self.assertIn('Registrar respuesta', html)
        self.assertNotIn(f'{BASE}/{record_id}/cerrar', html)
        self.assertNotIn(f'{BASE}/{record_id}/reabrir', html)
        self.assertEqual(302, self.client.post(f'{BASE}/{record_id}/responder',
                                             data={'respuesta': 'Completado'}).status_code)
        html = self.html(record_id)
        self.assertIn('Completado', html)
        self.assertIn(f'{BASE}/{record_id}/cerrar', html)
        self.assertNotIn(f'{BASE}/{record_id}/cancelar', html)
        self.client.post(f'{BASE}/{record_id}/cerrar')
        self.assertEqual(E.cerrada, self.fetch(Reclamacion, record_id).estado)
        self.assertNotIn(f'{BASE}/{record_id}/editar', self.html(record_id))
        self.assertEqual(302, self.client.get(f'{BASE}/{record_id}/editar').status_code)
        self.client.post(f'{BASE}/{record_id}/reabrir')
        self.assertEqual(E.cerrada, self.fetch(Reclamacion, record_id).estado)
        self.login(RoleEnum.ADMINISTRADOR)
        self.assertIn(f'{BASE}/{record_id}/reabrir', self.html(record_id))
        self.client.post(f'{BASE}/{record_id}/reabrir')
        found = self.fetch(Reclamacion, record_id)
        self.assertEqual((E.respondida, None, 'Completado'),
                         (found.estado, found.fecha_cierre, found.respuesta))
        self.assertEqual(['create', 'update', 'update', 'update'], self.actions())

    def test_cancel_guards_and_reopen_clear_reason(self):
        record_id = self.create()
        html = self.client.post(f'{BASE}/{record_id}/cerrar', follow_redirects=True).get_data(as_text=True)
        self.assertIn('no permite esta operación', html)
        self.client.post(f'{BASE}/{record_id}/cancelar', data={'motivo_cancelacion': 'Duplicada'})
        self.assertEqual(E.cancelada, self.fetch(Reclamacion, record_id).estado)
        self.login(RoleEnum.ADMINISTRADOR)
        self.client.post(f'{BASE}/{record_id}/reabrir')
        found = self.fetch(Reclamacion, record_id)
        self.assertEqual((E.recibida, None), (found.estado, found.motivo_cancelacion))

    def test_forms_show_customer_enums_and_optional_nc(self):
        with self.app.app_context():
            nc_id = db.session.execute(NoConformidad.__table__.insert().values(
                descripcion='Falta una pieza', fecha_detectada=TODAY)).inserted_primary_key[0]
            db.session.commit()
        record_id = self.create(no_conformidad_id=str(nc_id))
        html = self.client.get(f'{BASE}/{record_id}/editar').get_data(as_text=True)
        self.assertIn('Farmacia Sol', html)
        self.assertIn('Teléfono', html)
        self.assertIn('Falta una pieza', html)
        for name in ('estado', 'respuesta', 'fecha_respuesta', 'fecha_cierre', 'motivo_cancelacion'):
            self.assertNotIn(f'name="{name}"', html)
        self.assertIn(f'/no_conformidades/{nc_id}', self.html(record_id))
        self.client.post(f'{BASE}/{record_id}/editar', data=FORM | {'descripcion': 'Actualizada'})
        self.assertIn('Actualizada', self.html(record_id))

    def test_invalid_input_rerenders_and_cannot_set_state(self):
        self.login()
        for values in ({'cliente_id': '999'}, {'descripcion': ''}, {'canal': 'invalid'},
                       {'fecha_recepcion': '2026-10-19'}, {'no_conformidad_id': '999'}):
            self.assertEqual(200, self.client.post(f'{BASE}/nueva', data=FORM | values).status_code)
        self.assertEqual(0, self.count(Reclamacion))
        record_id = self.create(estado='cerrada', respuesta='Injected')
        found = self.fetch(Reclamacion, record_id)
        self.assertEqual((E.recibida, None), (found.estado, found.respuesta))

    def test_all_roles_read_create_edit_and_transitions_are_post_only(self):
        record_id = self.create()
        for role in RoleEnum:
            self.login(role)
            for path in ('/', '/nueva', f'/{record_id}', f'/{record_id}/editar'):
                self.assertEqual(200, self.client.get(BASE + path).status_code)
        for transition in ('responder', 'cerrar', 'cancelar', 'reabrir'):
            self.assertEqual(405, self.client.get(f'{BASE}/{record_id}/{transition}').status_code)
        self.assertEqual(404, self.client.get(f'{BASE}/999').status_code)

    def test_filters_pagination_and_untrusted_text(self):
        record_id = self.create(descripcion='<script>unsafe</script>')
        self.create(descripcion='Segunda', canal='email')
        html = self.client.get(f'{BASE}/?cliente_id=1&estado=recibida&canal=web').get_data(as_text=True)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', html)
        self.assertNotIn('Segunda', html)
        self.assertNotIn('<script>unsafe</script>', self.html(record_id))
        self.assertIn('Siguiente', self.client.get(f'{BASE}/?per_page=1').get_data(as_text=True))

    def test_transition_forms_require_csrf_tokens(self):
        record_id = self.create()
        self.app.config['WTF_CSRF_ENABLED'] = True
        page = _PostForms()
        page.feed(self.html(record_id))
        self.assertEqual(2, len(page.forms))
        for form in page.forms:
            self.assertTrue(form['fields']['csrf_token'])
            self.assertEqual(400, self.client.post(form['action']).status_code)
        form = next(f for f in page.forms if f['action'].endswith('/responder'))
        self.assertEqual(302, self.client.post(form['action'],
                         data=form['fields'] | {'respuesta': 'Enviado'}).status_code)
