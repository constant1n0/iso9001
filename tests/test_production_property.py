"""PS4 public rules: rejected writes preserve all database and audit hashes."""
from datetime import date
from itertools import product
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from test_production import ProductionTests
from test_design import actor
from app.extensions import db
from app.models import RoleEnum
from app.services import clientes, people, providers, reclamaciones, nonconformities
from app.services.errors import Conflict, PermissionDenied

TODAY = date(2026, 1, 2)
ITEM = dict(customer_id=1, identification='Gauge', description='Reference gauge',
    received_date=date(2020, 1, 1), responsible_id=1, location='Workshop', condition='Intact')
INCIDENT = dict(item_id=1, date=date(2020, 1, 2), kind='damage', description='Scratch',
    owner_notified=True, notification_date=date(2020, 1, 2), notification_means='Email E-1')


class PropertyCase(ProductionTests):
    def setUp(self):
        super().setUp()
        clientes.create(db.session, self.who, {'nombre': 'Customer'})
        providers.providers.create(db.session, self.who,
            {'nombre': 'Provider', 'tipo_suministro': 'producto'})
        db.session.commit()
        from app.services import production_property
        self.property = production_property

    def item(self, **values):
        row = self.property.items.create(db.session, self.who, ITEM | values, today=TODAY)
        db.session.commit()
        return row

    def incident(self, **values):
        row = self.property.incidents.create(db.session, self.who, INCIDENT | values, today=TODAY)
        db.session.commit()
        return row

    def update(self, row, **values):
        result = self.property.items.update(db.session, self.who, row.id, values, today=TODAY)
        db.session.commit()
        return result


for _name in tuple(ProductionTests.__dict__):
    if _name.startswith('test_'):
        setattr(PropertyCase, _name, None)
del ProductionTests


