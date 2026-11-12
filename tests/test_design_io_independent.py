"""Independent DD2 probes: S3 and S5 (inputs/outputs only)."""
import json
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from register_routes import RegisterRoutesBase
from test_communication_independent import BrowserForm, hashes
from test_design import actor, TODAY
from mcp_support import McpDbCase, mcp_actor
from app.extensions import db
from app.models import RoleEnum
from app.services import design, people, documents, document_revisions

PROJECT = dict(title='Independent design', description='Reusable packaging', owner_id=1)
INPUTS = ('functional_performance', 'legal_regulatory', 'prior_knowledge',
          'failure_consequences', 'other')
OUTPUTS = ('specification', 'drawing', 'procedure_instruction',
           'prototype_sample', 'software', 'other')
DATA = {
    'entradas': dict(kind='prior_knowledge', text='Prior trial <A> & B'),
    'salidas': dict(kind='software', text='Controller <A> & B',
                    acceptance_criteria='Response below 100 ms'),
}
INVALID = 'Elige una revisión aprobada o vigente que puedas consultar.'


def revision(who, publish=False):
    approver = people.create(db.session, who, {'nombre': 'Independent approver'})
    doc = documents.create(db.session, who, dict(
        title='PRIVATE TITLE', code='PRIVATE-CODE', category='OTRO', owner_id=1,
        author_id=1, content='PRIVATE CONTENT', change_summary='Initial'))
    row = document_revisions.list_(db.session, who, doc.id)[0]
    document_revisions.submit(db.session, who, row.id)
    document_revisions.approve(db.session, who, row.id,
                              approver_id=approver.id, today=TODAY)
    if publish:
        document_revisions.publish(db.session, who, row.id, today=TODAY)
    db.session.commit()
    return doc.id, row.id


