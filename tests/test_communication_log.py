"""CM2 behavior through public services, MCP tools and additive migration DDL."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import CommunicationPlan, Person, RoleEnum
from app.services import communication, people
from app.services.actor import Actor
from app.services.errors import Conflict, PermissionDenied, ValidationError

DAY = date(2020, 1, 2)
ADMIN = Actor(None, 'recorder', RoleEnum.ADMINISTRADOR, 'cli')
AUDITOR = Actor(None, 'auditor', RoleEnum.AUDITOR, 'cli')
OPERATOR = Actor(None, 'reader', RoleEnum.OPERATIVO, 'cli')
PLAN = dict(title='Results', content='Explain results', scope='ambas', channel='web',
            owner_id=1, frequency='Every month', next_date=date(2099, 1, 1),
            audience_text='Staff')
LOG = dict(plan_id=1, date=DAY, communicator_id=1, channel='reunion',
           channel_detail='Room 2', audience_text='Actual attendees',
           summary='Results discussed', evidence='Minutes M-1',
           next_date=date(2020, 7, 19))


def service():
    from app.services import communication_log
    return communication_log


class CommunicationLogTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        people.create(db.session, ADMIN, {'nombre': 'Owner'})
        self.plan = communication.items.create(db.session, ADMIN, PLAN)
        db.session.commit()

    def state(self):
        return ([self.audit.snapshot(row) | communication.link_ids(row)
                 for row in communication.items.list_(db.session, ADMIN, retired=None)],
                [self.audit.snapshot(row) for row in service().items.list_(db.session, ADMIN)],
                [self.audit.snapshot(row) for row in self.audit_rows()],
                [self.audit.snapshot(row) for row in people.list_(db.session, ADMIN)])

    def record(self, actor=ADMIN, **changes):
        row = service().items.create(db.session, actor, LOG | changes, today=DAY)
        db.session.commit()
        return row

    def reject(self, data, message):
        before = self.state()
        with self.assertRaises(ValidationError) as caught:
            service().items.create(db.session, ADMIN, data, today=DAY)
        self.assertEqual(message, caught.exception.message)
        # Commit rather than rollback proves validation made no pending changes either.
        db.session.commit()
        self.assertEqual(before, self.state())

    def test_required_fields(self):
        for key in ('plan_id', 'date', 'communicator_id', 'channel', 'evidence', 'next_date'):
            self.reject({k: v for k, v in LOG.items() if k != key},
                        f'Faltan campos obligatorios: {key}.')
        self.reject(LOG | {'plan_id': 999},
                    'El campo «plan_id» no corresponde a ninguna comunicación planificada.')
        self.reject(LOG | {'channel': 'email'}, 'El valor del campo «channel» no es válido.')
        self.reject(LOG | {'unknown': True}, 'Campos no permitidos: unknown.')

    def test_date_required_and_not_future(self):
        self.reject(LOG | {'date': None}, 'El campo «date» es obligatorio y debe ser una fecha.')
        self.reject(LOG | {'date': DAY + timedelta(days=1)},
                    'La fecha de comunicación no puede ser futura.')

    def test_communicator_active_when_new_even_if_plan_owner(self):
        self.reject(LOG | {'communicator_id': None},
                    'El campo «communicator_id» debe ser un número entero.')
        self.reject(LOG | {'communicator_id': 999},
                    'El campo «communicator_id» no corresponde a ninguna persona.')
        people.update(db.session, ADMIN, 1, {'activo': False})
        db.session.commit()
        self.reject(LOG, 'La persona del campo «communicator_id» está desactivada; elige una persona activa.')
        active = people.create(db.session, ADMIN, {'nombre': 'Actual communicator'})
        db.session.commit()
        self.assertEqual(active.id, self.record(communicator_id=active.id).communicator_id)

    def test_evidence_required(self):
        for blank in ('', ' \n\t'):
            self.reject(LOG | {'evidence': blank}, 'El campo «evidence» es obligatorio.')
        self.reject(LOG | {'evidence': None}, 'El campo «evidence» debe ser texto.')

    def test_new_next_date_required_and_strictly_after_occurrence(self):
        self.reject(LOG | {'next_date': None},
                    'El campo «next_date» es obligatorio y debe ser una fecha.')
        for following in (DAY, DAY - timedelta(days=1)):
            self.reject(LOG | {'next_date': following},
                        'Indica una nueva próxima fecha posterior a la comunicación.')

    def test_atomic_plan_update_and_creation_audit_no_recomputation(self):
        before = len(self.audit_rows())
        row = self.record(actor=AUDITOR)
        db.session.expire_all()
        self.assertEqual(LOG['next_date'], communication.items.get(db.session, ADMIN, 1).next_date)
        self.assertEqual((DAY, 1, 'reunion', 'Room 2', 'Actual attendees',
                          'Results discussed', 'Minutes M-1', LOG['next_date']),
                         (row.date, row.communicator_id, row.channel.name, row.channel_detail,
                          row.audience_text, row.summary, row.evidence, row.next_date))
        audits = self.audit_rows()[before:]
        self.assertEqual([('communication_plan', 'update'), ('communication_log', 'create')],
                         [(r.entity_type, r.action) for r in audits])
        self.assertEqual('2099-01-01', audits[0].before['next_date'])
        self.assertEqual('2020-07-19', audits[0].after['next_date'])
        self.assertEqual('Minutes M-1', audits[1].after['evidence'])
        self.assertEqual({'auditor'}, {r.actor_label for r in audits})
        state = self.state()
        service().items.list_page(db.session, OPERATOR)
        db.session.commit()
        self.assertEqual(state, self.state())
        service().items.create(db.session, ADMIN, LOG | {'next_date': date(2021, 3, 5)}, today=DAY)
        db.session.rollback()
        self.assertEqual(state, self.state())

    def test_backdated_rejected_same_day_allowed_latest_id_first(self):
        first = self.record()
        self.reject(LOG | {'date': DAY - timedelta(days=1)},
                    'La comunicación no puede ser anterior a la última registrada.')
        second = self.record(evidence='Minutes M-2')
        self.assertEqual([second, first], service().items.list_(db.session, ADMIN))
        self.assertIs(second, service().latest(db.session, 1))

    def test_retired_rejects_recording_and_retains_readable_history(self):
        row = self.record()
        communication.items.update(db.session, ADMIN, 1, {'retired': True})
        db.session.commit()
        self.reject(LOG, 'No se pueden registrar comunicaciones de una entrada retirada.')
        self.assertEqual([row], service().items.list_(db.session, OPERATOR, plan_id=1))

    def test_immutable_update_and_delete_even_noop(self):
        row = self.record()
        before = self.state()
        for call in (lambda: service().items.update(db.session, ADMIN, row.id, {}, today=DAY),
                     lambda: service().items.delete(db.session, ADMIN, row.id)):
            with self.assertRaises(ValidationError) as caught:
                call()
            self.assertEqual('Las comunicaciones registradas no se modifican ni se eliminan; registra una nueva.',
                             caught.exception.message)
            db.session.commit()
            self.assertEqual(before, self.state())

    def test_plan_with_occurrences_cannot_be_deleted(self):
        self.record()
        for retired in (False, True):
            communication.items.update(db.session, ADMIN, 1, {'retired': retired})
            db.session.commit()
            before = self.state()
            with self.assertRaises(Conflict) as caught:
                communication.items.delete(db.session, ADMIN, 1)
            self.assertEqual(communication.STILL_REFERENCED, caught.exception.message)
            db.session.commit()
            self.assertEqual(before, self.state())

    def test_people_protected_after_owner_reassigned_and_plan_retired(self):
        actual = people.create(db.session, ADMIN, {'nombre': 'Actual'})
        db.session.commit()
        self.record(communicator_id=actual.id)
        communication.items.update(db.session, ADMIN, 1, {'retired': True})
        people.update(db.session, ADMIN, actual.id, {'activo': False})
        db.session.commit()
        before = self.state()
        for person_id in (1, actual.id):
            with self.assertRaises(Conflict) as caught:
                people.delete(db.session, ADMIN, person_id)
            self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
            db.session.commit()
            self.assertEqual(before, self.state())

    def test_role_matrix(self):
        before = self.state()
        with self.assertRaises(PermissionDenied):
            self.record(actor=OPERATOR)
        db.session.commit()
        self.assertEqual(before, self.state())
        row = self.record(actor=AUDITOR)
        for role in RoleEnum:
            who = Actor(None, 'reader', role, 'cli')
            self.assertIs(row, service().items.get(db.session, who, row.id))
            self.assertEqual(([row], 1), service().items.list_page(db.session, who))

    def test_filters_combine_and_date_bounds_inclusive(self):
        first = self.record(date=DAY - timedelta(days=1))
        second = self.record()
        other = communication.items.create(db.session, ADMIN, PLAN | {'title': 'Other'})
        db.session.commit()
        third = self.record(plan_id=other.id)
        for filters, expected in (({'plan_id': 1}, [second, first]),
                                  ({'date_from': DAY}, [third, second]),
                                  ({'date_to': DAY - timedelta(days=1)}, [first]),
                                  ({'plan_id': 1, 'date_from': DAY, 'date_to': DAY,
                                    'communicator_id': 1}, [second]),
                                  ({'communicator_id': 999}, [])):
            self.assertEqual(expected, service().items.list_(db.session, ADMIN, **filters))
            self.assertEqual((expected, len(expected)),
                             service().items.list_page(db.session, ADMIN, **filters))


class CommunicationLogMcpTestCase(McpDbCase):
    async def test_create_read_list_immutable_permissions_and_rejections_atomic(self):
        from test_communication_independent import hashes
        self.seed('communication')
        data = LOG | {'date': DAY.isoformat(), 'next_date': '2020-07-19'}
        for who in (mcp_actor(RoleEnum.OPERATIVO), mcp_actor(scopes=('read',))):
            before = hashes()
            rejected = await self.call(who, 'qms_create', {'module': 'communication_log', 'data': data})
            self.assertTrue(rejected.is_error)
            self.assertEqual('Error executing tool qms_create: No tienes permiso para realizar esta acción.',
                             rejected.content[0].text)
            self.assertEqual(before, hashes())
        for key in ('next_date', 'evidence'):
            before = hashes()
            rejected = await self.call(mcp_actor(), 'qms_create', {
                'module': 'communication_log', 'data': {k: v for k, v in data.items() if k != key}})
            self.assertTrue(rejected.is_error)
            self.assertEqual(f'Error executing tool qms_create: Faltan campos obligatorios: {key}.',
                             rejected.content[0].text)
            self.assertEqual(before, hashes())
        created = await self.call(mcp_actor(RoleEnum.AUDITOR), 'qms_create',
                                  {'module': 'communication_log', 'data': data})
        self.assertFalse(created.is_error, created.content)
        row = created.structured_content
        self.assertEqual(('2020-01-02', 'reunión', 'Minutes M-1', '2020-07-19'),
                         (row['date'], row['channel'], row['evidence'], row['next_date']))
        plan = await self.call(mcp_actor(), 'qms_get', {'module': 'communication', 'id': 1})
        self.assertEqual(('2020-07-19', '2020-01-02'),
                         (plan.structured_content['next_date'], plan.structured_content['latest_date']))
        for role in RoleEnum:
            got = await self.call(mcp_actor(role), 'qms_get', {'module': 'communication_log', 'id': row['id']})
            self.assertEqual(row['evidence'], got.structured_content['evidence'])
            listed = await self.call(mcp_actor(role), 'qms_list', {'module': 'communication_log',
                'filters': {'plan_id': 1, 'date_from': '2020-01-02', 'date_to': '2020-01-02', 'communicator_id': 1}})
            self.assertEqual(1, listed.structured_content['total'])
        before = hashes()
        refused = await self.call(mcp_actor(), 'qms_update', {
            'module': 'communication_log', 'id': row['id'], 'data': {}})
        self.assertTrue(refused.is_error)
        self.assertEqual('Error executing tool qms_update: Las comunicaciones registradas no se modifican ni se eliminan; registra una nueva.',
                         refused.content[0].text)
        self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {t.name for t in await self.list_tools()})


class CommunicationLogMigrationTestCase(unittest.TestCase):
    def test_additive_upgrade_downgrade_preserves_plan_and_matches_model(self):
        paths = list((Path(__file__).resolve().parents[1] / 'migrations/versions').glob('*_add_communication_log.py'))
        self.assertEqual(1, len(paths))
        from app.models import CommunicationLog
        spec = importlib.util.spec_from_file_location('communication_log_migration', paths[0])
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('a0c2e4f6b8d0', migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
                table = CommunicationLog.__table__
                db.metadata.create_all(connection, tables=[t for t in db.metadata.sorted_tables if t is not table])
                connection.execute(Person.__table__.insert().values(nombre='Existing owner'))
                connection.execute(CommunicationPlan.__table__.insert().values(**PLAN))
                before = connection.execute(select(CommunicationPlan.__table__)).all()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    info = inspect(connection)
                    self.assertEqual({c.name: (str(c.type), c.nullable) for c in table.columns},
                                     {c['name']: (str(c['type']), c['nullable']) for c in info.get_columns(table.name)})
                    self.assertEqual({i.name for i in table.indexes},
                                     {i['name'] for i in info.get_indexes(table.name)})
                    self.assertEqual({c.name for c in table.constraints if c.name},
                                     {c['name'] for c in info.get_check_constraints(table.name)} |
                                     {f['name'] for f in info.get_foreign_keys(table.name)} |
                                     {info.get_pk_constraint(table.name)['name']} - {None})
                    for fk in info.get_foreign_keys(table.name):
                        self.assertTrue(fk['name'])
                        if fk['referred_table'] != 'users':
                            self.assertEqual('RESTRICT', fk['options']['ondelete'])
                            self.assertEqual('RESTRICT', fk['options']['onupdate'])
                    for bad in ({'date': None}, {'plan_id': None}, {'communicator_id': None},
                                {'evidence': ''}, {'evidence': None}, {'channel': 'email'},
                                {'next_date': DAY}, {'plan_id': 999}, {'communicator_id': 999}):
                        with self.subTest(bad=bad), self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(table.insert().values(**(LOG | bad)))
                    connection.execute(table.insert().values(**LOG))
                    for target, pk in ((CommunicationPlan.__table__, CommunicationPlan.id),
                                       (Person.__table__, Person.id)):
                        with self.assertRaises(IntegrityError):
                            with connection.begin_nested():
                                connection.execute(target.delete().where(pk == 1))
                    self.assertEqual(before, connection.execute(select(CommunicationPlan.__table__)).all())
                    migration.downgrade()
                    self.assertNotIn(table.name, inspect(connection).get_table_names())
                    self.assertEqual(before, connection.execute(select(CommunicationPlan.__table__)).all())
                    migration.upgrade()
        finally:
            engine.dispose()
