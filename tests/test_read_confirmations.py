"""Read confirmations: service rules, audit history and database constraints (5.2, 7.3)."""
import os
import unittest
from datetime import date, datetime, timedelta
from uuid import uuid4

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema

from app.extensions import db
from app.models import ConfirmacionLectura, Document, DocumentCategory, DocumentRevision, Person, RoleEnum
from app.services import document_revisions, people, read_confirmations as confirmations
from app.services.actor import Actor
from app.services.errors import Conflict, NotFound, PermissionDenied, ValidationError
from test_nonconformity_service import ServiceBase

TODAY = date(2026, 10, 18)
ADMIN = Actor(None, 'test', RoleEnum.ADMINISTRADOR, 'cli')


class ReadConfirmationsTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.person = db.session.execute(Person.__table__.insert().values(nombre='Ana')).inserted_primary_key[0]
        document = db.session.execute(Document.__table__.insert().values(
            title='Policy', code='POL-1', category=DocumentCategory.POLITICA_CALIDAD)).inserted_primary_key[0]
        self.revision = db.session.execute(DocumentRevision.__table__.insert().values(
            document_id=document, numero=1, content='Policy text')).inserted_primary_key[0]
        db.session.commit()
        self.valid = dict(persona_id=self.person, tipo='objetivos', fecha_confirmacion=TODAY)

    def create(self, **values):
        row = confirmations.create(db.session, ADMIN, self.valid | values, today=TODAY)
        db.session.commit()
        return row

    def test_create_update_noop_delete_keep_attribution_and_history(self):
        row = self.create(tipo='documento', document_revision_id=self.revision, notas=' Read ')
        self.assertEqual(('documento', self.revision, ' Read '),
                         (row.tipo.name, row.document_revision_id, row.notas))
        self.assertIsNotNone(row.created_at)
        self.assertEqual(row.created_at, row.updated_at)
        self.assertEqual('Documento', self.audit_rows()[-1].after['tipo'])
        confirmations.update(db.session, ADMIN, row.id, {'notas': ' Read '}, today=TODAY)
        db.session.commit()
        self.assertEqual(1, len(self.audit_rows()))
        confirmations.update(db.session, ADMIN, row.id,
                             {'tipo': 'objetivos', 'document_revision_id': None}, today=TODAY)
        db.session.commit()
        self.assertEqual(('Documento', 'Objetivos de calidad'),
                         (self.audit_rows()[-1].before['tipo'], self.audit_rows()[-1].after['tipo']))
        record_id = row.id
        confirmations.delete(db.session, ADMIN, record_id)
        db.session.commit()
        self.assertEqual(['create', 'update', 'delete'], [r.action for r in self.audit_rows()])
        with self.assertRaises(NotFound):
            confirmations.get(db.session, ADMIN, record_id)

    def test_invalid_creates_and_partial_updates_leave_record_and_audit_unchanged(self):
        row = self.create(notas='Kept')
        cases = [
            ({'tipo': 'documento'}, confirmations.REVISION_REQUIRED),
            ({'document_revision_id': self.revision}, confirmations.OBJECTIVES_ONLY),
            ({'document_revision_id': 999}, confirmations.UNKNOWN_REVISION),
            ({'fecha_confirmacion': TODAY + timedelta(days=1)}, confirmations.FUTURE_DATE),
            ({'persona_id': 999}, 'El campo «persona_id» no corresponde a ninguna persona.'),
            ({'persona_id': None}, 'El campo «persona_id» debe ser un número entero.'),
            ({'persona_id': True}, 'El campo «persona_id» debe ser un número entero.'),
            ({'document_revision_id': True}, 'El campo «document_revision_id» debe ser un número entero.'),
            ({'tipo': 'Documento'}, 'El valor del campo «tipo» no es válido.'),
            ({'fecha_confirmacion': datetime(2026, 1, 1)},
             'El campo «fecha_confirmacion» es obligatorio y debe ser una fecha.'),
            ({'created_by_id': 1}, 'Campos no permitidos: created_by_id.'),
        ]
        for changes, message in cases:
            for updating in (False, True):
                with self.subTest(changes=changes, updating=updating):
                    with self.assertRaises(ValidationError) as caught:
                        if updating:
                            confirmations.update(db.session, ADMIN, row.id, changes, today=TODAY)
                        else:
                            confirmations.create(db.session, ADMIN, self.valid | changes, today=TODAY)
                    self.assertEqual(message, caught.exception.message)
                    db.session.rollback()
                    found, total = confirmations.list_page(db.session, ADMIN)
                    self.assertEqual((1, 1, 'Kept', 'objetivos', None, TODAY),
                                     (total, len(self.audit_rows()), found[0].notas,
                                      found[0].tipo.name, found[0].document_revision_id,
                                      found[0].fecha_confirmacion))

    def test_required_fields_and_optional_blank_notes(self):
        for key in self.valid:
            with self.subTest(key=key), self.assertRaises(ValidationError) as caught:
                confirmations.create(db.session, ADMIN,
                                     {k: v for k, v in self.valid.items() if k != key}, today=TODAY)
            self.assertEqual(f'Faltan campos obligatorios: {key}.', caught.exception.message)
        row = self.create(notas='   ', fecha_confirmacion=TODAY - timedelta(days=1))
        self.assertIsNone(row.notas)

    def test_order_pagination_and_combined_filters(self):
        old = self.create(fecha_confirmacion=TODAY - timedelta(days=1))
        first = self.create()
        latest = self.create(tipo='documento', document_revision_id=self.revision)
        self.assertEqual([latest, first, old], confirmations.list_(db.session, ADMIN))
        filters = dict(persona_id=self.person, tipo='documento', document_revision_id=self.revision)
        self.assertEqual([latest], confirmations.list_(db.session, ADMIN, **filters))
        self.assertEqual(([latest], 1), confirmations.list_page(db.session, ADMIN, **filters))
        self.assertEqual(([first], 3), confirmations.list_page(db.session, ADMIN, page=2, per_page=1))
        self.assertEqual([], confirmations.list_(db.session, ADMIN, persona_id=2**40))
        self.assertEqual([], confirmations.list_(db.session, ADMIN, document_revision_id=2**40))
        with self.assertRaises(ValidationError):
            confirmations.list_(db.session, ADMIN, tipo='wrong')

    def test_every_role_writes_but_only_web_or_cli_admin_deletes(self):
        for role in RoleEnum:
            who = Actor(None, 'test', role, 'web')
            row = confirmations.create(db.session, who, self.valid, today=TODAY)
            confirmations.update(db.session, who, row.id, {'notas': 'Changed'}, today=TODAY)
            self.assertIs(row, confirmations.get(db.session, who, row.id))
            if role is not RoleEnum.ADMINISTRADOR:
                with self.assertRaises(PermissionDenied):
                    confirmations.delete(db.session, who, row.id)
            db.session.commit()
        for channel, scopes in [('mcp', frozenset({'read', 'write'})), ('web', frozenset({'read'}))]:
            with self.assertRaises(PermissionDenied):
                confirmations.delete(db.session, Actor(None, 'test', RoleEnum.ADMINISTRADOR,
                                                       channel, scopes), row.id)

    def test_cited_draft_is_not_discarded_by_the_revision_service(self):
        from app.models import EstadoRevision

        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.execute(DocumentRevision.__table__.update()
                           .where(DocumentRevision.id == self.revision)
                           .values(estado=EstadoRevision.vigente))
        document_id = db.session.get(DocumentRevision, self.revision).document_id
        draft = db.session.execute(DocumentRevision.__table__.insert().values(
            document_id=document_id, numero=2, content='New draft')).inserted_primary_key[0]
        db.session.commit()
        row = self.create(tipo='documento', document_revision_id=draft)
        with self.assertRaises(Conflict):
            document_revisions.discard_draft(db.session, ADMIN, draft)
        db.session.rollback()
        self.assertEqual(draft, confirmations.get(db.session, ADMIN, row.id).document_revision_id)
        self.assertIsNotNone(document_revisions.get(db.session, ADMIN, draft))
        self.assertEqual(['create'], [record.action for record in self.audit_rows()])

    def test_cited_person_is_protected_and_inactive_link_remains_editable(self):
        row = self.create()
        with self.assertRaises(Conflict) as caught:
            people.delete(db.session, ADMIN, self.person)
        self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
        db.session.rollback()
        people.update(db.session, ADMIN, self.person, {'activo': False})
        db.session.commit()
        confirmations.update(db.session, ADMIN, row.id, {'persona_id': self.person, 'notas': 'Kept'}, today=TODAY)
        db.session.commit()
        with self.assertRaises(ValidationError):
            confirmations.create(db.session, ADMIN, self.valid, today=TODAY)
        self.assertEqual('Kept', confirmations.get(db.session, ADMIN, row.id).notas)


