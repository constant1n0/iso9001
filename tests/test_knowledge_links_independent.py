"""Independent KN2 regression probes: S3 retirement, S4 links, S5 access.

Run: python -m unittest discover -s tests -p 'test_knowledge_links_independent.py' -v
Fixtures deliberately do not reuse the writer's link tests.
"""
from datetime import date
from html.parser import HTMLParser
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import importlib.util
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from unittest.mock import patch

from werkzeug.datastructures import MultiDict
import test_auth_bootstrap as bootstrap
from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from app import models as m
from app.extensions import db
from app.services import audit, knowledge
from app.services.actor import Actor

ADMIN = Actor(None, 'independent', m.RoleEnum.ADMINISTRADOR, 'cli')
LISTS = ('process_ids', 'document_ids', 'expert_ids', 'training_ids', 'requirement_ids')
ORIGINS = ('no_conformidad_id', 'hallazgo_id', 'mejora_id', 'accion_revision_id')
VALID = dict(kind='leccion', title='Independent lesson', summary='Keep the sample.', owner_id=1)
BASELINE = os.environ.get('KN2_BASELINE') == '1'


def seed():
    fixtures = [
        (m.Person, dict(nombre='Owner')),
        (m.RolResponsabilidad, dict(rol='Fabrication')),
        (m.Auditoria, dict(area_auditada='RESTRICTED_AUDIT', auditor='Lead', resultado='Evidence')),
        (m.RevisionDireccion, dict(fecha=date(2020, 1, 1), periodo_desde=date(2019, 1, 1),
                                  periodo_hasta=date(2019, 12, 31))),
    ]
    for model, values in fixtures:
        db.session.execute(model.__table__.insert().values(**values))
    for n in range(1, 9):
        fixtures = [
            (m.Person, dict(nombre=f'Expert {n}')),
            (m.ProcesoOperacion, dict(proceso=f'Process {n}')),
            (m.Document, dict(code=f'DOC-{n}', title=f'RESTRICTED_DOC_{n}', category=m.DocumentCategory.OTRO)),
            (m.Capacitacion, dict(tema=f'Training {n}', personal='Team', fecha=date(2020, 1, 1))),
            (m.CompetenceRequirement, dict(rol_id=1, tipo=m.CompetenceType.habilidad, descripcion=f'Requirement {n}')),
            (m.NoConformidad, dict(descripcion=f'NC {n}')),
            (m.HallazgoAuditoria, dict(auditoria_id=1, tipo=m.TipoHallazgo.observacion, descripcion=f'RESTRICTED_FINDING_{n}')),
            (m.Mejora, dict(descripcion=f'Improvement {n}', fecha_identificacion=date(2020, 1, 1))),
            (m.AccionRevision, dict(revision_id=1, responsable_id=1, tipo=m.TipoAccionRevision.mejora,
                                   descripcion=f'RESTRICTED_ACTION_{n}', fecha_limite=date(2020, 2, 1))),
        ]
        for model, values in fixtures:
            db.session.execute(model.__table__.insert().values(**values))
    db.session.commit()


def digest():
    return hashlib.sha256(Path(db.engine.url.database).read_bytes()).hexdigest()


def targets():
    return {t.name: [tuple(row) for row in db.session.execute(t.select())]
            for t in db.metadata.sorted_tables
            if not t.name.startswith('knowledge_') and t.name != m.AuditLog.__tablename__}


def links(item):
    return {key: getattr(item, key) for key in (*LISTS, *ORIGINS)}


