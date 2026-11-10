"""Communication plan rules through the service and public MCP tools (CM1)."""
from datetime import date, timedelta
from unittest.mock import patch

from test_nonconformity_service import ServiceBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import clientes, people, providers, stakeholders
from app.services.actor import Actor
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationError

TODAY = date(2026, 11, 2)
ADMIN = Actor(None, 'test', RoleEnum.ADMINISTRADOR, 'cli')
AUDITOR = Actor(None, 'test', RoleEnum.AUDITOR, 'cli')
OPERATOR = Actor(None, 'test', RoleEnum.OPERATIVO, 'cli')


def service():
    from app.services import communication
    return communication


class CommunicationTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.owner = people.create(db.session, ADMIN, {'nombre': 'Owner'})
        db.session.commit()
        self.data = dict(title='Quality results', content='Explain performance', scope='ambas',
                         channel='web', owner_id=self.owner.id, frequency='Every month',
                         next_date=TODAY, audience_text='All staff')

    def create(self, **changes):
        item = service().items.create(db.session, ADMIN, self.data | changes)
        db.session.commit()
        return item

    def snapshot(self):
        return ([self.audit.snapshot(row) | service().link_ids(row)
                 for row in service().items.list_(db.session, ADMIN, retired=None)],
                [self.audit.snapshot(row) for row in self.audit_rows()])

    def reject(self, data, message, item=None):
        before = self.snapshot()
        with self.assertRaises(ValidationError) as caught:
            if item is None:
                service().items.create(db.session, ADMIN, data)
            else:
                service().items.update(db.session, ADMIN, item.id, data)
        self.assertEqual(message, caught.exception.message)
        db.session.commit()
        self.assertEqual(before, self.snapshot())

    def test_required_fields_and_exact_scope_channel_enums(self):
        for key in ('title', 'content', 'scope', 'channel', 'owner_id', 'frequency', 'next_date'):
            self.reject({k: v for k, v in self.data.items() if k != key},
                        f'Faltan campos obligatorios: {key}.')
        item = self.create()
        for key in ('title', 'content', 'frequency'):
            for value in ('', ' \n\t'):
                self.reject({key: value}, f'El campo «{key}» es obligatorio.', item)
        for key in ('scope', 'channel'):
            for value in ('other', '', None, True):
                self.reject(self.data | {key: value}, f'El valor del campo «{key}» no es válido.')
                self.reject({key: value}, f'El valor del campo «{key}» no es válido.', item)
        for scope in ('interna', 'externa', 'ambas'):
            self.assertEqual(scope, self.create(scope=scope).scope.name)
        for channel in ('correo', 'reunion', 'telefono', 'tablon_intranet', 'web', 'otro'):
            self.assertEqual(channel, self.create(channel=channel).channel.name)
        self.reject({'owner_id': None}, 'El campo «owner_id» debe ser un número entero.', item)
        self.reject({'next_date': None}, 'El campo «next_date» es obligatorio y debe ser una fecha.', item)

    def test_at_least_one_audience_and_rejected_writes_leave_nothing_changed(self):
        message = 'Indica al menos un destinatario o una descripción de destinatarios.'
        self.reject(self.data | {'audience_text': '  '}, message)
        item = self.create()
        self.reject({'title': 'Must not change', 'audience_text': None}, message, item)
        self.reject({'title': 'Must not change', 'person_ids': [999]},
                    'El campo «person_ids» contiene identificadores desconocidos: 999.', item)
        self.reject({'title': 'Must not change', 'person_ids': [True]},
                    'El campo «person_ids» debe ser una lista de identificadores.', item)
        self.reject({'title': 'Must not change', 'unknown': True},
                    'Campos no permitidos: unknown.', item)

    def test_new_inactive_people_rejected_existing_owner_and_audience_retained(self):
        audience = people.create(db.session, ADMIN, {'nombre': 'Audience'})
        item = self.create(person_ids=[audience.id])
        for person in (self.owner, audience):
            people.update(db.session, ADMIN, person.id, {'activo': False})
        db.session.commit()
        active = people.create(db.session, ADMIN, {'nombre': 'Active'})
        db.session.commit()
        for key, value in (('owner_id', self.owner.id), ('person_ids', [audience.id])):
            self.reject(self.data | {'owner_id': active.id} | {key: value},
                        f'La persona del campo «{key}» está desactivada; elige una persona activa.')
        self.reject({'owner_id': audience.id},
                    'La persona del campo «owner_id» está desactivada; elige una persona activa.', item)
        before = self.snapshot()
        service().items.update(db.session, ADMIN, item.id,
                               {'owner_id': self.owner.id, 'person_ids': [audience.id]})
        db.session.commit()
        self.assertEqual(before, self.snapshot())

    def test_each_audience_set_replace_clear_persisted_and_parent_audited(self):
        targets = (
            ('stakeholder_ids', stakeholders, {'nombre': 'Party'}, 'id_interesado'),
            ('customer_ids', clientes, {'nombre': 'Customer'}, 'id'),
            ('provider_ids', providers.providers,
             {'nombre': 'Provider', 'tipo_suministro': 'producto'}, 'id'),
            ('person_ids', people, {'nombre': 'Person'}, 'id'),
        )
        for key, module, data, pk in targets:
            with self.subTest(key=key):
                first = module.create(db.session, ADMIN, data)
                second = module.create(db.session, ADMIN, data | {'nombre': data['nombre'] + ' 2'})
                db.session.commit()
                ids = [getattr(first, pk), getattr(second, pk)]
                item = self.create(audience_text=None, **{key: [ids[0], ids[0]]})
                self.assertEqual([ids[0]], self.audit_rows()[-1].after[key])
                before = len(self.audit_rows())
                service().items.update(db.session, ADMIN, item.id, {key: list(reversed(ids))})
                db.session.commit()
                db.session.expire_all()
                found = service().items.get(db.session, ADMIN, item.id)
                self.assertEqual(ids, service().link_ids(found)[key])
                self.assertEqual(before + 1, len(self.audit_rows()))
                self.assertEqual(([ids[0]], ids),
                                 (self.audit_rows()[-1].before[key], self.audit_rows()[-1].after[key]))
                before = self.snapshot()
                service().items.update(db.session, ADMIN, item.id, {key: ids + ids})
                db.session.commit()
                self.assertEqual(before, self.snapshot())
                service().items.update(db.session, ADMIN, item.id,
                                       {key: [], 'audience_text': 'Other recipients'})
                db.session.commit()
                self.assertEqual([], service().link_ids(found)[key])
                self.assertEqual((ids, []),
                                 (self.audit_rows()[-1].before[key], self.audit_rows()[-1].after[key]))

    def test_blocked_provider_new_link_refused_but_existing_retained(self):
        provider = providers.providers.create(db.session, ADMIN,
                    {'nombre': 'Supplier', 'tipo_suministro': 'producto'})
        item = self.create(provider_ids=[provider.id])
        providers.block(db.session, ADMIN, provider.id, 'No longer current', today=TODAY)
        db.session.commit()
        self.reject(self.data | {'provider_ids': [provider.id]},
                    'El proveedor del campo «provider_ids» está bloqueado; elige otro proveedor.')
        before = self.snapshot()
        service().items.update(db.session, ADMIN, item.id, {'provider_ids': [provider.id]})
        db.session.commit()
        self.assertEqual(before, self.snapshot())

    def test_overdue_boundary_filters_and_retired_exclusion(self):
        for offset, retired, expected in ((-1, False, True), (0, False, False),
                                         (1, False, False), (-1, True, False)):
            item = self.create(next_date=TODAY + timedelta(days=offset), retired=retired)
            self.assertIs(expected, service().is_overdue(item, today=TODAY))
        self.assertEqual(1, len(service().items.list_(db.session, ADMIN, overdue=True,
                                                     today=TODAY, retired=None)))
        self.assertEqual(3, len(service().items.list_(db.session, ADMIN, overdue=False,
                                                     today=TODAY, retired=None)))
        self.assertEqual(4, len(service().items.list_(db.session, ADMIN,
                                                     scope='ambas', channel='web', retired=None)))
        self.assertEqual([], service().items.list_(db.session, ADMIN, scope='interna'))

    def test_retired_hidden_by_default_still_readable(self):
        active = self.create()
        retired = self.create(retired=True)
        self.assertEqual([active], service().items.list_(db.session, OPERATOR))
        self.assertEqual(([retired], 1), service().items.list_page(db.session, OPERATOR, retired=True))
        self.assertEqual(2, service().items.list_page(db.session, OPERATOR, retired=None)[1])
        self.assertIs(retired, service().items.get(db.session, OPERATOR, retired.id))

    def test_permissions_noop_retirement_admin_delete_and_citation_hook(self):
        item = service().items.create(db.session, AUDITOR, self.data)
        db.session.commit()
        for role in RoleEnum:
            who = Actor(None, 'reader', role, 'cli')
            self.assertIs(item, service().items.get(db.session, who, item.id))
        before = self.snapshot()
        for call in (lambda: service().items.create(db.session, OPERATOR, self.data),
                     lambda: service().items.update(db.session, OPERATOR, item.id, {'retired': True}),
                     lambda: service().items.delete(db.session, OPERATOR, item.id),
                     lambda: service().items.delete(db.session, AUDITOR, item.id),
                     lambda: service().items.delete(db.session, mcp_actor(), item.id)):
            with self.assertRaises(PermissionDenied):
                call()
            db.session.commit()
            self.assertEqual(before, self.snapshot())
        service().items.update(db.session, AUDITOR, item.id, {'retired': True})
        db.session.commit()
        before = self.snapshot()
        service().items.update(db.session, AUDITOR, item.id, {'retired': True})
        db.session.commit()
        self.assertEqual(before, self.snapshot())
        # Future CM2 registers its incoming FK here; exercise the public deletion seam.
        from app.models import Person
        with patch.object(service(), 'REFERENCES', (Person.id,)):
            with self.assertRaises(Conflict) as caught:
                service().items.delete(db.session, ADMIN, item.id)
            self.assertEqual(service().STILL_REFERENCED, caught.exception.message)
        db.session.commit()
        self.assertEqual(before, self.snapshot())
        service().items.delete(db.session, ADMIN, item.id)
        db.session.commit()
        with self.assertRaises(NotFound):
            service().items.get(db.session, ADMIN, item.id)
        self.assertEqual('delete', self.audit_rows()[-1].action)

    def test_people_owner_and_audience_protected_uncited_deletion_preserved(self):
        audience = people.create(db.session, ADMIN, {'nombre': 'Audience'})
        self.create(person_ids=[audience.id])
        before = self.snapshot()
        for person in (self.owner, audience):
            with self.assertRaises(Conflict) as caught:
                people.delete(db.session, ADMIN, person.id)
            self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
            db.session.commit()
            self.assertEqual(before, self.snapshot())
        uncited = people.create(db.session, ADMIN, {'nombre': 'Uncited'})
        db.session.commit()
        people.delete(db.session, ADMIN, uncited.id)
        db.session.commit()
        self.assertEqual([audience, self.owner], people.list_(db.session, ADMIN))


