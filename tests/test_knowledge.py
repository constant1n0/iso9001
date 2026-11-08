"""Knowledge register service rules and MCP contract (KN1, S1–S3 and S5)."""
from datetime import date, timedelta

from test_nonconformity_service import ServiceBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import people
from app.services.actor import Actor
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationError

TODAY = date(2026, 11, 2)
ADMIN = Actor(None, 'test', RoleEnum.ADMINISTRADOR, 'cli')
AUDITOR = Actor(None, 'test', RoleEnum.AUDITOR, 'cli')
OPERATOR = Actor(None, 'test', RoleEnum.OPERATIVO, 'cli')


def register():
    from app.services.knowledge import items
    return items


class KnowledgeTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.owner = people.create(db.session, ADMIN, {'nombre': 'Ana'})
        db.session.commit()
        self.data = dict(kind='conocimiento', title='Set-up procedure',
                         summary='Keep the reference sample before adjusting the tool.',
                         source='Workshop', owner_id=self.owner.id)

    def create(self, **changes):
        item = register().create(db.session, ADMIN, self.data | changes)
        db.session.commit()
        return item

    def snapshot(self):
        return ([self.audit.snapshot(row) for row in register().list_(
            db.session, ADMIN, retired=None)],
                [self.audit.snapshot(row) for row in self.audit_rows()])

    def reject(self, data, message, item=None):
        before = self.snapshot()
        with self.assertRaises(ValidationError) as caught:
            if item is None:
                register().create(db.session, ADMIN, data)
            else:
                register().update(db.session, ADMIN, item.id, data)
        self.assertEqual(message, caught.exception.message)
        db.session.commit()  # rejection must not need rollback to avoid partial writes
        self.assertEqual(before, self.snapshot())

    def test_type_validation_and_both_types(self):
        for value in ('other', 'Conocimiento', '', None, True):
            self.reject(self.data | {'kind': value},
                        'El valor del campo «kind» no es válido.')
        for value in ('conocimiento', 'leccion'):
            self.assertEqual(value, self.create(kind=value).kind.name)
        item = register().list_(db.session, ADMIN)[0]
        self.reject({'kind': 'other'}, 'El valor del campo «kind» no es válido.', item)
        self.assertEqual(1, len(register().list_(db.session, ADMIN, kind='leccion')))

    def test_required_title_summary_and_owner_on_create_and_edit(self):
        for key in ('title', 'summary', 'owner_id', 'kind'):
            missing = {k: v for k, v in self.data.items() if k != key}
            self.reject(missing, f'Faltan campos obligatorios: {key}.')
        item = self.create()
        for key in ('title', 'summary'):
            for value in ('', ' \n\t'):
                message = f'El campo «{key}» es obligatorio.'
                self.reject(self.data | {key: value}, message)
                self.reject({key: value}, message, item)
        self.reject(self.data | {'owner_id': None},
                    'El campo «owner_id» debe ser un número entero.')
        self.reject({'owner_id': None},
                    'El campo «owner_id» debe ser un número entero.', item)

    def test_inactive_or_unknown_owner_is_rejected_but_existing_owner_stays(self):
        item = self.create()
        inactive = people.create(db.session, ADMIN, {'nombre': 'Inactive', 'activo': False})
        people.update(db.session, ADMIN, self.owner.id, {'activo': False})
        db.session.commit()
        message = 'La persona del campo «owner_id» está desactivada; elige una persona activa.'
        self.reject(self.data, message)
        self.reject({'title': 'Changed', 'owner_id': inactive.id}, message, item)
        self.reject({'owner_id': 999},
                    'El campo «owner_id» no corresponde a ninguna persona.', item)
        before = len(self.audit_rows())
        register().update(db.session, ADMIN, item.id,
                          {'title': 'Changed', 'owner_id': self.owner.id})
        db.session.commit()
        self.assertEqual('Changed', register().get(db.session, ADMIN, item.id).title)
        self.assertEqual(before + 1, len(self.audit_rows()))

    def test_overdue_boundary_no_date_and_retired_never_overdue(self):
        from app.services.knowledge import is_overdue
        for due, retired, expected in (
            (None, False, False), (TODAY, False, False),
            (TODAY + timedelta(days=1), False, False),
            (TODAY - timedelta(days=1), False, True),
            (TODAY - timedelta(days=1), True, False),
        ):
            item = self.create(next_review_date=due, retired=retired)
            before = self.snapshot()
            self.assertIs(expected, is_overdue(item, today=TODAY))
            self.assertEqual(before, self.snapshot())
        listed = register().list_(db.session, ADMIN, overdue=True, today=TODAY, retired=None)
        self.assertEqual(1, len(listed))
        self.assertFalse(listed[0].retired)
        self.assertEqual(4, len(register().list_(
            db.session, ADMIN, overdue=False, today=TODAY, retired=None)))

    def test_retired_hidden_by_default_but_readable_and_filterable(self):
        active = self.create()
        retired = self.create(title='Retired', retired=True)
        self.assertEqual([active], register().list_(db.session, ADMIN))
        self.assertEqual(([retired], 1), register().list_page(db.session, ADMIN, retired=True))
        self.assertEqual(2, register().list_page(db.session, ADMIN, retired=None)[1])
        self.assertEqual(([], 1), register().list_page(db.session, ADMIN, page=2, per_page=1))
        self.assertIs(retired, register().get(db.session, OPERATOR, retired.id))

    def test_permissions_and_audited_edit_retirement_noop_and_delete(self):
        item = register().create(db.session, AUDITOR, self.data)
        db.session.commit()
        for role in RoleEnum:
            who = Actor(None, 'test', role, 'cli')
            self.assertIs(item, register().get(db.session, who, item.id))
        before = self.snapshot()
        for call in (
            lambda: register().create(db.session, OPERATOR, self.data),
            lambda: register().update(db.session, OPERATOR, item.id, {'retired': True}),
            lambda: register().delete(db.session, OPERATOR, item.id),
            lambda: register().delete(db.session, AUDITOR, item.id),
        ):
            with self.assertRaises(PermissionDenied):
                call()
            db.session.commit()
            self.assertEqual(before, self.snapshot())
        register().update(db.session, AUDITOR, item.id, {'summary': 'New evidence', 'retired': True})
        db.session.commit()
        last = self.audit_rows()[-1]
        self.assertEqual(('update', 'New evidence', True),
                         (last.action, last.after['summary'], last.after['retired']))
        before = self.snapshot()
        register().update(db.session, AUDITOR, item.id, {'summary': 'New evidence', 'retired': True})
        db.session.commit()
        self.assertEqual(before, self.snapshot())
        register().delete(db.session, ADMIN, item.id)
        db.session.commit()
        with self.assertRaises(NotFound):
            register().get(db.session, ADMIN, item.id)
        self.assertEqual('delete', self.audit_rows()[-1].action)
        self.assertEqual('New evidence', self.audit_rows()[-1].before['summary'])

    def test_person_owner_cannot_be_deleted_and_uncited_people_still_can(self):
        item = self.create()
        before = self.snapshot()
        with self.assertRaises(Conflict) as caught:
            people.delete(db.session, ADMIN, self.owner.id)
        self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
        db.session.commit()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.owner.id, register().get(db.session, ADMIN, item.id).owner_id)
        uncited = people.create(db.session, ADMIN, {'nombre': 'Uncited'})
        db.session.commit()
        people.delete(db.session, ADMIN, uncited.id)
        db.session.commit()
        self.assertEqual([self.owner], people.list_(db.session, ADMIN))


