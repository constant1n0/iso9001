"""Knowledge citations through services, HTML and MCP (KN2, S3–S5)."""
from datetime import date
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_nonconformity_service import ServiceBase
from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app import models
from app.services import audit, knowledge, people
from app.services.actor import Actor
from app.services.errors import Conflict, ValidationError

ADMIN = Actor(None, 'links', models.RoleEnum.ADMINISTRADOR, 'cli')
LISTS = {'process_ids': 'procesos', 'document_ids': 'documentos',
         'expert_ids': 'personas', 'training_ids': 'capacitaciones',
         'requirement_ids': 'competencias_requeridas'}
ORIGINS = ('no_conformidad_id', 'hallazgo_id', 'mejora_id', 'accion_revision_id')
VALID = dict(kind='leccion', title='Reference sample', summary='Keep the sample.', owner_id=1)
MIGRATION = Path('migrations/versions/f9b1d3e5a7c9_add_knowledge_links.py')


def seed_targets(session):
    """Core fixtures isolate citation behavior from unrelated workflows."""
    fixtures = (
        (models.Person, dict(nombre='Owner')),
        (models.Person, dict(nombre='Expert')),
        (models.RolResponsabilidad, dict(rol='Welder')),
        (models.ProcesoOperacion, dict(proceso='Welding')),
        (models.ProcesoOperacion, dict(proceso='Inspection')),
        (models.Document, dict(code='WI-1', title='Instruction',
                               category=models.DocumentCategory.OTRO)),
        (models.Document, dict(code='WI-2', title='Other instruction',
                               category=models.DocumentCategory.OTRO)),
        (models.Capacitacion, dict(tema='Practice', personal='Team', fecha=date(2020, 1, 1))),
        (models.Capacitacion, dict(tema='Refresher', personal='Team', fecha=date(2020, 2, 1))),
        (models.CompetenceRequirement, dict(rol_id=1, tipo=models.CompetenceType.habilidad,
                                            descripcion='Set up')),
        (models.CompetenceRequirement, dict(rol_id=1, tipo=models.CompetenceType.formacion,
                                            descripcion='Inspect')),
        (models.NoConformidad, dict(descripcion='Defect')),
        (models.Auditoria, dict(area_auditada='Shop', auditor='Lead', resultado='Evidence')),
        (models.HallazgoAuditoria, dict(auditoria_id=1, tipo=models.TipoHallazgo.observacion,
                                        descripcion='Finding')),
        (models.Mejora, dict(descripcion='Improve', fecha_identificacion=date(2020, 1, 1))),
        (models.RevisionDireccion, dict(fecha=date(2020, 1, 1),
                                        periodo_desde=date(2019, 1, 1),
                                        periodo_hasta=date(2019, 12, 31))),
        (models.AccionRevision, dict(revision_id=1, responsable_id=1,
                                     tipo=models.TipoAccionRevision.mejora,
                                     descripcion='Review action', fecha_limite=date(2020, 2, 1))),
    )
    for model, values in fixtures:
        session.execute(model.__table__.insert().values(**values))
    session.commit()


class KnowledgeLinksServiceTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        seed_targets(db.session)

    def create(self, **changes):
        item = knowledge.items.create(db.session, ADMIN, VALID | changes)
        db.session.commit()
        return item

    def state(self):
        """Read the public register and immutable audit history."""
        return ([audit.snapshot(item) | {
            key: getattr(item, key, None) for key in LISTS
        } for item in knowledge.items.list_(db.session, ADMIN, retired=None)],
                [audit.snapshot(row) for row in self.audit_rows()])

    def reject(self, item, data, message):
        before = self.state()
        for creating in (False, True):
            with self.subTest(creating=creating), self.assertRaises(ValidationError) as error:
                if creating:
                    knowledge.items.create(db.session, ADMIN, VALID | data)
                else:
                    knowledge.items.update(db.session, ADMIN, item.id, data)
            self.assertEqual(message, error.exception.message)
            db.session.commit()
            self.assertEqual(before, self.state())

    def test_each_list_set_replace_clear_persist_audit_noop_and_retire(self):
        for key in LISTS:
            with self.subTest(key=key):
                first, second = (1, 2)
                item = self.create(**{key: [first]})
                self.assertEqual([first], getattr(knowledge.items.get(db.session, ADMIN, item.id), key))
                self.assertEqual([first], self.audit_rows()[-1].after[key])
                knowledge.items.update(db.session, ADMIN, item.id, {key: [second, second]})
                db.session.commit()
                self.assertEqual([second], getattr(knowledge.items.get(db.session, ADMIN, item.id), key))
                row = self.audit_rows()[-1]
                self.assertEqual(([first], [second]), (row.before[key], row.after[key]))
                before = self.state()
                knowledge.items.update(db.session, ADMIN, item.id, {key: [second]})
                db.session.commit()
                self.assertEqual(before, self.state())
                knowledge.items.update(db.session, ADMIN, item.id, {'retired': True})
                db.session.commit()
                self.assertEqual([second], getattr(knowledge.items.get(db.session, ADMIN, item.id), key))
                knowledge.items.update(db.session, ADMIN, item.id, {key: []})
                db.session.commit()
                self.assertEqual([], getattr(knowledge.items.get(db.session, ADMIN, item.id), key))
                self.assertEqual(([second], []), (self.audit_rows()[-1].before[key], self.audit_rows()[-1].after[key]))

    def test_bad_lists_and_unknown_ids_reject_all_fields_without_writes(self):
        item = self.create()
        for key in LISTS:
            for bad in ('1', [True], [1.0], [None], {}, None):
                self.reject(item, {'title': 'Must not change', key: bad},
                            f'El campo «{key}» debe ser una lista de identificadores.')
            for bad in (0, -1, 999, 2**63):
                self.reject(item, {'title': 'Must not change', key: [bad]},
                            f'El campo «{key}» contiene identificadores desconocidos: {bad}.')

    def test_inactive_expert_and_withdrawn_document_new_refused_existing_retained(self):
        item = self.create(expert_ids=[2], document_ids=[1])
        people.update(db.session, ADMIN, 2, {'activo': False})
        db.session.execute(models.Document.__table__.update().where(models.Document.id == 1)
                           .values(withdrawn_at=date(2020, 1, 1)))
        db.session.commit()
        for key, value, message in (
            ('expert_ids', [2], 'La persona del campo «expert_ids» está desactivada; elige una persona activa.'),
            ('document_ids', [1], 'El documento del campo «document_ids» está dado de baja; elige uno en uso.'),
        ):
            before = self.state()
            with self.assertRaises(ValidationError) as error:
                knowledge.items.create(db.session, ADMIN, VALID | {key: value})
            self.assertEqual(message, error.exception.message)
            db.session.commit()
            self.assertEqual(before, self.state())
        knowledge.items.update(db.session, ADMIN, item.id, {
            'title': 'Retained', 'expert_ids': [2], 'document_ids': [1]})
        db.session.commit()
        self.assertEqual(('Retained', [2], [1]), (item.title, item.expert_ids, item.document_ids))
        self.assertEqual({'title': 'Retained'}, {k: v for k, v in self.audit_rows()[-1].after.items()
                                               if not k.startswith('updated_')})

    def test_optional_typed_origin_both_kinds_set_change_clear_and_no_side_effects(self):
        targets = (models.NoConformidad, models.HallazgoAuditoria, models.Mejora, models.AccionRevision)
        before = {model: [audit.snapshot(row) for row in db.session.query(model)]
                  for model in targets}
        for kind in ('conocimiento', 'leccion'):
            item = self.create(kind=kind)
            previous = None
            for key in ORIGINS:
                payload = {key: 1} | ({previous: None} if previous else {})
                knowledge.items.update(db.session, ADMIN, item.id, payload)
                db.session.commit()
                self.assertEqual(1, getattr(knowledge.items.get(db.session, ADMIN, item.id), key))
                self.assertEqual(1, self.audit_rows()[-1].after[key])
                previous = key
            knowledge.items.update(db.session, ADMIN, item.id, {previous: None})
            db.session.commit()
            self.assertIsNone(getattr(item, previous))
            self.assertEqual((1, None), (self.audit_rows()[-1].before[previous], self.audit_rows()[-1].after[previous]))
        self.assertEqual(before, {
            model: [audit.snapshot(row) for row in db.session.query(model)]
            for model in targets})
        self.assertEqual({'knowledge_items'}, {row.entity_type for row in self.audit_rows()})

    def test_origin_ids_must_exist_be_integer_and_only_one_may_remain(self):
        item = self.create()
        for key in ORIGINS:
            for bad in (True, '1', 1.2):
                self.reject(item, {key: bad}, f'El campo «{key}» debe ser un número entero.')
            for bad in (0, -1, 999, 2**63):
                self.reject(item, {key: bad}, f'El campo «{key}» no corresponde a ningún registro de origen.')
        self.reject(item, {'no_conformidad_id': 1, 'hallazgo_id': 1},
                    'Elige como máximo un registro de origen.')
        knowledge.items.update(db.session, ADMIN, item.id, {'no_conformidad_id': 1})
        db.session.commit()
        before = self.state()
        with self.assertRaises(ValidationError) as error:
            knowledge.items.update(db.session, ADMIN, item.id, {'hallazgo_id': 1})
        self.assertEqual('Elige como máximo un registro de origen.', error.exception.message)
        db.session.commit()
        self.assertEqual(before, self.state())

    def test_expert_person_reference_protection_and_uncited_deletion_preserved(self):
        item = self.create(expert_ids=[2], retired=True)
        before = self.state()
        with self.assertRaises(Conflict) as error:
            people.delete(db.session, ADMIN, 2)
        self.assertEqual(people.STILL_REFERENCED, error.exception.message)
        db.session.commit()
        self.assertEqual(before, self.state())
        knowledge.items.update(db.session, ADMIN, item.id, {'expert_ids': []})
        db.session.commit()
        people.delete(db.session, ADMIN, 2)
        db.session.commit()
        self.assertEqual([1], [p.id for p in people.list_(db.session, ADMIN)])

    def test_delete_knowledge_with_outbound_links_keeps_full_audit(self):
        item = self.create(**{key: [1] for key in LISTS}, no_conformidad_id=1)
        knowledge.items.delete(db.session, ADMIN, item.id)
        db.session.commit()
        self.assertEqual([], knowledge.items.list_(db.session, ADMIN, retired=None))
        for key in LISTS:
            self.assertEqual([1], self.audit_rows()[-1].before[key])
        self.assertEqual(1, self.audit_rows()[-1].before['no_conformidad_id'])
        self.assertEqual(2, len(people.list_(db.session, ADMIN)))