class CommunicationMcpTestCase(McpDbCase):
    async def test_mcp_read_write_filters_permissions_no_delete_and_noop(self):
        owner_id = self.seed('personas')
        data = dict(title='Quality results', content='Explain results', scope='externa',
                    channel='reunion', channel_detail='Room 2', frequency='Monthly',
                    next_date='2000-01-01', owner_id=owner_id, person_ids=[owner_id])
        created = await self.call(mcp_actor(RoleEnum.AUDITOR), 'qms_create',
                                  {'module': 'communication', 'data': data})
        self.assertFalse(created.is_error, created.content)
        row = created.structured_content
        self.assertEqual(('Externa', 'reunión', [owner_id], True),
                         (row['scope'], row['channel'], row['person_ids'], row['overdue']))
        from app.models import AuditLog
        before = [r.id for r in AuditLog.query.order_by(AuditLog.id)]
        noop = await self.call(mcp_actor(), 'qms_update',
                               {'module': 'communication', 'id': row['id'], 'data': data})
        self.assertFalse(noop.is_error, noop.content)
        for actor in (mcp_actor(RoleEnum.OPERATIVO), mcp_actor(scopes=('read',))):
            rejected = await self.call(actor, 'qms_update', {
                'module': 'communication', 'id': row['id'], 'data': {'title': 'Forbidden'}})
            self.assertTrue(rejected.is_error)
            self.assertEqual('Error executing tool qms_update: No tienes permiso para realizar esta acción.',
                             rejected.content[0].text)
        rejected = await self.call(mcp_actor(), 'qms_update', {
            'module': 'communication', 'id': row['id'], 'data': {'person_ids': [], 'title': 'Bad'}})
        self.assertTrue(rejected.is_error)
        self.assertEqual('Error executing tool qms_update: Indica al menos un destinatario o una descripción de destinatarios.',
                         rejected.content[0].text)
        self.assertEqual(before, [r.id for r in AuditLog.query.order_by(AuditLog.id)])
        for filters, total in (({}, 1), ({'scope': 'interna'}, 0),
                               ({'channel': 'reunion', 'overdue': True}, 1)):
            listed = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_list',
                                     {'module': 'communication', 'filters': filters})
            self.assertFalse(listed.is_error, listed.content)
            self.assertEqual(total, listed.structured_content['total'])
        retired = await self.call(mcp_actor(RoleEnum.AUDITOR), 'qms_update', {
            'module': 'communication', 'id': row['id'], 'data': {'retired': True}})
        self.assertFalse(retired.is_error, retired.content)
        self.assertFalse(retired.structured_content['overdue'])
        listed = await self.call(mcp_actor(), 'qms_list', {'module': 'communication'})
        self.assertEqual(0, listed.structured_content['total'])
        got = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_get',
                              {'module': 'communication', 'id': row['id']})
        self.assertTrue(got.structured_content['retired'])
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})
