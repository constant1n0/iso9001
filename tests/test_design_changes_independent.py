"""Independent DD4 regressions: S4 versions/changes and S5 authority/closure."""
import io
import unittest

from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

import test_design_changes as fixtures
from test_communication_independent import BrowserForm, hashes
from test_design import TODAY, actor
from app.extensions import db
from app.models import Person, RoleEnum
from app.services import design, design_changes as changes, design_io, people
from app.services.errors import PermissionDenied, ValidationError
import mcp_support


class IndependentChanges(fixtures.ChangeCase):
    def start(self):
        self.stage(self.project)
        for target in ('planificado', 'en_curso'):
            design.transition(db.session, self.who, self.project.id, target)
        db.session.commit()

    def prepared(self):
        output = self.approve(self.output())
        change = self.authorize(self.propose(output_ids=[output.id]))
        return output, change

    def test_s4_s5_authorized_unapplied_change_must_not_be_stranded_by_completion(self):
        self.start()
        output, change = self.prepared()
        self.assertFalse(changes.is_applied(db.session, change.id))
        before = hashes()
        try:
            design.complete(db.session, self.admin, self.project.id, today=TODAY)
        except ValidationError:
            self.assertEqual(before, hashes())
            db.session.rollback()
            return
        db.session.commit()
        self.assertEqual('completado', self.project.state.name)
        self.assertEqual([output.id], [v.output_id for v in
            changes.current_versions(db.session, self.project.id)])
        self.reject(lambda: changes.apply(db.session, self.admin, change.id),
                    'Un proyecto completado es de solo lectura.')
        self.fail('Completion stranded an authorized change without creating its versions')

    def test_s4_competing_authorizations_are_atomic_for_multiple_lineages(self):
        first, second = [self.approve(self.output()) for _ in range(2)]
        multi = self.authorize(self.propose(output_ids=[first.id, second.id]))
        competing = self.authorize(self.propose(output_ids=[second.id]))
        changes.apply(db.session, self.admin, competing.id)
        db.session.commit()
        self.reject(lambda: changes.apply(db.session, self.admin, multi.id),
                    'Elige al menos una salida aprobada actual del mismo proyecto.')
        self.assertFalse(changes.is_applied(db.session, multi.id))
        self.assertEqual([first.id], [r.id for r in
            changes.history(db.session, self.who, first.id)])

    def test_s4_history_is_readable_by_every_role_and_original_is_frozen(self):
        original, change = self.prepared()
        new = changes.apply(db.session, self.admin, change.id)[0]
        design_io.outputs.update(db.session, self.who, new.id, {'text': 'Replacement'})
        db.session.commit()
        self.assertEqual((2, original.id, 'draft'),
                         (new.version, new.lineage_id, new.approval_state.name))
        for role in RoleEnum:
            self.assertEqual([original.id, new.id], [r.id for r in
                changes.history(db.session, actor(role), new.id)])
        for channel in ('web', 'mcp'):
            self.reject(lambda: design_io.outputs.update(db.session, actor(channel=channel),
                original.id, {'text': original.text}), fixtures.FROZEN)
        self.reject(lambda: design_io.outputs.delete(db.session, self.who, original.id),
                    fixtures.FROZEN)
        self.reject(lambda: design_io.outputs.delete(db.session, self.who, new.id),
                    'Las versiones creadas por cambios no se eliminan.')

    def test_s4_empty_zero_negative_malformed_and_duplicate_affected_ids(self):
        output = self.approve(self.output())
        for ids in ([], [0], [-1], [True], ['1'], [None], None):
            self.reject(lambda: self.propose(output_ids=ids),
                        'Elige al menos una salida aprobada actual del mismo proyecto.')
        change = self.propose(output_ids=[output.id, output.id])
        self.assertEqual([output.id], change.output_ids)
        for key in ('reason', 'impact', 'actions'):
            self.reject(lambda: self.propose(output_ids=[output.id], **{key: '  '}),
                        f'El campo «{key}» es obligatorio.')
        pending = self.propose(output_ids=[output.id], review='  ')
        self.reject(lambda: self.authorize(pending), 'Revisa el cambio antes de autorizarlo.')

    def test_s5_relinked_user_and_changed_owner_rechecked_with_warm_identity_map(self):
        output = self.output()
        cached_person = db.session.get(Person, self.approver_id)
        self.assertEqual(self.admin.user_id, cached_person.user_id)
        with Session(db.engine) as other:
            people.update(other, self.who, self.approver_id, {'user_id': None})
            people.update(other, self.who, self.project.owner_id,
                          {'user_id': self.admin.user_id})
            other.commit()
        self.reject(lambda: changes.approve_output(db.session, self.admin, output.id,
                                                   today=TODAY), fixtures.SEPARATION)
        with Session(db.engine) as other:
            people.update(other, self.who, self.project.owner_id, {'user_id': None})
            other.commit()
        self.reject(lambda: changes.approve_output(db.session, self.admin, output.id,
                                                   today=TODAY), fixtures.NO_PERSON)

    def test_s4_s5_apply_permissions_all_non_web_actors_and_operative(self):
        _, change = self.prepared()
        for role in RoleEnum:
            self.reject(lambda: changes.apply(db.session, actor(role, channel='mcp'),
                                               change.id), error=PermissionDenied)
        self.reject(lambda: changes.apply(db.session, actor(RoleEnum.OPERATIVO),
                                           change.id), error=PermissionDenied)

    def test_s4_s5_rendered_noops_and_dropped_fields_keep_database_hashes(self):
        self.login(RoleEnum.ADMINISTRADOR)
        output = self.output(text='\nPlano <A> & B\n', acceptance_criteria='\nPrueba\n')
        base = f'/diseno/{self.project.id}'
        for path in (base + '/editar', base + f'/salidas/{output.id}/editar'):
            form = BrowserForm()
            form.feed(self.client.get(path).text)
            before = hashes()
            self.assertEqual(302, self.client.post(path, data=form.values).status_code)
            self.assertEqual(before, hashes())
        self.approve(output)
        change = self.propose(output_ids=[output.id])
        path = base + f'/cambios/{change.id}/editar'
        form = BrowserForm()
        form.feed(self.client.get(path).text)
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=form.values).status_code)
        self.assertEqual(before, hashes())
        for extra, message in (({'project_id': '999'}, 'Un elemento no puede pasar a otro proyecto.'),
                               ({'state': 'authorized'}, 'Campos no permitidos: state.'),
                               ({'review': (io.BytesIO(b'file'), 'review.txt')},
                                'Campos no permitidos: review.')):
            before = hashes()
            data = MultiDict(form.values)
            for key, value in extra.items():
                data[key] = value
            response = self.client.post(path, data=data)
            self.assertEqual(400, response.status_code)
            self.assertIn(message, response.text)
            self.assertEqual(before, hashes())

    def test_s5_completed_web_routes_refuse_dd1_dd4_writes(self):
        self.start()
        output = self.approve(self.output())
        self.login(RoleEnum.ADMINISTRADOR)
        design.complete(db.session, self.admin, self.project.id, today=TODAY)
        db.session.commit()
        base = f'/diseno/{self.project.id}'
        calls = [('/editar', {'title': 'Changed', 'description': 'P', 'owner_id': '1'}),
                 ('/etapas/nueva', {'name': 'Late', 'owner_id': '1'}),
                 ('/etapas/1/editar', {'name': 'Late', 'owner_id': '1', 'position': '1'}),
                 ('/etapas/1/eliminar', {}), ('/eliminar', {}),
                 ('/transicion', {'target': 'cancelado', 'cancellation_reason': 'No'}),
                 ('/entradas/nuevo', {'kind': 'other', 'text': 'Late'}),
                 ('/salidas/nuevo', {'kind': 'other', 'text': 'Late', 'acceptance_criteria': 'OK'}),
                 (f'/salidas/{output.id}/editar', {'kind': 'other', 'text': 'Late',
                                                 'acceptance_criteria': 'OK'}),
                 (f'/salidas/{output.id}/eliminar', {}),
                 (f'/salidas/{output.id}/aprobar', {}), ('/completar', {}),
                 ('/controles/planes/nuevo', {'kind': 'review', 'description': 'Late',
                                               'responsible_id': '1'}),
                 ('/controles/registro/nuevo', {'kind': 'review', 'date': TODAY.isoformat(),
                    'responsible_id': '1', 'result': 'conforming', 'evidence': 'Late'}),
                 ('/cambios/nuevo', fixtures.CHANGE | {'output_ids': str(output.id)})]
        for suffix, data in calls:
            with self.subTest(suffix=suffix):
                before = hashes()
                response = self.client.post(base + suffix, data=data)
                self.assertIn(response.status_code, (200, 302))
                self.assertEqual(before, hashes())


