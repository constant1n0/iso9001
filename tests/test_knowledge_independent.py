"""Independent KN1 probes: S1–S3, S5."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
import test_auth_bootstrap as bootstrap
from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import AuditLog, KnowledgeItem, Person, RoleEnum
from app.services import people, knowledge
from app.services.actor import Actor
from app.services.errors import PermissionDenied, ValidationError

ADMIN = Actor(None, 'probe', RoleEnum.ADMINISTRADOR, 'cli')
FORM = dict(kind='leccion', title='Lección de ajuste', summary='Conserva la muestra.',
            source='Taller', owner_id='1', next_review_date='')

def digest():
    return hashlib.sha256(Path(db.engine.url.database).read_bytes()).hexdigest()

class IndependentWeb(RegisterRoutesBase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        build = bootstrap.build_app
        p = patch.object(bootstrap, 'build_app', lambda: build(
            SQLALCHEMY_DATABASE_URI='sqlite:///' + storage.name + '/web-probe.sqlite'))
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        self.seed_with(people, {'nombre':'Activa'})
        self.login(RoleEnum.ADMINISTRADOR)

    def test_lesson_edit_browser_roundtrip_preserves_kind(self):
        self.assertEqual(302, self.client.post('/conocimientos/nuevo', data=FORM).status_code)
        html = self.client.get('/conocimientos/1/editar').get_data(as_text=True)
        select = re.search(r'<select[^>]*name="kind"[^>]*>(.*?)</select>', html, re.S).group(1)
        options = re.findall(r'<option([^>]*)>', select)
        chosen = next((v for v in options if 'selected' in v), options[0])
        browser_value = re.search(r'value="([^"]*)"', chosen).group(1)
        print('EDIT_KIND_OPTIONS', select, 'BROWSER_VALUE', browser_value)
        before = self.actions()
        response = self.client.post('/conocimientos/1/editar', data=FORM | {'kind':browser_value})
        self.assertEqual(302, response.status_code)
        self.assertEqual('leccion', self.fetch(KnowledgeItem, 1).kind.name)
        self.assertEqual(before, self.actions())

    def test_web_rejection_hashes_csrf_and_owner_guard(self):
        self.assertEqual(302, self.client.post('/conocimientos/nuevo', data=FORM).status_code)
        for role in RoleEnum:
            self.login(role)
            for url in ('/conocimientos/', '/conocimientos/1'):
                self.assertEqual(200, self.client.get(url).status_code)
            if role not in (RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR):
                for url in ('/conocimientos/nuevo', '/conocimientos/1/editar', '/conocimientos/1/eliminar'):
                    with self.app.app_context(): before = digest()
                    response = self.client.post(url, data=FORM)
                    self.assertEqual((302, '/dashboard/'), (response.status_code, response.location))
                    self.assertIn(('danger', 'No tienes permiso para acceder a esta página.'), self.flashes())
                    with self.app.app_context(): self.assertEqual(before, digest())
        self.login(RoleEnum.AUDITOR)
        with self.app.app_context(): before = digest()
        self.assertEqual('/dashboard/', self.client.post('/conocimientos/1/eliminar').location)
        with self.app.app_context(): self.assertEqual(before, digest())
        self.login(RoleEnum.ADMINISTRADOR)
        with self.app.app_context():
            before = digest()
            from app.services.errors import Conflict
            with self.assertRaises(Conflict) as error:
                people.delete(db.session, ADMIN, 1)
            self.assertEqual(people.STILL_REFERENCED, error.exception.message)
            db.session.commit()
            self.assertEqual(before, digest())
        self.app.config['WTF_CSRF_ENABLED'] = True
        for url in ('/conocimientos/nuevo','/conocimientos/1/editar','/conocimientos/1/eliminar'):
            with self.app.app_context(): before = digest()
            result = self.client.post(url, data=FORM)
            self.assertEqual(400, result.status_code)
            self.assertEqual({'message':'Solicitud inválida'}, result.get_json())
            with self.app.app_context(): self.assertEqual(before, digest())
        for url in ('/conocimientos/nuevo','/conocimientos/1/editar'):
            self.assertRegex(self.client.get(url).get_data(as_text=True), r'name="csrf_token"[^>]*value="[^"]+"')
        print('WEB_REJECTION_HASHES_UNCHANGED')

class IndependentMcp(McpDbCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.app_config = {'SQLALCHEMY_DATABASE_URI':'sqlite:///' + storage.name + '/mcp-probe.sqlite'}
        super().setUp()
    async def test_documented_example_matrix_boundaries_and_hashes(self):
        self.seed('personas')
        self.seed('personas', nombre='Inactiva', activo=False)
        data = {'kind':'leccion', 'title':'Ajuste del útil',
                'summary':'Conserva la muestra de referencia antes de ajustar el útil.',
                'source':'Experiencia del taller', 'owner_id':1}
        created = await self.call(mcp_actor(), 'qms_create', {'module':'knowledge','data':data})
        self.assertFalse(created.is_error, created.content)
        row = created.structured_content
        self.assertEqual(('Lección aprendida', data['title'], data['summary'], data['source'],1,False,False),
            (row['kind'],row['title'],row['summary'],row['source'],row['owner_id'],row['retired'],row['overdue']))
        print('DOC_EXAMPLE', row)
        for role in RoleEnum:
            who = mcp_actor(role)
            self.assertFalse((await self.call(who,'qms_get',{'module':'knowledge','id':1})).is_error)
            for name, args in [('qms_create',{'module':'knowledge','data':data}),
                               ('qms_update',{'module':'knowledge','id':1,'data':{'summary':role.name}})]:
                before, audits = digest(), AuditLog.query.count()
                result = await self.call(who,name,args)
                allowed = role in (RoleEnum.ADMINISTRADOR,RoleEnum.AUDITOR)
                self.assertEqual(not allowed, result.is_error)
                if allowed: self.assertEqual(audits + 1, AuditLog.query.count())
                else:
                    self.assertEqual('Error executing tool '+name+': No tienes permiso para realizar esta acción.', result.content[0].text)
                    self.assertEqual(before,digest())
            before = digest()
            with self.assertRaises(PermissionDenied): knowledge.items.delete(db.session,who,1)
            self.assertEqual(before,digest())
        self.assertNotIn('qms_delete',{tool.name for tool in await self.list_tools()})
        cases = [({'owner_id':0}, 'El campo «owner_id» no corresponde a ninguna persona.'),
                 ({'owner_id':-1}, 'El campo «owner_id» no corresponde a ninguna persona.'),
                 ({'owner_id':999}, 'El campo «owner_id» no corresponde a ninguna persona.'),
                 ({'owner_id':2}, 'La persona del campo «owner_id» está desactivada; elige una persona activa.'),
                 ({'title':'x'*201}, 'El campo «title» admite como máximo 200 caracteres.'),
                 ({'summary':' \t'}, 'El campo «summary» es obligatorio.'),
                 ({'kind':'Leccion'}, 'El valor del campo «kind» no es válido.'),
                 ({'next_review_date':'2026-02-30'}, 'El campo «next_review_date» debe ser una fecha AAAA-MM-DD.'),
                 ({'retired':None}, 'El campo «retired» debe ser verdadero o falso.')]
        for bad,message in cases:
            before = digest()
            result = await self.call(mcp_actor(),'qms_update',{'module':'knowledge','id':1,'data':bad})
            self.assertTrue(result.is_error, (bad,result.content))
            self.assertEqual('Error executing tool qms_update: '+message, result.content[0].text)
            self.assertEqual(before,digest())
        for scopes in ((),('read',)):
            before = digest()
            result = await self.call(mcp_actor(scopes=scopes),'qms_update',{'module':'knowledge','id':1,'data':{'retired':True}})
            self.assertTrue(result.is_error)
            self.assertEqual(before,digest())
        today = date(2026,11,2)
        with patch('app.mcp_server.registry.local_today',return_value=today):
            for due,retired,overdue in ((None,False,False),(today,False,False),
                    (today-timedelta(days=1),False,True),(today+timedelta(days=1),False,False),
                    (today-timedelta(days=1),True,False)):
                result = await self.call(mcp_actor(),'qms_update',{'module':'knowledge','id':1,
                    'data':{'next_review_date':due.isoformat() if due else None,'retired':retired,'title':'x'*200}})
                self.assertFalse(result.is_error,result.content)
                self.assertEqual(overdue,result.structured_content['overdue'])
            self.assertEqual(2,(await self.call(mcp_actor(),'qms_list',{'module':'knowledge'})).structured_content['total'])
            self.assertFalse((await self.call(mcp_actor(RoleEnum.OPERATIVO),'qms_get',{'module':'knowledge','id':1})).is_error)
        print('MCP_MATRIX_VALIDATION_HASHES_UNCHANGED')

class IndependentMigration(unittest.TestCase):
    def test_additive_schema_and_data_snapshot(self):
        path = Path('migrations/versions/e8a0c2d4f6b8_add_organizational_knowledge.py')
        spec = importlib.util.spec_from_file_location('probe_migration',path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('d7f9a1b3c5e7',migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            db.metadata.create_all(connection,tables=[t for t in db.metadata.sorted_tables if t.name != 'knowledge_items'])
            connection.execute(Person.__table__.insert().values(nombre='Preserved'))
            def schema():
                return connection.execute(text("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name != 'knowledge_items' ORDER BY type,name")).all()
            def data():
                return {t.name:connection.execute(t.select()).all() for t in db.metadata.sorted_tables if t.name != 'knowledge_items'}
            before_schema,before_data = schema(),data()
            with patch.object(migration,'op',Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(before_schema,schema()); self.assertEqual(before_data,data())
                migration.downgrade()
                self.assertEqual(before_schema,schema()); self.assertEqual(before_data,data())
        engine.dispose()
        print('ALL_EXISTING_SCHEMA_AND_ROWS_UNCHANGED')