class KnowledgeLinksMcpTestCase(McpDbCase):
    def setUp(self):
        super().setUp()
        seed_targets(db.session)

    async def test_mcp_links_read_create_update_clear_invalid_and_operator_denial(self):
        links = {key: [1] for key in LISTS} | {'no_conformidad_id': 1}
        result = await self.call(mcp_actor(), 'qms_create', {'module': 'knowledge', 'data': VALID | links})
        self.assertFalse(result.is_error, result.content)
        item_id = result.structured_content['id']
        for key, value in links.items():
            self.assertEqual(value, result.structured_content[key])
        before = models.AuditLog.query.count()
        for data, who, message in (
            ({'expert_ids': [999]}, mcp_actor(), 'El campo «expert_ids» contiene identificadores desconocidos: 999.'),
            ({'process_ids': []}, mcp_actor(models.RoleEnum.OPERATIVO), 'No tienes permiso para realizar esta acción.'),
        ):
            bad = await self.call(who, 'qms_update', {'module': 'knowledge', 'id': item_id, 'data': data})
            self.assertTrue(bad.is_error)
            self.assertEqual('Error executing tool qms_update: ' + message, bad.content[0].text)
            self.assertEqual(before, models.AuditLog.query.count())
        read = await self.call(mcp_actor(models.RoleEnum.OPERATIVO), 'qms_get', {'module': 'knowledge', 'id': item_id})
        for key, value in links.items():
            self.assertEqual(value, read.structured_content[key])
        cleared = {key: [] for key in LISTS} | {'no_conformidad_id': None}
        updated = await self.call(mcp_actor(models.RoleEnum.AUDITOR), 'qms_update', {
            'module': 'knowledge', 'id': item_id, 'data': cleared})
        self.assertFalse(updated.is_error, updated.content)
        for key, value in cleared.items():
            self.assertEqual(value, updated.structured_content[key])
        self.assertEqual(before + 1, models.AuditLog.query.count())