class BrowserForm(HTMLParser):
    """Successful browser controls; an unselected single select uses its first option."""
    def __init__(self, html):
        super().__init__()
        self.controls, self.select, self.textarea = [], None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'select':
            self.select = (a.get('name'), 'multiple' in a, [])
        elif tag == 'option' and self.select:
            self.select[2].append((a.get('value', ''), 'selected' in a))
        elif tag == 'textarea':
            self.textarea = [a.get('name'), '']
        elif tag == 'input' and a.get('name') and 'disabled' not in a:
            kind = a.get('type', 'text')
            if kind not in ('submit', 'button') and (kind not in ('checkbox', 'radio') or 'checked' in a):
                self.controls.append((a['name'], a.get('value', 'on')))

    def handle_data(self, data):
        if self.textarea:
            self.textarea[1] += data

    def handle_endtag(self, tag):
        if tag == 'select' and self.select:
            name, multiple, options = self.select
            chosen = [value for value, selected in options if selected]
            if not chosen and not multiple and options:
                chosen = [options[0][0]]
            self.controls.extend((name, value) for value in chosen)
            self.select = None
        elif tag == 'textarea' and self.textarea:
            # HTML parsing drops the first newline immediately following <textarea>.
            name, value = self.textarea
            if value.startswith('\r\n'): value = value[2:]
            elif value.startswith(('\r', '\n')): value = value[1:]
            self.controls.append((name, value))
            self.textarea = None

    def data(self):
        return MultiDict((k, v) for k, v in self.controls if k)