class IndependentMcp(mcp_support.McpDbCase):
    async def test_s4_s5_completed_mcp_and_explicit_approval_fields(self):
        self.seed('design_projects')
        self.seed('design_stages')
        oid = self.seed('design_outputs')
        self.approve_design_output(oid)
        output = db.session.get(changes.DesignOutput, oid)
        person = db.session.get(Person, output.approved_by_id)
        admin = fixtures.Actor(person.user_id, 'Approver', RoleEnum.ADMINISTRADOR, 'web')
        for target in ('planificado', 'en_curso'):
            design.transition(db.session, admin, 1, target)
        db.session.commit()
        who = mcp_support.mcp_actor()
        for module, data in (('design_projects', {'completed_by_id': person.id}),
                             ('design_outputs', {'approved_by_id': person.id})):
            before = hashes()
            result = await self.call(who, 'qms_update',
                                     {'module': module, 'id': 1, 'data': data})
            key = next(iter(data))
            self.assertTrue(result.is_error)
            self.assertEqual(f'Error executing tool qms_update: Campos no permitidos: {key}.',
                             result.content[0].text)
            self.assertEqual(before, hashes())
        design.complete(db.session, admin, 1, today=TODAY)
        db.session.commit()
        for module in ('design_projects', 'design_stages', 'design_outputs'):
            before = hashes()
            result = await self.call(who, 'qms_update',
                                     {'module': module, 'id': 1, 'data': {}})
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_update: Un proyecto completado es de solo lectura.',
                             result.content[0].text)
            self.assertEqual(before, hashes())
        before = hashes()
        result = await self.call(who, 'qms_create', {'module': 'design_changes', 'data':
            fixtures.CHANGE | {'project_id': 1, 'output_ids': [oid]}})
        self.assertTrue(result.is_error)
        self.assertEqual('Error executing tool qms_create: Un proyecto completado es de solo lectura.',
                         result.content[0].text)
        self.assertEqual(before, hashes())


if __name__ == '__main__':
    unittest.main()