class ConstraintCases:
    def test_constraint_matrix_and_restrict_foreign_keys(self):
        engine = create_engine(self.uri)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if engine.dialect.name == 'postgresql':
                    schema = 'read_confirmations_' + uuid4().hex
                    connection.execute(CreateSchema(schema))
                    connection = connection.execution_options(schema_translate_map={None: schema})
                else:
                    connection.execute(text('PRAGMA foreign_keys=ON'))
                db.metadata.create_all(connection)
                person = connection.execute(Person.__table__.insert().values(nombre='Ana')).inserted_primary_key[0]
                doc = connection.execute(Document.__table__.insert().values(
                    title='Policy', code='POL-1', category=DocumentCategory.POLITICA_CALIDAD)).inserted_primary_key[0]
                revision = connection.execute(DocumentRevision.__table__.insert().values(
                    document_id=doc, numero=1, content='Text')).inserted_primary_key[0]
                table = ConfirmacionLectura.__table__
                for tipo, link, valid in [('documento', revision, True), ('documento', None, False),
                                          ('objetivos', None, True), ('objetivos', revision, False),
                                          (None, None, False), (None, revision, False),
                                          ('unknown', None, False)]:
                    with self.subTest(tipo=tipo, link=link):
                        savepoint = connection.begin_nested()
                        try:
                            statement = table.insert().values(persona_id=person, tipo=tipo,
                                document_revision_id=link, fecha_confirmacion=TODAY)
                            if valid:
                                connection.execute(statement)
                            else:
                                with self.assertRaises(IntegrityError):
                                    connection.execute(statement)
                        finally:
                            savepoint.rollback()
                connection.execute(table.insert().values(persona_id=person, tipo='documento',
                                   document_revision_id=revision, fecha_confirmacion=TODAY))
                for statement in [Person.__table__.delete().where(Person.id == person),
                                  DocumentRevision.__table__.delete().where(DocumentRevision.id == revision),
                                  table.insert().values(persona_id=None, tipo='objetivos', fecha_confirmacion=TODAY),
                                  table.insert().values(persona_id=999, tipo='objetivos', fecha_confirmacion=TODAY),
                                  table.insert().values(persona_id=person, tipo='objetivos', fecha_confirmacion=None),
                                  table.insert().values(persona_id=person, tipo='documento', document_revision_id=999,
                                                        fecha_confirmacion=TODAY)]:
                    with connection.begin_nested() as savepoint:
                        with self.assertRaises(IntegrityError):
                            connection.execute(statement)
                        savepoint.rollback()
            finally:
                transaction.rollback()
        engine.dispose()


class SqliteConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = 'sqlite:///:memory:'


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_URI'), 'TEST_POSTGRES_URI is not set')
class PostgresConstraintTestCase(ConstraintCases, unittest.TestCase):
    uri = os.environ.get('TEST_POSTGRES_URI')