class KnowledgeLinksWebTestCase(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            seed_targets(db.session)
        self.login(models.RoleEnum.ADMINISTRADOR)

    def test_form_links_show_roundtrip_retirement_and_operator_denial(self):
        form = VALID | {'owner_id': '1', **{key: ['1'] for key in LISTS},
                        'no_conformidad_id': '1', 'hallazgo_id': '', 'mejora_id': '',
                        'accion_revision_id': ''}
        response = self.client.post('/conocimientos/nuevo', data=form)
        self.assertEqual((302, '/conocimientos/1'), (response.status_code, response.location))
        html = self.client.get('/conocimientos/1').get_data(as_text=True)
        for label in ('Welding', 'WI-1', 'Owner', 'Practice', 'Set up', 'No conformidad'):
            self.assertIn(label, html)
        before = self.actions()
        html = self.client.get('/conocimientos/1/editar').get_data(as_text=True)
        for key in (*LISTS, 'no_conformidad_id'):
            import re
            select_html = re.search(
                rf'<select[^>]*name="{key}"[^>]*>(.*?)</select>', html, re.S).group(1)
            self.assertIn('selected value="1"', select_html)
        self.assertEqual(302, self.client.post('/conocimientos/1/editar', data=form).status_code)
        self.assertEqual(before, self.actions())
        with self.app.app_context():
            people.update(db.session, ADMIN, 1, {'activo': False})
            db.session.execute(models.Document.__table__.update().where(models.Document.id == 1)
                               .values(withdrawn_at=date(2020, 1, 1)))
            db.session.commit()
        html = self.client.get('/conocimientos/1/editar').get_data(as_text=True)
        self.assertIn('desactivada', html)
        self.assertIn('de baja', html)
        self.assertEqual(302, self.client.post('/conocimientos/1/editar', data=form | {'retired': 'y'}).status_code)
        before = self.actions()
        self.login(models.RoleEnum.OPERATIVO)
        response = self.client.post('/conocimientos/1/editar', data=form | {'process_ids': []})
        self.assertEqual((302, '/dashboard/'), (response.status_code, response.location))
        self.assertIn(('danger', 'No tienes permiso para acceder a esta página.'), self.flashes())
        self.assertEqual(before, self.actions())
        self.assertEqual(200, self.client.get('/conocimientos/1').status_code)


class KnowledgeLinksMigrationTestCase(unittest.TestCase):
    def test_additive_migration_preserves_knowledge_and_restricts_every_target(self):
        self.assertTrue(MIGRATION.exists(), 'KN2 migration must exist')
        spec = importlib.util.spec_from_file_location('knowledge_links_migration', MIGRATION)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('e8a0c2d4f6b8', migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                connection.execute(text('PRAGMA foreign_keys=ON'))
                # Construct the actual pre-KN2 schema with the KN1 migration.
                base_path = MIGRATION.with_name('e8a0c2d4f6b8_add_organizational_knowledge.py')
                base_spec = importlib.util.spec_from_file_location('knowledge_base', base_path)
                base = importlib.util.module_from_spec(base_spec)
                base_spec.loader.exec_module(base)
                db.metadata.create_all(connection, tables=[table for table in db.metadata.sorted_tables
                    if not table.name.startswith('knowledge_')])
                with patch.object(base, 'op', Operations(MigrationContext.configure(connection))):
                    base.upgrade()
                connection.execute(models.Person.__table__.insert().values(nombre='Existing'))
                connection.execute(text("INSERT INTO knowledge_items (kind,title,summary,owner_id) VALUES ('leccion','Existing','Preserve',1)"))
                before = connection.execute(text('SELECT * FROM knowledge_items')).all()
                base_columns = [c['name'] for c in inspect(connection).get_columns('knowledge_items')]
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    # Full pre-existing row, including retirement and attribution.
                    upgraded = connection.execute(text('SELECT * FROM knowledge_items')).one()._mapping
                    self.assertEqual(tuple(before[0]), tuple(upgraded[key] for key in base_columns))
                    for table in (models.KnowledgeItem.__table__, *models.KNOWLEDGE_ASSOCIATIONS.values()):
                        actual = inspect(connection)
                        self.assertEqual({c.name for c in table.columns},
                                         {c['name'] for c in actual.get_columns(table.name)})
                        for fk in actual.get_foreign_keys(table.name):
                            if fk['name'].startswith('fk_knowledge_') and fk['referred_table'] != 'users':
                                self.assertEqual('RESTRICT', fk['options']['ondelete'])
                    migration.downgrade()
                    self.assertEqual(before, connection.execute(text('SELECT * FROM knowledge_items')).all())
                    migration.upgrade()
        finally:
            engine.dispose()

    def test_database_restricts_linked_targets_and_multiple_origins(self):
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                connection.execute(text('PRAGMA foreign_keys=ON'))
                db.metadata.create_all(connection)
                with Session(bind=connection) as session:
                    seed_targets(session)
                item_id = connection.execute(models.KnowledgeItem.__table__.insert().values(**VALID)).inserted_primary_key[0]
                association = getattr(models, 'KNOWLEDGE_ASSOCIATIONS', {})
                self.assertEqual(set(LISTS), set(association))
                for key, target, target_id in (
                    ('expert_ids', models.Person.__table__, 2),
                    ('process_ids', models.ProcesoOperacion.__table__, 1),
                    ('document_ids', models.Document.__table__, 1),
                    ('training_ids', models.Capacitacion.__table__, 1),
                    ('requirement_ids', models.CompetenceRequirement.__table__, 1),
                ):
                    table = association[key]
                    target_column = list(table.c)[1]
                    connection.execute(table.insert().values(knowledge_id=item_id, **{target_column.name: target_id}))
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(target.delete().where(list(target.primary_key)[0] == target_id))
                for key, model in zip(ORIGINS, (models.NoConformidad, models.HallazgoAuditoria,
                                                models.Mejora, models.AccionRevision)):
                    connection.execute(models.KnowledgeItem.__table__.update().values(**{key: 1}))
                    with self.assertRaises(IntegrityError), connection.begin_nested():
                        connection.execute(model.__table__.delete().where(list(model.__table__.primary_key)[0] == 1))
                    connection.execute(models.KnowledgeItem.__table__.update().values(**{key: None}))
                with self.assertRaises(IntegrityError), connection.begin_nested():
                    connection.execute(models.KnowledgeItem.__table__.update().values(no_conformidad_id=999))
                with self.assertRaises(IntegrityError), connection.begin_nested():
                    connection.execute(models.KnowledgeItem.__table__.update().values(no_conformidad_id=1, hallazgo_id=1))
        finally:
            engine.dispose()