class KnowledgeMcpTestCase(McpDbCase):
    async def test_create_update_read_filters_permissions_and_no_delete(self):
        owner_id = self.seed('personas')
        data = dict(kind='leccion', title='Lesson', summary='Keep evidence',
                    source='Team discussion', owner_id=owner_id, next_review_date='2020-01-01')
        who = mcp_actor(RoleEnum.AUDITOR)
        created = await self.call(who, 'qms_create', {'module': 'knowledge', 'data': data})
        self.assertFalse(created.is_error, created.content)
        row = created.structured_content
        self.assertEqual(('Lección aprendida', True), (row['kind'], row['overdue']))
        updated = await self.call(who, 'qms_update', {
            'module': 'knowledge', 'id': row['id'], 'data': {'summary': 'Revised', 'retired': True}})
        self.assertFalse(updated.is_error, updated.content)
        self.assertEqual(('Revised', False),
                         (updated.structured_content['summary'], updated.structured_content['overdue']))
        for filters, total in (({}, 0), ({'retired': True}, 1),
                               ({'kind': 'leccion', 'retired': True}, 1),
                               ({'retired': True, 'overdue': True}, 0)):
            result = await self.call(who, 'qms_list', {'module': 'knowledge', 'filters': filters})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(total, result.structured_content['total'])
        from app.models import AuditLog
        before = [r.id for r in AuditLog.query.order_by(AuditLog.id)]
        for caller in (mcp_actor(RoleEnum.OPERATIVO), mcp_actor(scopes=('read',))):
            rejected = await self.call(caller, 'qms_create', {'module': 'knowledge', 'data': data})
            self.assertTrue(rejected.is_error)
            self.assertEqual('Error executing tool qms_create: No tienes permiso para realizar esta acción.',
                             rejected.content[0].text)
        for bad, message in (({'kind': 'other'}, 'El valor del campo «kind» no es válido.'),
                             ({'summary': ' '}, 'El campo «summary» es obligatorio.')):
            rejected = await self.call(who, 'qms_update', {
                'module': 'knowledge', 'id': row['id'], 'data': bad})
            self.assertTrue(rejected.is_error)
            self.assertEqual('Error executing tool qms_update: ' + message, rejected.content[0].text)
        self.assertEqual(before, [r.id for r in AuditLog.query.order_by(AuditLog.id)])
        got = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_get',
                              {'module': 'knowledge', 'id': row['id']})
        self.assertEqual('Revised', got.structured_content['summary'])
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})
        with self.assertRaises(PermissionDenied):
            register().delete(db.session, mcp_actor(), row['id'])
