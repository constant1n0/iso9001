"""Customer evidence and optional production citations through public interfaces."""
from datetime import timedelta
from pathlib import Path
import importlib.util
from unittest.mock import patch

from sqlalchemy import event, inspect
from sqlalchemy.dialects import postgresql
from alembic.migration import MigrationContext
from alembic.operations import Operations
from werkzeug.datastructures import MultiDict

from test_customer_orders import OrderCase, TODAY
from test_communication_independent import BrowserForm, hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import AuditLog, RoleEnum
from app.services import (communication, communication_log, process_operations,
                          production, reclamaciones, clientes)
from app.services.errors import Conflict, ValidationError


class LinkRules(OrderCase):
    def setUp(self):
        super().setUp()
        process_operations.create(db.session, self.who, {'proceso': 'Workshop'})
        db.session.commit()

    def accepted(self):
        row = self.create()
        self.review(self.latest(row))
        return row

    def production(self, **values):
        return production.orders.create(db.session, self.who, dict(
            code='P-1', kind='production', title='Containers', process_id=1) | values)

    def evidence(self):
        plan = communication.items.create(db.session, self.who, dict(title='Customer call',
            content='Requirements', scope='externa', channel='telefono', owner_id=1,
            frequency='On change', next_date=TODAY, audience_text='Customer'))
        log = communication_log.items.create(db.session, self.who, dict(plan_id=plan.id,
            date=TODAY, communicator_id=1, channel='telefono', evidence='Call reference',
            next_date=TODAY + timedelta(days=1)), today=TODAY)
        complaint = reclamaciones.create(db.session, self.who,
            dict(cliente_id=1, descripcion='Late containers'), today=TODAY)
        db.session.commit()
        return log.id, complaint.id

    def update_links(self, row, **values):
        try:
            result = self.service.orders.update(db.session, self.who, row.id, values, today=TODAY)
        except ValidationError as exc:
            self.fail(f'Evidence update refused: {exc}')
        db.session.commit()
        return result

    def test_new_citation_requires_accepted_and_same_customer(self):
        row = self.create()
        self.refuse(lambda: self.production(customer_order_id=row.id),
                    'Elige un pedido aceptado (comprometido).')
        self.review(self.latest(row), result='rejected')
        self.refuse(lambda: self.production(customer_order_id=row.id),
                    'Elige un pedido aceptado (comprometido).')
        self.revision(row)
        self.review(self.latest(row))
        other = clientes.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        self.refuse(lambda: self.production(customer_id=other.id, customer_order_id=row.id),
                    'El pedido debe pertenecer al mismo cliente que la orden de producción.')
        linked = self.production(customer_id=1, customer_order_id=row.id)
        db.session.commit()
        self.assertEqual(row.id, production.orders.get(db.session, self.who, linked.id).customer_order_id)
        self.refuse(lambda: production.orders.update(db.session, self.who, linked.id,
            {'customer_id': other.id}),
            'El pedido debe pertenecer al mismo cliente que la orden de producción.')

    def test_retained_citation_after_revision_and_cancellation(self):
        row = self.accepted()
        try:
            linked = self.production(customer_order_id=row.id)
        except ValidationError as exc:
            self.fail(f'Accepted citation refused: {exc}')
        db.session.commit()
        self.revision(row)
        before = hashes()
        production.orders.update(db.session, self.who, linked.id, {'customer_order_id': row.id})
        db.session.commit()
        self.assertEqual(before, hashes())
        self.service.cancel(db.session, self.who, row.id, reason='Stop')
        db.session.commit()
        production.orders.update(db.session, self.who, linked.id, {'customer_order_id': row.id})
        db.session.commit()
        self.assertEqual(row.id, production.orders.get(db.session, self.who, linked.id).customer_order_id)
        production.orders.update(db.session, self.who, linked.id, {'customer_order_id': None})
        db.session.commit()
        self.refuse(lambda: production.orders.update(db.session, self.who, linked.id,
            {'customer_order_id': row.id}), 'Elige un pedido aceptado (comprometido).')

    def test_evidence_sets_existence_customer_cancelled_and_noop(self):
        row = self.accepted()
        log, complaint = self.evidence()
        self.update_links(row, communication_ids=[log, log], complaint_ids=[complaint])
        got = self.service.orders.get(db.session, self.who, row.id)
        self.assertEqual(([log], [complaint]), (got.communication_ids, got.complaint_ids))
        before = hashes()
        self.update_links(row, communication_ids=[log], complaint_ids=[complaint])
        self.assertEqual(before, hashes())
        for key in ('communication_ids', 'complaint_ids'):
            for value in ([999], [True], None, ['1']):
                self.refuse(lambda: self.service.orders.update(db.session, self.who, row.id,
                    {key: value}, today=TODAY))
        audit = db.session.scalar(db.select(AuditLog).where(
            AuditLog.entity_type == 'customer_orders').order_by(AuditLog.id.desc()))
        self.assertEqual([log], audit.after['communication_ids'])
        other = clientes.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        self.refuse(lambda: reclamaciones.update(db.session, self.who, complaint,
            {'cliente_id': other.id}, today=TODAY),
            'La reclamación debe pertenecer al mismo cliente que el pedido.')
        foreign = reclamaciones.create(db.session, self.who,
            dict(cliente_id=other.id, descripcion='Other claim'), today=TODAY)
        db.session.commit()
        self.refuse(lambda: self.service.orders.update(db.session, self.who, row.id,
            {'complaint_ids': [foreign.id]}, today=TODAY),
            'La reclamación debe pertenecer al mismo cliente que el pedido.')
        self.update_links(row, communication_ids=[], complaint_ids=[])
        self.service.cancel(db.session, self.who, row.id, reason='Stop')
        db.session.commit()
        self.refuse(lambda: self.service.orders.update(db.session, self.who, row.id,
            {'communication_ids': []}, today=TODAY), 'Un pedido cancelado es de solo lectura.')

    def test_cited_records_refuse_deletion_without_writes(self):
        row = self.accepted()
        log, complaint = self.evidence()
        self.update_links(row, communication_ids=[log], complaint_ids=[complaint])
        self.refuse(lambda: reclamaciones.delete(db.session, self.who, complaint),
                    'No puedes eliminar una reclamación citada por pedidos.', Conflict)
        self.refuse(lambda: communication_log.items.delete(db.session, self.who, log),
                    communication_log.IMMUTABLE)
        linked = self.production(customer_order_id=row.id)
        db.session.commit()
        self.refuse(lambda: self.service.orders.delete(db.session, self.who, row.id),
                    'No puedes eliminar un pedido citado por producción.', Conflict)
        self.assertEqual(row.id, production.orders.get(db.session, self.who, linked.id).customer_order_id)

    def test_preserve_unlinked_draft_complaint_and_production_writes(self):
        row = self.create()
        self.service.orders.update(db.session, self.who, row.id,
            {'requirements': 'Updated needs'}, today=TODAY)
        db.session.commit()
        self.assertEqual('Updated needs', self.latest(row).requirements)
        _, complaint = self.evidence()
        reclamaciones.update(db.session, self.who, complaint,
            {'descripcion': 'Clarified claim'}, today=TODAY)
        db.session.commit()
        self.assertEqual('Clarified claim', reclamaciones.get(db.session, self.who, complaint).descripcion)
        reclamaciones.delete(db.session, self.who, complaint)
        unlinked = self.production()
        db.session.commit()
        self.assertIsNone(production.orders.get(db.session, self.who, unlinked.id).customer_order_id)
        before = hashes()
        production.orders.update(db.session, self.who, unlinked.id, {'customer_order_id': None})
        db.session.commit()
        self.assertEqual(before, hashes())

    def test_citation_locks_and_refreshes_customer_order(self):
        row = self.accepted()
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                seen.append((str(state.statement.compile(dialect=postgresql.dialect())),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            try:
                self.production(customer_order_id=row.id)
            except ValidationError as exc:
                self.fail(f'Citation refused before lock: {exc}')
            self.assertTrue(any('FROM customer_orders' in sql and 'FOR SHARE' in sql
                                and refresh for sql, refresh in seen), seen)
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)
        db.session.rollback()
        from app.models import CustomerOrder
        db.session.execute(CustomerOrder.__table__.update().where(CustomerOrder.id == row.id)
            .values(cancelled=True, cancellation_reason='Concurrent')
            .execution_options(synchronize_session=False))
        self.assertFalse(row.cancelled)
        self.refuse(lambda: self.production(customer_order_id=row.id),
                    'Elige un pedido aceptado (comprometido).')

    def login_admin(self):
        self.login(RoleEnum.ADMINISTRADOR)
        from flask import g
        g.pop('_login_user', None)

    def test_evidence_form_refuses_contradictory_selection(self):
        row = self.accepted()
        log, complaint = self.evidence()
        self.login_admin()
        path = f'/pedidos/{row.id}/vinculos'
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        for key, value, label in (('communication_ids', log, 'comunicaciones'),
                                  ('complaint_ids', complaint, 'reclamaciones')):
            data = MultiDict(parser.values)
            data.setlist(key, ['', str(value)])
            before = hashes()
            refused = self.client.post(path, data=data)
            self.assertEqual(400, refused.status_code)
            self.assertIn(f'Elige «Sin {label}» o uno o varios registros, no ambas cosas.',
                          refused.get_data(as_text=True))
            self.assertEqual(before, hashes())
        valid = MultiDict(parser.values)
        valid.setlist('communication_ids', [str(log)])
        valid.setlist('complaint_ids', [str(complaint)])
        self.assertEqual(302, self.client.post(path, data=valid).status_code)
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=valid).status_code)
        self.assertEqual(before, hashes())
        self.service.cancel(db.session, self.who, row.id, reason='Stop')
        db.session.commit()
        before = hashes()
        self.assertEqual(200, self.client.post(path, data=valid).status_code)
        self.assertEqual(before, hashes())

    def test_detail_links_both_directions_and_empty_production_roundtrip(self):
        row = self.accepted()
        log, complaint = self.evidence()
        self.update_links(row, communication_ids=[log], complaint_ids=[complaint])
        linked = self.production(customer_order_id=row.id)
        db.session.commit()
        self.login_admin()
        text = self.client.get(f'/pedidos/{row.id}').get_data(as_text=True)
        self.assertIn(f'/produccion/{linked.id}', text)
        self.assertIn('Call reference', text)
        self.assertIn('Late containers', text)
        text = self.client.get(f'/produccion/{linked.id}').get_data(as_text=True)
        self.assertIn(f'/pedidos/{row.id}', text)
        self.revision(row)
        parser = BrowserForm()
        path = f'/produccion/{linked.id}/editar'
        parser.feed(self.client.get(path).get_data(as_text=True))
        self.assertEqual(str(row.id), parser.values['customer_order_id'])
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=parser.values).status_code)
        self.assertEqual(before, hashes())
        production.orders.update(db.session, self.who, linked.id, {'customer_order_id': None})
        db.session.commit()
        parser = BrowserForm()
        parser.feed(self.client.get(path).get_data(as_text=True))
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=parser.values).status_code)
        self.assertEqual(before, hashes())

    def test_additive_link_migration_preserves_rows(self):
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_link_customer_orders.py'))
        self.assertEqual(1, len(paths), 'Additive link migration required')
        spec = importlib.util.spec_from_file_location('link_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('a2c4e6f8b0d3', migration.down_revision)
        self.create()
        self.production()
        db.session.commit()
        db.session.remove()
        with db.engine.begin() as connection:
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.downgrade()
                tables = sorted(inspect(connection).get_table_names())
                def snapshot():
                    return [(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all()) for t in tables]
                before = snapshot()
                migration.upgrade()
                for name in ('customer_order_communications', 'customer_order_complaints'):
                    self.assertTrue(inspect(connection).get_pk_constraint(name)['name'])
                    for fk in inspect(connection).get_foreign_keys(name):
                        self.assertTrue(fk['name'])
                        self.assertEqual('RESTRICT', fk['options']['ondelete'])
                        self.assertEqual('RESTRICT', fk['options']['onupdate'])
                        indexed = [i['column_names'] for i in inspect(connection).get_indexes(name)]
                        indexed += [inspect(connection).get_pk_constraint(name)['constrained_columns']]
                        self.assertTrue(any(cols[:1] == fk['constrained_columns'] for cols in indexed))
                fk = next(f for f in inspect(connection).get_foreign_keys('production_orders')
                          if f['constrained_columns'] == ['customer_order_id'])
                self.assertEqual(('customer_orders', 'RESTRICT', 'RESTRICT'),
                    (fk['referred_table'], fk['options']['ondelete'], fk['options']['onupdate']))
                self.assertTrue(next(c for c in inspect(connection).get_columns('production_orders')
                    if c['name'] == 'customer_order_id')['nullable'])
                self.assertIsNone(connection.exec_driver_sql('SELECT customer_order_id FROM production_orders').scalar())
                migration.downgrade()
                self.assertEqual(before, snapshot())
                migration.upgrade()


class LinkMcp(McpDbCase):
    async def test_mcp_reads_and_writes_optional_citations_and_evidence(self):
        order = self.seed('customer_orders')
        log = self.seed('communication_log')
        complaint = self.seed('reclamaciones')
        who = mcp_actor()
        result = await self.call(who, 'qms_update', dict(module='customer_orders', id=order,
            data=dict(communication_ids=[log], complaint_ids=[complaint])))
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(([log], [complaint]), (result.structured_content['communication_ids'],
                                               result.structured_content['complaint_ids']))
        created = await self.call(who, 'qms_create', dict(module='customer_orders', data=dict(
            code='C-2', customer_id=1, kind='contract', received_date='2020-01-01',
            requirements='Containers', communication_ids=[log], complaint_ids=[complaint])))
        self.assertFalse(created.is_error, created.content)
        self.assertEqual([log], created.structured_content['communication_ids'])
        self.prepare_customer_revision()
        self.seed('procesos')
        created = await self.call(who, 'qms_create', dict(module='production_orders', data=dict(
            code='P-MCP', kind='production', title='Containers', process_id=1,
            customer_order_id=order)))
        self.assertFalse(created.is_error, created.content)
        self.assertEqual(order, created.structured_content['customer_order_id'])
        prod = self.seed('production_orders')
        result = await self.call(who, 'qms_update', dict(module='production_orders', id=prod,
            data=dict(customer_order_id=order)))
        self.assertFalse(result.is_error, result.content)
        # Filter lists to the same public records as the get calls.
        production.orders.update(db.session, self.cli(), prod, {'lot': 'MCP'})
        db.session.commit()
        for slug, record_id, expected in (('production_orders', prod, {'customer_order_id': order}),
                ('customer_orders', order, {'communication_ids': [log], 'complaint_ids': [complaint]})):
            for tool, args in (('qms_get', {'id': record_id}), ('qms_list', {'filters': {'customer_id': 1} if slug == 'customer_orders' else {'lot': 'MCP'}})):
                result = await self.call(who, tool, {'module': slug} | args)
                self.assertFalse(result.is_error, result.content)
                record = result.structured_content if tool == 'qms_get' else result.structured_content['items'][0]
                self.assertEqual(expected, {k: record[k] for k in expected})
