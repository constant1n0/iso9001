"""Independent PS4 regressions: S4 custody/evidence/boundaries and S3."""
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from flask import g
from test_production_property import PropertyCase, TODAY
from test_communication_independent import BrowserForm, hashes
from app.extensions import db
from app.models import CustomerProperty, RoleEnum
from app.services import clientes, nonconformities, reclamaciones
from app.services.errors import Conflict


class IndependentProperty(PropertyCase):
    def admin(self):
        self.login(RoleEnum.ADMINISTRADOR)
        g.pop('_login_user', None)

    def browser(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_s4_conflicting_empty_order_selection_is_not_silently_dropped(self):
        row = self.item(order_ids=[self.create().id])
        self.admin()
        path = f'/produccion/propiedad/{row.id}/editar'
        form = self.browser(path)
        form.setlist('order_ids', ['', '1'])
        form['location'] = 'New bay'
        before = hashes()
        response = self.client.post(path, data=form)
        self.assertEqual(400, response.status_code,
                         'Explicit "Sin órdenes" was ignored with a success redirect')
        self.assertEqual(before, hashes())

    def test_s4_both_owner_forms_roundtrip_without_writes(self):
        for values in ({}, {'customer_id': None, 'provider_id': 1}):
            row = self.item(description='Línea <A> & B\nSegunda línea', **values)
            self.admin()
            path = f'/produccion/propiedad/{row.id}/editar'
            form = self.browser(path)
            before = hashes()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashes())

    def test_s4_referenced_delete_paths_refuse_with_message_not_500(self):
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        self.item(order_ids=[self.create().id])
        self.item(customer_id=None, provider_id=1)
        customer = clientes.create(db.session, self.who, {'nombre': 'Other customer'})
        complaint = reclamaciones.create(db.session, self.who,
            {'cliente_id': customer.id, 'descripcion': 'Damage'}, today=TODAY)
        nc = nonconformities.create(db.session, self.who,
            {'descripcion': 'Damage', 'fecha_detectada': TODAY})
        db.session.commit()
        self.incident(complaint_id=complaint.id, nonconformity_id=nc.id)
        self.reject(lambda: reclamaciones.delete(db.session, self.who, complaint.id),
                    error=Conflict)
        self.admin()
        for path in ('/clientes/1/eliminar', '/proveedores/1/eliminar',
                     '/personas/1/eliminar', '/produccion/1/eliminar',
                     '/no_conformidades/eliminar/1'):
            with self.subTest(path=path):
                with self.client.session_transaction() as session:
                    session.pop('_flashes', None)
                before = hashes()
                self.assertEqual(302, self.client.post(path).status_code)
                self.assertEqual(before, hashes())
                self.assertTrue(any(level == 'danger' and message
                                    for level, message in self.flashes()))

    def test_s3_s4_terminal_incidents_never_change_or_release_order(self):
        order = self.create()
        for target in ('returned', 'disposed'):
            row = self.item(order_ids=[order.id])
            self.property.transition(db.session, self.who, row.id, target,
                today=TODAY, terminal_date=TODAY, terminal_note='Owner receipt')
            db.session.commit()
            self.incident(item_id=row.id, date=TODAY, notification_date=TODAY)
            self.reject(lambda: self.update(row, location='Changed'), self.property.READ_ONLY)
            self.reject(lambda: self.property.items.delete(db.session, self.who, row.id),
                        self.property.READ_ONLY)
            self.assertEqual('planned', order.state.name)
        for target in ('in_progress', 'finished'):
            self.service.transition(db.session, self.who, order.id, target)
        db.session.commit()
        self.assertEqual('finished', order.state.name)
        self.assertNotIn('released', type(order.state).__members__)

    def test_s4_notification_boundary_exact_error_and_atomicity(self):
        self.item()
        for values in ({'date': TODAY, 'notification_date': TODAY - timedelta(days=1)},
                       {'date': TODAY, 'notification_date': TODAY + timedelta(days=1)}):
            self.reject(lambda: self.incident(**values),
                'La notificación debe ser posterior o igual a la incidencia; ninguna fecha puede ser futura.')
        self.reject(lambda: self.incident(owner_notified=False),
                    'Registra la notificación al dueño antes de guardar la incidencia.')
        self.incident(date=TODAY, notification_date=TODAY)

    def test_s4_json_and_uploaded_terminal_fields_refused_with_csrf(self):
        self.item()
        self.admin()
        self.app.config['WTF_CSRF_ENABLED'] = True
        form = self.browser('/produccion/propiedad/1')
        headers = {'X-CSRFToken': form['csrf_token']}
        for suffix in ('editar', 'transicion', 'eliminar', 'incidencias/nueva'):
            before = hashes()
            response = self.client.post('/produccion/propiedad/1/' + suffix,
                headers=headers, json={'target': 'returned', 'terminal_note': 'Injected'})
            self.assertEqual(400, response.status_code)
            self.assertEqual(before, hashes())
        before = hashes()
        response = self.client.post('/produccion/propiedad/1/transicion', headers=headers,
            data={'target': 'returned', 'terminal_date': TODAY.isoformat(),
                  'terminal_note': (BytesIO(b'Injected'), 'note.txt')})
        self.assertEqual(400, response.status_code)
        self.assertEqual(before, hashes())

    def test_s4_cached_item_refreshes_before_editability_validation(self):
        row = self.item()
        original = self.property._lock
        def competing_return(session, record_id):
            session.execute(CustomerProperty.__table__.update().where(
                CustomerProperty.id == record_id).values(status='returned',
                terminal_date=TODAY, terminal_note='Concurrent return')
                .execution_options(synchronize_session=False))
            return original(session, record_id)
        with patch.object(self.property, '_lock', side_effect=competing_return):
            self.reject(lambda: self.update(row, location='Wrong'), self.property.READ_ONLY)