class IndependentWeb(RegisterRoutesBase):
    def setUp(self):
        super().setUp()
        self.seed_with(people, {'nombre': 'Independent owner'})
        self.login(RoleEnum.ADMINISTRADOR)
        for _ in range(2):
            self.assertEqual(302, self.client.post('/diseno/nuevo', data=PROJECT).status_code)

    def state(self):
        with self.app.app_context():
            return hashes()

    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.text)
        return parser.values

    def test_s3_uncited_revisions_are_not_a_general_revision_reader(self):
        with self.app.app_context():
            doc, effective = revision(actor(), publish=True)
            draft = document_revisions.start_draft(db.session, actor(), doc,
                                                    {'author_id': 1}).id
            db.session.commit()
        for role in RoleEnum:
            self.login(role)
            for rid in (effective, draft):
                with self.subTest(role=role.name, revision=rid):
                    before = self.state()
                    response = self.client.get(f'/diseno/revisiones/{rid}')
                    self.assertEqual(before, self.state())
                    self.assertEqual(404, response.status_code)

    def test_s3_explicit_parent_cannot_be_silently_dropped(self):
        for section, data in DATA.items():
            self.assertEqual(302, self.client.post(
                f'/diseno/1/{section}/nuevo', data=data).status_code)
            for suffix in ('nuevo', '1/editar'):
                with self.subTest(section=section, suffix=suffix):
                    self.client.get('/diseno/1')  # consume setup/create flashes
                    before = self.state()
                    response = self.client.post(f'/diseno/1/{section}/{suffix}',
                                                data=data | {'project_id': '2'})
                    flashes = self.flashes()
                    print('EXPLICIT_PARENT', section, suffix, response.status_code, flashes)
                    success = any(level == 'success' for level, _ in flashes)
                    self.assertEqual((before, False), (self.state(), success))
                    self.assertIn(response.status_code, (200, 400))
                self.client.get('/diseno/1')  # consume prior flashes

    def test_s3_s5_obsolete_citation_roundtrip_privacy_and_parent_delete(self):
        with self.app.app_context():
            doc, rid = revision(actor(), publish=True)
        for section, data in DATA.items():
            self.assertEqual(302, self.client.post(f'/diseno/1/{section}/nuevo',
                data=data | {'revision_id': str(rid)}).status_code)
        self.assertIn('PRIVATE CONTENT', self.client.get(f'/diseno/revisiones/{rid}').text)
        before = self.state()
        response = self.client.post('/diseno/1/eliminar')
        self.assertEqual(302, response.status_code)
        self.assertIn(('danger', 'No se puede eliminar un proyecto con entradas o salidas.'),
                      self.flashes())
        self.assertEqual(before, self.state())
        with self.app.app_context():
            document_revisions.withdraw(db.session, actor(), doc, 'Retired', today=TODAY)
            db.session.commit()
        for section in DATA:
            path = f'/diseno/1/{section}/1/editar'
            form = self.form(path)
            self.assertEqual([str(rid)], form.getlist('revision_id'))
            before = self.state()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, self.state())
        self.login(RoleEnum.OPERATIVO)
        page = self.client.get('/diseno/1').text
        self.assertIn('Revisión vinculada', page)
        for secret in ('PRIVATE TITLE', 'PRIVATE-CODE', 'PRIVATE CONTENT'):
            self.assertNotIn(secret, page)
        self.assertEqual(404, self.client.get(f'/diseno/revisiones/{rid}').status_code)
        for section, data in DATA.items():
            for suffix in ('nuevo', '1/editar', '1/eliminar'):
                before = self.state()
                self.assertEqual(302, self.client.post(
                    f'/diseno/1/{section}/{suffix}', data=data).status_code)
                self.assertEqual(before, self.state())

    def test_s3_parent_lock_precedes_content_validation(self):
        from app.services import design_io
        with self.app.app_context():
            sql = []
            def capture(state):
                if state.is_select:
                    sql.append(str(state.statement.compile(dialect=postgresql.dialect())))
            event.listen(db.session(), 'do_orm_execute', capture)
            self.addCleanup(event.remove, db.session(), 'do_orm_execute', capture)
            original = design_io._values
            def checked(*args, **kwargs):
                self.assertTrue(any('FROM design_projects' in s and 'FOR UPDATE' in s
                                    for s in sql), sql)
                return original(*args, **kwargs)
            with patch.object(design_io, '_values', side_effect=checked):
                for name, data in (('inputs', DATA['entradas']), ('outputs', DATA['salidas'])):
                    register = getattr(design_io, name)
                    sql.clear()
                    row = register.create(db.session, actor(), data | {'project_id': 1})
                    db.session.commit()
                    for change in ({'text': 'Changed'}, {'text': '   '}):
                        sql.clear()
                        before = hashes()
                        if change['text'].strip():
                            register.update(db.session, actor(), row.id, change)
                            db.session.commit()
                        else:
                            from app.services.errors import ValidationError
                            with self.assertRaisesRegex(ValidationError, 'es obligatorio'):
                                register.update(db.session, actor(), row.id, change)
                            db.session.rollback()
                            self.assertEqual(before, hashes())
                    sql.clear()
                    register.delete(db.session, actor(), row.id)
                    self.assertTrue(any('FROM design_projects' in s and 'FOR UPDATE' in s
                                        for s in sql))
                    db.session.commit()


    def test_s3_migration_roundtrip_preserves_dd1_and_has_indexed_named_keys(self):
        import importlib.util
        from pathlib import Path
        from sqlalchemy import inspect
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from app.models import DesignInput, DesignOutput
        path = Path(__file__).resolve().parents[1] / 'migrations/versions'
        path /= 'd3f5a7b9c1e3_add_design_inputs_outputs.py'
        spec = importlib.util.spec_from_file_location('independent_dd2_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('c2e4f6a8b0d2', migration.down_revision)
        with self.app.app_context():
            design.stages.create(db.session, actor(), {'project_id': 1, 'name': 'Trial',
                                                       'owner_id': 1}, today=TODAY)
            db.session.commit()
            db.session.remove()
            db.metadata.drop_all(db.engine, tables=[DesignOutput.__table__, DesignInput.__table__])
            with db.engine.begin() as connection:
                legacy = sorted(inspect(connection).get_table_names())
                def snapshot():
                    return repr([(t, connection.exec_driver_sql(f'SELECT * FROM "{t}"').all())
                                 for t in legacy]).encode()
                before = snapshot()
                with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                    self.assertEqual(before, snapshot())
                    for table in ('design_inputs', 'design_outputs'):
                        info = inspect(connection)
                        self.assertTrue(info.get_pk_constraint(table)['name'])
                        self.assertIn('ck_' + table + '_kind',
                                      {c['name'] for c in info.get_check_constraints(table)})
                        indexed = [i['column_names'] for i in info.get_indexes(table)]
                        for fk in info.get_foreign_keys(table):
                            self.assertTrue(fk['name'])
                            self.assertIn(fk['constrained_columns'], indexed)
                            if fk['referred_table'] != 'users':
                                self.assertEqual('RESTRICT', fk['options']['ondelete'])
                                self.assertEqual('RESTRICT', fk['options']['onupdate'])
                    migration.downgrade()
                    self.assertEqual(legacy, sorted(inspect(connection).get_table_names()))
                    self.assertEqual(before, snapshot())


class IndependentMcp(McpDbCase):
    async def test_s3_documented_example_filters_and_strict_negative_inputs(self):
        self.seed('design_projects')
        example = {'module': 'design_outputs', 'data': {
            'project_id': 1, 'kind': 'specification', 'text': 'Especificación del envase',
            'acceptance_criteria': 'Capacidad de 500 ml ± 5 ml'}}
        result = await self.call(mcp_actor(), 'qms_create', example)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(example['data']['acceptance_criteria'],
                         result.structured_content['acceptance_criteria'])
        _, rid = revision(self.cli())
        for module, types, data in (('design_inputs', INPUTS, DATA['entradas']),
                                   ('design_outputs', OUTPUTS, DATA['salidas'])):
            for kind in types:
                result = await self.call(mcp_actor(), 'qms_create', {
                    'module': module, 'data': data | {'project_id': 1, 'kind': kind,
                                                     'revision_id': rid}})
                self.assertFalse(result.is_error, result.content)
                item = result.structured_content['id']
                listed = await self.call(mcp_actor(), 'qms_list', {
                    'module': module, 'filters': {'project_id': 1, 'kind': kind}})
                self.assertIn(item, [row['id'] for row in listed.structured_content['items']])
            for key, value, message in (
                ('kind', 'otro', 'El valor del campo «kind» no es válido.'),
                ('text', ' \n ', 'El campo «text» es obligatorio.'),
                ('revision_id', 0, INVALID), ('revision_id', -1, INVALID),
                ('revision_id', 99999, INVALID),
                ('project_id', 2, 'Un elemento no puede pasar a otro proyecto.')):
                before = hashes()
                result = await self.call(mcp_actor(), 'qms_update', {
                    'module': module, 'id': item, 'data': {key: value}})
                self.assertTrue(result.is_error)
                self.assertEqual('Error executing tool qms_update: ' + message,
                                 result.content[0].text)
                self.assertEqual(before, hashes())
            before = hashes()
            result = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_get', {
                'module': module, 'id': item})
            self.assertFalse(result.is_error)
            for secret in ('PRIVATE TITLE', 'PRIVATE-CODE', 'PRIVATE CONTENT'):
                self.assertNotIn(secret, json.dumps(result.structured_content))
            denied = await self.call(mcp_actor(RoleEnum.OPERATIVO), 'qms_update', {
                'module': module, 'id': item, 'data': {'text': 'Denied'}})
            self.assertTrue(denied.is_error)
            self.assertEqual(before, hashes())
        self.assertNotIn('qms_delete', {tool.name for tool in await self.list_tools()})

    async def test_s3_s5_draft_unreadable_and_cancelled_writes_are_atomic(self):
        self.seed('design_projects')
        doc, rid = revision(self.cli(), publish=True)
        draft = document_revisions.start_draft(db.session, self.cli(), doc,
                                                {'author_id': 1}).id
        db.session.commit()
        for module, data in (('design_inputs', DATA['entradas']),
                             ('design_outputs', DATA['salidas'])):
            for who, revision_id in ((mcp_actor(), draft),
                                      (mcp_actor(scopes=('write',)), rid)):
                before = hashes()
                result = await self.call(who, 'qms_create', {
                    'module': module, 'data': data | {'project_id': 1,
                                                     'revision_id': revision_id}})
                self.assertTrue(result.is_error)
                self.assertEqual('Error executing tool qms_create: ' + INVALID,
                                 result.content[0].text)
                self.assertEqual(before, hashes())
            result = await self.call(mcp_actor(), 'qms_create', {
                'module': module, 'data': data | {'project_id': 1}})
            self.assertFalse(result.is_error)
        design.transition(db.session, actor(), 1, 'cancelado', cancellation_reason='Stop')
        db.session.commit()
        for module, data in (('design_inputs', DATA['entradas']),
                             ('design_outputs', DATA['salidas'])):
            for tool, args in (('qms_create', {'data': data | {'project_id': 1}}),
                               ('qms_update', {'id': 1, 'data': {'text': 'Changed'}})):
                before = hashes()
                result = await self.call(mcp_actor(), tool, {'module': module} | args)
                self.assertTrue(result.is_error)
                self.assertEqual(f'Error executing tool {tool}: ' + design.READ_ONLY,
                                 result.content[0].text)
                self.assertEqual(before, hashes())
