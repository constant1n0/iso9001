"""Improvement HTML routes as thin adapters; the JSON API is left to QF-8."""

from __future__ import annotations

import unittest
from datetime import date
from functools import partial
from types import SimpleNamespace

from register_routes import RegisterRoutesBase

from app.models import Mejora, RoleEnum
from app.services import improvements

DAY = date(2026, 10, 5)
FORM = {"descripcion": "Etiqueta ilegible", "fecha_identificacion": DAY.isoformat(),
        "origen": "otro", "beneficio": ""}
SEED_SERVICE = SimpleNamespace(create=partial(improvements.create, today=DAY))


class ImprovementRoutesTestCase(RegisterRoutesBase):
    BASE = "/mejoras"

    def test_create_edit_and_delete_are_audited_with_the_same_flashes(self) -> None:
        self.login()
        self.assertEqual(302, self.client.post(f"{self.BASE}/nueva", data=FORM).status_code)
        self.assertIn(("success", "Mejora registrada exitosamente"), self.flashes())
        with self.app.app_context():
            created = Mejora.query.one()
            self.assertEqual((None, self.ids[RoleEnum.OPERATIVO]), (created.beneficio, created.created_by_id))
            self.assertIsNone(created.fecha_implementacion)
            record_id = created.id_mejora
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM)
        self.assertEqual(["create"], self.actions())  # unchanged edit writes nothing
        self.client.post(f"{self.BASE}/editar/{record_id}", data=FORM | {"beneficio": "Cambiar"})
        self.assertIn(("success", "Mejora actualizada exitosamente"), self.flashes())
        self.login(RoleEnum.ADMINISTRADOR)  # only administrators delete
        self.client.post(f"{self.BASE}/eliminar/{record_id}")
        self.assertIn(("success", "Mejora eliminada correctamente"), self.flashes())
        self.assertEqual(["create", "update", "delete"], self.actions())
        self.assertEqual(404, self.client.post(f"{self.BASE}/eliminar/{record_id}").status_code)

    def test_invalid_form_rerenders_without_writing(self) -> None:
        self.login()
        self.assertEqual(200, self.client.post(f"{self.BASE}/nueva", data=FORM | {"descripcion": ""}).status_code)
        self.assertEqual(0, self.count(Mejora))

    def test_web_lifecycle_and_detail_preserve_every_role_write_access(self):
        from unittest.mock import patch
        from app.extensions import db
        from app.services import people
        from app.services.actor import Actor

        with self.app.app_context():
            for role, uid in self.ids.items():
                people.create(db.session, Actor(None, 'seed', RoleEnum.ADMINISTRADOR, 'system'),
                              {'nombre': role.name, 'user_id': uid})
            db.session.commit()
        for role in RoleEnum:
            self.login(role)
            with patch('app.routes.mejora_routes.local_today', return_value=DAY):
                self.assertEqual(302, self.client.post('/mejoras/nueva', data=FORM).status_code)
                with self.app.app_context():
                    record_id = Mejora.query.order_by(Mejora.id_mejora.desc()).first().id_mejora
                page = f'/mejoras/{record_id}'
                html = self.client.get(page).get_data(as_text=True)
                self.assertIn(f'action="{page}/aprobar"', html)
                self.assertEqual(302, self.client.post(f'{page}/aprobar').status_code)
                self.assertEqual(302, self.client.post(f'{page}/implantar', data={
                    'fecha_implementacion': DAY.isoformat()}).status_code)
                self.assertEqual(302, self.client.post(f'{page}/eficacia', data={
                    'eficacia': 'eficaz', 'fecha_eficacia': DAY.isoformat()}).status_code)
                with self.app.app_context():
                    found = db.session.get(Mejora, record_id)
                    self.assertEqual(('eficacia_revisada', DAY, DAY),
                                     (found.estado.name, found.fecha_implementacion, found.fecha_eficacia))
                self.assertNotIn(f'action="{page}/aprobar"', self.client.get(page).get_data(as_text=True))

    def test_contextual_origin_picker_and_cancellation(self):
        self.login()
        self.assertNotIn('name="no_conformidad_id"',
                         self.client.get('/mejoras/nueva').get_data(as_text=True))
        response = self.client.post('/mejoras/nueva', data=FORM | {
            'origen': 'no_conformidad', 'elegir_origen': '1'})
        self.assertEqual(200, response.status_code)
        self.assertIn('name="no_conformidad_id"', response.get_data(as_text=True))
        self.assertEqual(0, self.count(Mejora))
        self.client.post('/mejoras/nueva', data=FORM)
        response = self.client.post('/mejoras/1/cancelar', data={'motivo_cancelacion': 'Duplicada'})
        self.assertEqual(302, response.status_code)
        self.assertIn(('success', 'Mejora cancelada.'), self.flashes())
        self.assertEqual('cancelada', self.fetch(Mejora, 1).estado.name)
        for endpoint in ('aprobar', 'implantar', 'eficacia', 'cancelar'):
            self.assertEqual(405, self.client.get(f'/mejoras/1/{endpoint}').status_code)

    def test_pagination_keeps_page_and_size_arguments(self) -> None:
        for n in range(12):
            self.seed_with(SEED_SERVICE, {"descripcion": f"Hallazgo{n:02d}", "fecha_identificacion": DAY})
        self.login()

        def get(query=""):
            return self.client.get(f"{self.BASE}/?{query}").get_data(as_text=True)

        first, second = get(), get("page=2")
        self.assertIn("Hallazgo09", first)
        self.assertNotIn("Hallazgo10", first)
        self.assertIn("Página 1 de 2", first)
        self.assertIn("Hallazgo11", second)
        self.assertIn("Página 2 de 2", second)
        self.assertIn("Hallazgo04", get("per_page=5&page=1"))
        self.assertNotIn("Hallazgo05", get("per_page=5&page=1"))
        self.assertNotIn("Hallazgo00", get("page=99"))  # out of range: empty page, no error
        self.assertIn("Hallazgo00", get("page=0"))  # below 1 behaves as page 1