class PropertyRules(PropertyCase):
    def test_exactly_one_owner_and_required_fields(self):
        for values in ({'customer_id': None}, {'provider_id': 1}, {'customer_id': 999}):
            self.reject(lambda: self.item(**values))
        for key in ('identification', 'description', 'received_date', 'responsible_id', 'location', 'condition'):
            self.reject(lambda: self.property.items.create(db.session, self.who,
                {k: v for k, v in ITEM.items() if k != key}, today=TODAY),
                f'Faltan campos obligatorios: {key}.')
            self.reject(lambda: self.item(**{key: None}))
        row = self.item(customer_id=None, provider_id=1, reference='S-1')
        self.assertEqual((None, 1, 'custody'), (row.customer_id, row.provider_id, row.status.name))

    def test_received_date_and_active_person_retention(self):
        self.reject(lambda: self.item(received_date=date(2999, 1, 1)))
        row = self.item(received_date=TODAY)
        people.update(db.session, self.who, 1, {'activo': False})
        db.session.commit()
        from test_communication_independent import hashes
        before = hashes()
        self.update(row, responsible_id=1)
        self.assertEqual(before, hashes())
        self.reject(lambda: self.item())
        self.reject(lambda: people.delete(db.session, self.who, 1), people.STILL_REFERENCED, Conflict)

    def test_order_links_new_live_existing_cancelled_retained(self):
        order = self.create()
        row = self.item(order_ids=[order.id, order.id])
        self.service.transition(db.session, self.who, order.id, 'cancelled', cancellation_reason='Stop')
        db.session.commit()
        self.assertEqual([order.id], self.update(row, order_ids=[order.id]).order_ids)
        for bad in ([order.id], [999], [True], None):
            self.reject(lambda: self.item(order_ids=bad))
        self.assertEqual([], self.update(row, order_ids=[]).order_ids)

    def test_terminal_transitions_and_incidents_remain_possible_not_release(self):
        for target in ('returned', 'disposed'):
            row = self.item()
            def transition(**values):
                return self.property.transition(db.session, self.who, row.id,
                    target, today=TODAY, **values)
            for values in ({}, {'terminal_date': TODAY}, {'terminal_date': date(2999, 1, 1), 'terminal_note': 'Return'}):
                self.reject(lambda: transition(**values))
            transition(terminal_date=TODAY, terminal_note='Return')
            db.session.commit()
            self.assertEqual(target, row.status.name)
            self.reject(lambda: self.update(row))
            self.reject(lambda: transition(terminal_date=TODAY, terminal_note='Repeat'))
            self.incident(item_id=row.id)
        self.assertEqual([], self.service.orders.list_(db.session, self.who))

    def test_incident_rules_and_immutability(self):
        self.item()
        for key in INCIDENT:
            self.reject(lambda: self.property.incidents.create(db.session, self.who,
                {k: v for k, v in INCIDENT.items() if k != key}, today=TODAY))
        for values in ({'date': date(2999, 1, 1)}, {'kind': 'release'}, {'description': ' '},
            {'owner_notified': False}, {'owner_notified': None}, {'notification_means': ' '},
            {'notification_date': date(2020, 1, 1)}, {'notification_date': date(2999, 1, 1)}):
            self.reject(lambda: self.incident(**values))
        row = self.incident()
        for channel in ('web', 'mcp'):
            self.reject(lambda: self.property.incidents.update(db.session, actor(channel=channel), row.id, {}))
            self.reject(lambda: self.property.incidents.delete(db.session, actor(channel=channel), row.id),
                error=PermissionDenied if channel == 'mcp' else self.property.ValidationError)

    def test_complaint_nc_citations_and_restrict_every_target(self):
        from app.models import Cliente, Proveedor, ProductionOrder, Reclamacion, NoConformidad
        order = self.create()
        item = self.item(order_ids=[order.id])
        nc = nonconformities.create(db.session, self.who,
            {'descripcion': 'Scratch', 'fecha_detectada': date(2020, 1, 1)})
        complaint = reclamaciones.create(db.session, self.who,
            {'cliente_id': 1, 'descripcion': 'Scratch'}, today=TODAY)
        db.session.commit()
        for key in ('complaint_id', 'nonconformity_id'):
            self.reject(lambda: self.incident(**{key: 999}))
        self.incident(complaint_id=complaint.id, nonconformity_id=nc.id)
        self.item(customer_id=None, provider_id=1)
        db.session.execute(db.text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        for model in (Cliente, Proveedor, ProductionOrder, Reclamacion, NoConformidad):
            from sqlalchemy.exc import IntegrityError
            self.reject(lambda: db.session.execute(model.__table__.delete().where(model.id == 1)), error=IntegrityError)
        self.reject(lambda: self.property.items.delete(db.session, self.who, item.id))

    def test_permissions_delete_rule_and_mcp_no_lifecycle(self):
        row = self.item()
        for role, channel in product(RoleEnum, ('web', 'mcp')):
            who = actor(role, channel)
            self.assertEqual(row.id, self.property.items.get(db.session, who, row.id).id)
            self.assertIn(row, self.property.items.list_(db.session, who))
            if role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.property.items.create(db.session, who, ITEM, today=TODAY), error=PermissionDenied)
                self.reject(lambda: self.property.items.update(db.session, who, row.id, {}, today=TODAY), error=PermissionDenied)
                self.reject(lambda: self.property.incidents.create(db.session, who, INCIDENT, today=TODAY), error=PermissionDenied)
            else:
                self.property.items.update(db.session, who, row.id, {}, today=TODAY)
            if channel == 'mcp' or role is RoleEnum.OPERATIVO:
                self.reject(lambda: self.property.transition(db.session, who, row.id,
                    'returned', today=TODAY, terminal_date=TODAY, terminal_note='Back'), error=PermissionDenied)
            if channel == 'mcp' or role is not RoleEnum.ADMINISTRADOR:
                self.reject(lambda: self.property.items.delete(db.session, who, row.id), error=PermissionDenied)
        for data in ({'status': 'returned'}, {'terminal_note': 'Back'}, {'id': 1}):
            self.reject(lambda: self.property.items.update(db.session, self.who, row.id, data, today=TODAY))
        linked = self.item(order_ids=[self.create().id])
        self.reject(lambda: self.property.items.delete(db.session, self.who, linked.id))
        self.property.items.delete(db.session, self.who, row.id)
        db.session.commit()
        self.assertNotIn(row, self.property.items.list_(db.session, self.who))

    def test_lock_sql_and_post_lock_person_refresh(self):
        from app.models import Person
        row = self.item()
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            self.update(row, location='Store')
            self.incident()
            self.assertTrue(all(refresh for _, refresh in seen))
            self.assertTrue(any('FROM customer_property' in sql and 'FOR UPDATE' in sql for sql, _ in seen))
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        other = people.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        original = self.property._lock
        def deactivate(session, record_id):
            locked = original(session, record_id)
            session.execute(Person.__table__.update().where(Person.id == other.id)
                .values(activo=False).execution_options(synchronize_session=False))
            return locked
        with patch.object(self.property, '_lock', side_effect=deactivate):
            self.reject(lambda: self.update(row, responsible_id=other.id))