class IndependentLinksWeb(RegisterRoutesBase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        build = bootstrap.build_app
        p = patch.object(bootstrap, 'build_app', lambda: build(
            SQLALCHEMY_DATABASE_URI='sqlite:///' + storage.name + '/web.sqlite'))
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        with self.app.app_context():
            # Production PostgreSQL enforces these; SQLite must explicitly enable them.
            db.session.execute(db.text('PRAGMA foreign_keys=ON'))
            seed()
        self.login(m.RoleEnum.ADMINISTRADOR)

    def create(self, **extra):
        with self.app.app_context():
            item = knowledge.items.create(db.session, ADMIN, VALID | extra)
            db.session.commit()
            return item.id

    def browser(self, item_id):
        result = self.client.get(f'/conocimientos/{item_id}/editar')
        self.assertEqual(200, result.status_code)
        return BrowserForm(result.get_data(as_text=True)).data()

    def state(self, item_id):
        with self.app.app_context():
            return links(db.session.get(m.KnowledgeItem, item_id))

    def test_browser_roundtrip_each_origin_and_all_multiselects(self):
        """S3/S4: actual rendered defaults, replacement, clear, retired history, no side effects."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                item_id = self.create()
                form = self.browser(item_id)
                for k in LISTS:
                    form.setlist(k, ['1', '2'])
                form[origin] = '1'
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=form).status_code)
                form = self.browser(item_id)
                for k in LISTS:
                    self.assertEqual(['1', '2'], sorted(form.getlist(k)))
                self.assertEqual('1', form[origin])
                with self.app.app_context(): before, cited = digest(), targets()
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=form).status_code)
                with self.app.app_context(): self.assertEqual(before, digest())
                for k in LISTS:
                    form.setlist(k, ['2', '3'])
                form[origin] = '2'
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=form).status_code)
                expected = {k: [2, 3] for k in LISTS} | {k: 2 if k == origin else None for k in ORIGINS}
                self.assertEqual(expected, self.state(item_id))
                # Change the origin TYPE in the same POST, as the UI instructs.
                replacement = ORIGINS[(ORIGINS.index(origin) + 1) % 4]
                form[origin], form[replacement], form['retired'] = '', '2', 'y'
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=form).status_code)
                expected |= {origin: None, replacement: 2}
                self.assertEqual(expected, self.state(item_id))
                rendered = self.browser(item_id)
                self.assertEqual('2', rendered[replacement])
                with self.app.app_context(): before = digest()
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=rendered).status_code)
                with self.app.app_context(): self.assertEqual(before, digest())
                for k in LISTS:
                    rendered.poplist(k)  # Browser sends NO control for an empty multi-select.
                rendered[replacement] = ''
                self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=rendered).status_code)
                self.assertEqual({k: [] for k in LISTS} | {k: None for k in ORIGINS}, self.state(item_id))
                with self.app.app_context(): self.assertEqual(cited, targets())

    def test_historical_inactive_experts_and_withdrawn_documents_browser_roundtrip(self):
        """S3/S4: retained targets stay selected and survive a real browser submission."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        item_id = self.create(expert_ids=[2], document_ids=[1], retired=True, accion_revision_id=1)
        with self.app.app_context():
            db.session.execute(m.Person.__table__.update().where(m.Person.id == 2).values(activo=False))
            db.session.execute(m.Document.__table__.update().where(m.Document.id == 1).values(withdrawn_at=date(2020, 1, 1)))
            db.session.commit()
        form = self.browser(item_id)
        self.assertEqual(['2'], form.getlist('expert_ids'))
        self.assertEqual(['1'], form.getlist('document_ids'))
        self.assertEqual('1', form['accion_revision_id'])
        with self.app.app_context(): before = digest()
        self.assertEqual(302, self.client.post(f'/conocimientos/{item_id}/editar', data=form).status_code)
        with self.app.app_context(): self.assertEqual(before, digest())

    def test_web_rejections_and_role_denials_are_byte_identical(self):
        """S4/S5: malformed, nonexistent, conflicting and disallowed writes are atomic."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        item_id = self.create(process_ids=[1], no_conformidad_id=1)
        form = self.browser(item_id)
        for key in (*LISTS, *ORIGINS):
            for value in ('999', 'bad', '0', '-1'):
                bad = MultiDict(form)
                bad.setlist(key, [value])
                with self.app.app_context(): before = digest()
                result = self.client.post(f'/conocimientos/{item_id}/editar', data=bad)
                self.assertEqual(200, result.status_code)
                with self.app.app_context(): self.assertEqual(before, digest())
        bad = MultiDict(form)
        bad['hallazgo_id'] = '1'
        with self.app.app_context(): before = digest()
        result = self.client.post(f'/conocimientos/{item_id}/editar', data=bad)
        self.assertIn('Elige como máximo un registro de origen.', result.get_data(as_text=True))
        with self.app.app_context(): self.assertEqual(before, digest())
        for role in m.RoleEnum:
            if role in (m.RoleEnum.ADMINISTRADOR, m.RoleEnum.AUDITOR):
                continue
            self.login(role)
            for url in ('/conocimientos/nuevo', f'/conocimientos/{item_id}/editar'):
                with self.app.app_context(): before = digest()
                result = self.client.post(url, data=form)
                self.assertEqual((302, '/dashboard/'), (result.status_code, result.location))
                self.assertIn(('danger', 'No tienes permiso para acceder a esta página.'), self.flashes())
                with self.app.app_context(): self.assertEqual(before, digest())

    def test_show_hides_protected_document_and_origins(self):
        """S5: follow the existing QMS-change hidden-origin/document rendering rule."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        for origin in ('hallazgo_id', 'accion_revision_id'):
            item_id = self.create(document_ids=[1], **{origin: 1})
            self.login(m.RoleEnum.OPERATIVO)
            html = self.client.get(f'/conocimientos/{item_id}').get_data(as_text=True)
            for secret in ('DOC-1', 'RESTRICTED_DOC_1', 'RESTRICTED_FINDING_1', 'RESTRICTED_ACTION_1',
                           '/documents/1', '/auditorias/1', '/revisiones-direccion/1'):
                self.assertNotIn(secret, html)
            self.assertIn('Registro vinculado', html)
            self.login(m.RoleEnum.ADMINISTRADOR)

    def test_existing_delete_routes_refuse_cited_targets_without_500_or_writes(self):
        """S3/S4: a retired item still protects every target; baseline records delete normally."""
        cases = [
            ('process_ids', 1, '/procesos/1/eliminar', 'POST'),
            ('process_ids', 1, '/proceso_operacion/1', 'DELETE'),
            ('expert_ids', 2, '/personas/2/eliminar', 'POST'),
            ('training_ids', 1, '/capacitaciones/eliminar/1', 'POST'),
            ('requirement_ids', 1, '/competencias/requisitos/1/eliminar', 'POST'),
            ('no_conformidad_id', 1, '/no_conformidades/eliminar/1', 'POST'),
            ('hallazgo_id', 1, '/auditorias/1/hallazgos/1/eliminar', 'POST'),
            ('mejora_id', 1, '/mejoras/eliminar/1', 'POST'),
            ('accion_revision_id', 1, '/revisiones-direccion/1/acciones/1/eliminar', 'POST'),
        ]
        for key, target_id, url, method in cases:
            with self.subTest(key=key, url=url):
                if BASELINE and url == '/proceso_operacion/1':
                    with self.app.app_context():
                        db.session.execute(m.ProcesoOperacion.__table__.insert().values(id_proceso=1, proceso='Process 1'))
                        db.session.commit()
                if not BASELINE:
                    self.create(retired=True, **{key: [target_id] if key in LISTS else target_id})
                with self.app.app_context(): before = digest()
                # Testing propagates unhandled exceptions, making a 500 a test error.
                result = self.client.open(url, method=method)
                print('DELETE_PROBE', BASELINE, url, result.status_code, result.get_json(silent=True), self.flashes()[-1:])
                if BASELINE:
                    self.assertIn(result.status_code, (200, 302))
                    continue
                self.assertIn(result.status_code, (302, 409))
                if result.status_code == 409:
                    self.assertIn('message', result.get_json())
                else:
                    self.assertTrue(any(level == 'danger' for level, _ in self.flashes()))
                with self.app.app_context(): self.assertEqual(before, digest())


class IndependentLinksMcp(McpDbCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.app_config = {'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + storage.name + '/mcp.sqlite'}
        super().setUp()
        seed()

    async def test_mcp_set_change_clear_every_link_and_origin_and_audit(self):
        """S3/S4: omitted vs empty, duplicates, exact snapshots and untouched cited rows."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        cited = targets()
        created = await self.call(mcp_actor(), 'qms_create', {'module': 'knowledge', 'data': VALID})
        self.assertFalse(created.is_error, created.content)
        item_id = created.structured_content['id']
        for key in (*LISTS, *ORIGINS):
            for value in ([2, 1, 2], [3], []) if key in LISTS else (1, 2, None):
                expected = sorted(set(value)) if key in LISTS else value
                result = await self.call(mcp_actor(m.RoleEnum.AUDITOR), 'qms_update',
                    {'module': 'knowledge', 'id': item_id, 'data': {key: value}})
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(expected, result.structured_content[key])
                audit_row = m.AuditLog.query.order_by(m.AuditLog.id.desc()).first()
                self.assertEqual(expected, audit_row.after[key])
                before = digest()
                result = await self.call(mcp_actor(), 'qms_update',
                    {'module': 'knowledge', 'id': item_id, 'data': {key: value}})
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(before, digest())
        self.assertEqual(cited, targets())
        self.assertEqual({'knowledge_items'}, {r.entity_type for r in m.AuditLog.query.all()})
        # Execute the documentation's exact qms_update example against existing targets.
        example = {'module': 'knowledge', 'id': 1, 'data': {'process_ids': [2, 3], 'document_ids': [4],
            'expert_ids': [5], 'training_ids': [6], 'requirement_ids': [7], 'no_conformidad_id': 8}}
        result = await self.call(mcp_actor(), 'qms_update', example)
        self.assertFalse(result.is_error, result.content)
        for key, value in example['data'].items(): self.assertEqual(value, result.structured_content[key])
        result = await self.call(mcp_actor(), 'qms_update', {'module': 'knowledge', 'id': 1, 'data': {'retired': True}})
        for key, value in example['data'].items(): self.assertEqual(value, result.structured_content[key])
        self.assertEqual(cited, targets())

    async def test_mcp_rejections_and_all_nonwriting_roles_are_byte_identical(self):
        """S4/S5: exact MCP errors, empty/zero/unknown/malformed, create and update."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        self.seed('knowledge')
        cases = []
        for key in LISTS:
            for value in (None, '1', [True], [1.0], [None]):
                cases.append(({key: value}, f'El campo «{key}» debe ser una lista de identificadores.'))
            for value in (0, -1, 999):
                cases.append(({key: [value]}, f'El campo «{key}» contiene identificadores desconocidos: {value}.'))
        for key in ORIGINS:
            for value in (True, '1', 1.5):
                cases.append(({key: value}, f'El campo «{key}» debe ser un número entero.'))
            for value in (0, -1, 999):
                cases.append(({key: value}, f'El campo «{key}» no corresponde a ningún registro de origen.'))
        cases.append(({'hallazgo_id': 1, 'mejora_id': 1}, 'Elige como máximo un registro de origen.'))
        for bad, message in cases:
            for tool in ('qms_create', 'qms_update'):
                before = digest()
                args = {'module': 'knowledge', 'data': VALID | bad} if tool == 'qms_create' else {
                    'module': 'knowledge', 'id': 1, 'data': {'title': 'Must not change'} | bad}
                result = await self.call(mcp_actor(), tool, args)
                self.assertTrue(result.is_error, (bad, result.content))
                self.assertEqual(f'Error executing tool {tool}: {message}', result.content[0].text)
                self.assertEqual(before, digest())
        for role in m.RoleEnum:
            if role in (m.RoleEnum.ADMINISTRADOR, m.RoleEnum.AUDITOR): continue
            for tool in ('qms_create', 'qms_update'):
                before = digest()
                args = {'module': 'knowledge', 'data': VALID | {'process_ids': [1]}}
                if tool == 'qms_update': args['id'] = 1
                result = await self.call(mcp_actor(role), tool, args)
                self.assertTrue(result.is_error)
                self.assertEqual(f'Error executing tool {tool}: No tienes permiso para realizar esta acción.', result.content[0].text)
                self.assertEqual(before, digest())

    async def test_new_inactive_expert_and_withdrawn_document_links_are_atomic(self):
        """S4: new links reject unavailable targets without altering any data or audit."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        self.seed('knowledge')
        db.session.execute(m.Person.__table__.update().where(m.Person.id == 2).values(activo=False))
        db.session.execute(m.Document.__table__.update().where(m.Document.id == 1).values(withdrawn_at=date(2020, 1, 1)))
        db.session.commit()
        for key, value, message in (
            ('expert_ids', [2], 'La persona del campo «expert_ids» está desactivada; elige una persona activa.'),
            ('document_ids', [1], 'El documento del campo «document_ids» está dado de baja; elige uno en uso.'),
        ):
            before = digest()
            result = await self.call(mcp_actor(), 'qms_update', {'module': 'knowledge', 'id': 1,
                'data': {'title': 'Must not change', key: value}})
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_update: ' + message, result.content[0].text)
            self.assertEqual(before, digest())

    async def test_mcp_read_cannot_reveal_protected_target_content(self):
        """S5: raw FK ids follow existing MCP conventions, never expand protected content."""
        if BASELINE:
            self.skipTest('KN2 links do not exist at baseline')
        for origin, module in (('hallazgo_id', 'hallazgos_auditoria'), ('accion_revision_id', 'acciones_revision')):
            row = knowledge.items.create(db.session, ADMIN, VALID | {'document_ids': [1], origin: 1})
            db.session.commit()
            for role in m.RoleEnum:
                who = mcp_actor(role)
                for tool, args in (('qms_get', {'module': 'knowledge', 'id': row.id}),
                                   ('qms_list', {'module': 'knowledge'})):
                    result = await self.call(who, tool, args)
                    self.assertFalse(result.is_error)
                    for secret in ('RESTRICTED_DOC_1', 'RESTRICTED_FINDING_1', 'RESTRICTED_ACTION_1'):
                        self.assertNotIn(secret, str(result.structured_content))
                if role not in (m.RoleEnum.ADMINISTRADOR, m.RoleEnum.AUDITOR):
                    self.assertTrue((await self.call(who, 'qms_get', {'module': module, 'id': 1})).is_error)
                    denied = await self.call(who, 'qms_get', {'module': 'documentos', 'id': 1})
                    self.assertTrue(denied.is_error)


class IndependentLinksMigration(unittest.TestCase):
    def test_additive_schema_data_indexes_and_downgrade(self):
        """S4: old rows/schema preserved; named FK/check/index objects match the contract."""
        if BASELINE:
            self.skipTest('KN2 migration does not exist at baseline')
        def load(filename):
            spec = importlib.util.spec_from_file_location('independent_migration',
                Path('migrations/versions') / filename)
            result = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(result)
            return result
        base = load('e8a0c2d4f6b8_add_organizational_knowledge.py')
        new = load('f9b1d3e5a7c9_add_knowledge_links.py')
        self.assertEqual('e8a0c2d4f6b8', new.down_revision)
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text('PRAGMA foreign_keys=ON'))
            old_tables = [t for t in db.metadata.sorted_tables if not t.name.startswith('knowledge_')]
            db.metadata.create_all(conn, tables=old_tables)
            operations = Operations(MigrationContext.configure(conn))
            with patch.object(base, 'op', operations): base.upgrade()
            conn.execute(m.Person.__table__.insert().values(nombre='Existing owner'))
            conn.execute(text("INSERT INTO knowledge_items (kind,title,summary,source,owner_id,next_review_date,retired) "
                              "VALUES ('leccion','Preserved','Existing summary','Manual source',1,'2020-01-01',1)"))
            def unrelated_schema():
                return conn.execute(text("SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE tbl_name NOT LIKE 'knowledge_%' ORDER BY type,name")).all()
            def data():
                return {t.name: conn.execute(t.select()).all() for t in old_tables}
            columns = [c['name'] for c in inspect(conn).get_columns('knowledge_items')]
            before = conn.execute(text('SELECT * FROM knowledge_items')).all()
            schema, rows = unrelated_schema(), data()
            with patch.object(new, 'op', operations):
                new.upgrade()
                self.assertEqual(schema, unrelated_schema())
                self.assertEqual(rows, data())
                projection = ','.join(columns)
                self.assertEqual(before, conn.execute(text('SELECT ' + projection + ' FROM knowledge_items')).all())
                inspector = inspect(conn)
                for key in ORIGINS:
                    fks = {fk['name']: fk for fk in inspector.get_foreign_keys('knowledge_items')}
                    self.assertEqual('RESTRICT', fks[f'fk_knowledge_items_{key}']['options']['ondelete'])
                    self.assertEqual('RESTRICT', fks[f'fk_knowledge_items_{key}']['options']['onupdate'])
                    self.assertIn(f'ix_knowledge_items_{key}', {i['name'] for i in inspector.get_indexes('knowledge_items')})
                self.assertIn('ck_knowledge_items_one_origin', {c['name'] for c in inspector.get_check_constraints('knowledge_items')})
                for table in ('knowledge_processes', 'knowledge_documents', 'knowledge_experts',
                              'knowledge_trainings', 'knowledge_requirements'):
                    self.assertEqual(f'pk_{table}', inspector.get_pk_constraint(table)['name'])
                    self.assertEqual(['knowledge_id', 'target_id'], inspector.get_pk_constraint(table)['constrained_columns'])
                    self.assertIn(f'ix_{table}_target_id', {i['name'] for i in inspector.get_indexes(table)})
                    for fk in inspector.get_foreign_keys(table):
                        self.assertTrue(fk['name'].startswith(f'fk_{table}_'))
                        self.assertEqual({'ondelete': 'RESTRICT', 'onupdate': 'RESTRICT'}, fk['options'])
                new.downgrade()
                self.assertEqual(schema, unrelated_schema())
                self.assertEqual(rows, data())
                self.assertEqual(before, conn.execute(text('SELECT * FROM knowledge_items')).all())
                self.assertEqual(columns, [c['name'] for c in inspect(conn).get_columns('knowledge_items')])
                self.assertEqual({'knowledge_items'}, {t for t in inspect(conn).get_table_names() if t.startswith('knowledge_')})