class ImprovementLifecycleCsrfTestCase(RegisterRoutesBase):
    def test_each_lifecycle_post_requires_the_token_rendered_on_its_form(self):
        from test_delete_forms import _PostForms
        from app.services import people

        self.app.config['WTF_CSRF_ENABLED'] = True
        self.seed_with(people, {'nombre': 'Operator', 'user_id': self.ids[RoleEnum.OPERATIVO]})
        self.login()
        record_id = self.seed_with(SEED_SERVICE, {
            'descripcion': 'Mejora', 'fecha_identificacion': DAY})
        base = f'/mejoras/{record_id}'
        steps = (
            ('aprobar', {}, 'aprobada'),
            ('implantar', {'fecha_implementacion': '2026-11-01'}, 'implantada'),
            ('eficacia', {'eficacia': 'eficaz', 'fecha_eficacia': '2026-11-01'}, 'eficacia_revisada'),
        )
        # Use one fixed adapter date so the test does not depend on the wall clock.
        from unittest.mock import patch
        with patch('app.routes.mejora_routes.local_today', return_value=date(2026, 11, 1)):
            for endpoint, data, state in steps:
                page = _PostForms()
                page.feed(self.client.get(base).get_data(as_text=True))
                form = next(f for f in page.forms if f['action'] == f'{base}/{endpoint}')
                before = self.actions()
                self.assertEqual(400, self.client.post(form['action'], data=data).status_code)
                self.assertEqual(before, self.actions())
                self.assertTrue(form['fields']['csrf_token'])
                self.assertEqual(302, self.client.post(
                    form['action'], data=data | {'csrf_token': form['fields']['csrf_token']}).status_code)
                self.assertEqual(state, self.fetch(Mejora, record_id).estado.name)
            cancelled_id = self.seed_with(SEED_SERVICE, {
                'descripcion': 'Duplicada', 'fecha_identificacion': DAY})
            page = _PostForms()
            page.feed(self.client.get(f'/mejoras/{cancelled_id}').get_data(as_text=True))
            form = next(f for f in page.forms if f['action'] == f'/mejoras/{cancelled_id}/cancelar')
            data = {'motivo_cancelacion': 'Duplicada'}
            self.assertEqual(400, self.client.post(form['action'], data=data).status_code)
            self.assertEqual(302, self.client.post(form['action'], data=data | {
                'csrf_token': form['fields']['csrf_token']}).status_code)
            self.assertEqual('cancelada', self.fetch(Mejora, cancelled_id).estado.name)


if __name__ == "__main__":
    unittest.main()
