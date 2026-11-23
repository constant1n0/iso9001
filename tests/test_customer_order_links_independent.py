"""Independent S1/S5 probes; legacy shapes measured at 2d83f45.

The verifier's byte-hash HTML probe is intentionally not committed: it pins
whole pages and would break on any later template change.
"""
import re
import unittest
from datetime import timedelta
from pathlib import Path
from flask import g
from test_customer_orders import OrderCase, TODAY
from test_communication_independent import hashes
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.services.errors import Conflict
from app.models import AuditLog, RoleEnum
from app.services import clientes, communication, communication_log, production, process_operations, reclamaciones

EXPECTED = dict(id=1, code='P-1', kind='producción', title='Containers', process_id=1,
    customer_id=None, design_output_id=None, lot=None, owner_id=None, planned_start=None,
    planned_end=None, state='planificada', cancellation_reason=None, created_by_id=7,
    updated_by_id=7, revision_ids=[], equipment_ids=[], person_ids=[])
def legacy(row):
    return {k: v for k, v in row.items() if k not in ('customer_order_id', 'created_at', 'updated_at')}

class IndependentLinks(OrderCase):
    def setUp(self):
        super().setUp()
        process_operations.create(db.session, self.who, {'proceso': 'Workshop'})
        db.session.commit()
        self.login(RoleEnum.ADMINISTRADOR)
        g.pop('_login_user', None)
        g.csrf_token = 'probe-token'

    def prod(self, **values):
        return production.orders.create(db.session, self.who, dict(code='P-1',
            kind='production', title='Containers', process_id=1) | values)

    def test_s5_legacy_audit_shape(self):
        self.prod()
        db.session.commit()
        log = db.session.scalar(db.select(AuditLog).where(AuditLog.entity_type == 'production_orders'))
        self.assertEqual(EXPECTED | dict(created_by_id=1, updated_by_id=1), legacy(log.after))

    def test_s5_retention_and_new_citation_errors(self):
        order = self.create()
        message = 'Elige un pedido aceptado (comprometido).'
        self.refuse(lambda: self.prod(customer_order_id=order.id), message)
        self.review(self.latest(order))
        linked = self.prod(customer_id=1, customer_order_id=order.id)
        db.session.commit()
        other = clientes.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        self.refuse(lambda: production.orders.update(db.session, self.who, linked.id,
            {'customer_id': other.id}), 'El pedido debe pertenecer al mismo cliente que la orden de producción.')
        for state in ('pending', 'rejected', 'cancelled'):
            if state == 'pending': self.revision(order)
            elif state == 'rejected': self.review(self.latest(order), result='rejected')
            else:
                self.service.cancel(db.session, self.who, order.id, reason='Stopped')
                db.session.commit()
            before = hashes()
            production.orders.update(db.session, self.who, linked.id, {'customer_order_id': order.id})
            db.session.commit()
            self.assertEqual(before, hashes())
            self.refuse(lambda: self.prod(code='P-2', customer_order_id=order.id), message)

    def test_s5_evidence_rejection_audit_and_cited_complaint(self):
        order = self.create()
        plan = communication.items.create(db.session, self.who, dict(title='Call', content='Needs',
            scope='externa', channel='telefono', owner_id=1, frequency='On change', next_date=TODAY, audience_text='Customer'))
        log = communication_log.items.create(db.session, self.who, dict(plan_id=plan.id, date=TODAY,
            communicator_id=1, channel='telefono', evidence='Call ref', next_date=TODAY + timedelta(days=1)), today=TODAY)
        claim = reclamaciones.create(db.session, self.who, dict(cliente_id=1, descripcion='Late'), today=TODAY)
        db.session.commit()
        for key in ('communication_ids', 'complaint_ids'):
            for bad in (None, [True], ['1'], [0], [-1], [999]):
                self.refuse(lambda: self.service.orders.update(db.session, self.who, order.id, {key: bad}, today=TODAY))
        self.service.orders.update(db.session, self.who, order.id,
            {'communication_ids': [log.id, log.id], 'complaint_ids': [claim.id]}, today=TODAY)
        db.session.commit()
        audit = db.session.scalar(db.select(AuditLog).where(AuditLog.entity_type == 'customer_orders').order_by(AuditLog.id.desc()))
        self.assertEqual(([log.id], [claim.id]), (audit.after['communication_ids'], audit.after['complaint_ids']))
        other = clientes.create(db.session, self.who, {'nombre': 'Other'})
        db.session.commit()
        before = hashes()
        result = self.client.post(f'/reclamaciones/{claim.id}/editar', data=dict(cliente_id=str(other.id),
            fecha_recepcion=TODAY.isoformat(), descripcion='Late', canal='', no_conformidad_id=''))
        self.assertEqual(200, result.status_code)
        self.assertIn('La reclamación debe pertenecer al mismo cliente que el pedido.', result.get_data(as_text=True))
        self.assertEqual(before, hashes())
        self.refuse(lambda: reclamaciones.delete(db.session, self.who, claim.id),
            'No puedes eliminar una reclamación citada por pedidos.', Conflict)

class IndependentMcp(McpDbCase):
    async def test_s5_legacy_payload_shape(self):
        self.seed('procesos')
        for tool, args in (('create', {'data': dict(code='P-1', kind='production', title='Containers', process_id=1)}),
                           ('get', {'id': 1}), ('list', {}), ('update', {'id': 1, 'data': {}})):
            result = await self.call(mcp_actor(), 'qms_' + tool, {'module': 'production_orders'} | args)
            self.assertFalse(result.is_error, result.content)
            row = result.structured_content['items'][0] if tool == 'list' else result.structured_content
            with self.subTest(tool=tool): self.assertEqual(EXPECTED, legacy(row))

class IndependentDocs(unittest.TestCase):
    def test_s1_summary_matches_all_detail_rows(self):
        text = (Path(__file__).resolve().parents[1] / 'docs/analisis-brechas-iso-9001.md').read_text()
        rows = re.findall(r'^\| (\d+(?:\.\d+)+) [^|]+\| \**([CPND])\b', text, re.M)
        self.assertEqual(34, len(rows))
        for label, state in (('Cubierto', 'C'), ('Parcial', 'P'), ('No cubierto', 'N'), ('Solo soporte documental', 'D')):
            actual = [part for part, status in rows if status == state]
            summary = re.search(r'^\| ' + label + r' \| (\d+) \(([^)]*)\)', text, re.M)
            self.assertIsNotNone(summary, label)
            self.assertEqual(len(actual), int(summary[1]), label)
            self.assertEqual(actual, [] if summary[2] == 'ninguno' else summary[2].split(', '), label)
