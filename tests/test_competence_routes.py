"""Competence screens (QP-4): requirements per role and records of a person.

Thin adapters over ``app.services.competence`` under ``/competencias``. The
``COMPETENCE`` grant (decision Q1) lets every role read, administrators and
auditors create and edit, and only administrators delete. Records are reached
from the person's page (``/personas/<id>``) and return there. Service errors
are flashed on the re-rendered form, which keeps what was typed.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from test_people_routes import CsrfRoutesBase, PeopleRoutesBase
from test_user_routes import _post_forms

from app.extensions import db
from app.models import CompetenceEvaluation, CompetenceRecord, CompetenceRequirement, RoleEnum
from app.services import competence, people, training
from app.services.actor import Actor
from app.services.errors import Conflict, ValidationError

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
BASE = "/competencias"
REQ = f"{BASE}/requisitos"
SYSTEM = Actor(1, "seed", ADMIN, "system")


class CompetenceRoutesBase(PeopleRoutesBase):
    def setUp(self) -> None:
        super().setUp()
        self.calidad = self.add_role("Calidad")
        self.compras = self.add_role("Compras")

    def add_requirement(self, rol_id: int, descripcion: str = "Auditor interno",
                        tipo: str = "formacion") -> int:
        return self.seed_with(competence.requirements,
                              {"rol_id": rol_id, "tipo": tipo, "descripcion": descripcion})



class RequirementListTestCase(CompetenceRoutesBase):
    """Who may do what is pinned in test_access_characterization and test_ui_permissions."""

    def test_the_list_is_filtered_by_role(self) -> None:
        self.add_requirement(self.calidad, "Auditor interno")
        self.add_requirement(self.compras, "Negociación", "habilidad")
        self.assertIn("Habilidad", self.page(f"{REQ}/", OPERATIVO))  # the type's display name
        for query, shown in (("", {"Auditor interno", "Negociación"}),
                             (f"?rol_id={self.compras}", {"Negociación"}),
                             (f"?rol_id={self.calidad}", {"Auditor interno"}),
                             ("?rol_id=999", set()), ("?rol_id=x", {"Auditor interno",
                                                                   "Negociación"})):
            with self.subTest(query=query):
                html = self.page(f"{REQ}/{query}")
                for text in ("Auditor interno", "Negociación"):
                    self.assertEqual(text in shown, text in html, text)
        html = self.page(f"{REQ}/?rol_id={self.compras}")
        self.assertIn(f'<option selected value="{self.compras}">Compras</option>', html)



class RequirementWriteTestCase(CompetenceRoutesBase):
    FORM = {"tipo": "habilidad", "descripcion": "Manejo de calibres", "criterio": ""}

    def test_the_form_offers_roles_and_the_type_display_names(self) -> None:
        html = self.page(f"{REQ}/nuevo")
        self.assertIn(f'<option value="{self.calidad}">Calidad</option>', html)
        for name, label in (("educacion", "Educación"), ("formacion", "Formación"),
                            ("habilidad", "Habilidad"), ("experiencia", "Experiencia")):
            with self.subTest(name=name):
                self.assertRegex(html, rf'<option (selected )?value="{name}">{label}</option>')

    def test_auditor_creates_a_requirement(self) -> None:
        self.login(AUDITOR)
        response = self.client.post(f"{REQ}/nuevo", data=self.FORM | {
            "rol_id": str(self.compras), "criterio": "Observación en puesto"})
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{REQ}/?rol_id={self.compras}", response.headers["Location"])
        with self.app.app_context():
            created = CompetenceRequirement.query.one()
            self.assertEqual(
                (self.compras, "habilidad", "Manejo de calibres", "Observación en puesto"),
                (created.rol_id, created.tipo.name, created.descripcion, created.criterio))

    def test_edit_preselects_the_stored_values_and_saves(self) -> None:
        requirement = self.add_requirement(self.calidad, tipo="experiencia")
        html = self.page(f"{REQ}/{requirement}/editar")
        self.assertIn('<option selected value="experiencia">Experiencia</option>', html)
        self.assertIn(f'<option selected value="{self.calidad}">Calidad</option>', html)
        response = self.client.post(f"{REQ}/{requirement}/editar",
                                    data=self.FORM | {"rol_id": str(self.compras)})
        self.assertEqual(302, response.status_code)
        found = self.stored(CompetenceRequirement, requirement)
        self.assertEqual((self.compras, "habilidad", "Manejo de calibres"),
                         (found.rol_id, found.tipo.name, found.descripcion))

    def test_a_missing_role_or_an_unknown_one_keeps_the_input(self) -> None:
        self.login(ADMIN)
        for rol_id in ("", "999", "x"):
            with self.subTest(rol_id=rol_id):
                response = self.client.post(f"{REQ}/nuevo",
                                            data=self.FORM | {"rol_id": rol_id})
                self.assertEqual(200, response.status_code)
                self.assertIn('value="Manejo de calibres"', response.get_data(as_text=True))
        self.assertEqual(0, self.count(CompetenceRequirement))

    def test_service_refusals_are_shown_on_the_form_with_the_input_kept(self) -> None:
        requirement = self.add_requirement(self.calidad)
        self.login(ADMIN)
        data = self.FORM | {"rol_id": str(self.compras)}
        for name, url, error in (
            ("create", f"{REQ}/nuevo", ValidationError("Valor rechazado.")),
            ("update", f"{REQ}/{requirement}/editar", Conflict("Registro en conflicto.")),
        ):
            with self.subTest(name=name), patch.object(competence.requirements, name,
                                                       side_effect=error):
                response = self.client.post(url, data=data)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn(error.message, html)
                self.assertIn('value="Manejo de calibres"', html)
                self.assertIn(f'<option selected value="{self.compras}">Compras</option>', html)
        self.assertEqual(1, self.count(CompetenceRequirement))

    def test_delete_and_delete_conflict(self) -> None:
        free = self.add_requirement(self.calidad, "Libre")
        cited = self.add_requirement(self.calidad, "Citado")
        self.add_record(self.add_person(), requisito_id=cited)
        self.login(ADMIN)
        response = self.client.post(f"{REQ}/{free}/eliminar")
        self.assertEqual((302, f"{REQ}/"), (response.status_code, response.headers["Location"]))
        self.assertIsNone(self.stored(CompetenceRequirement, free))
        response = self.client.post(f"{REQ}/{cited}/eliminar")
        self.assertEqual((302, f"{REQ}/"), (response.status_code, response.headers["Location"]))
        self.assertIn(("danger", competence.REQUIREMENT_IN_USE), self.flashes())
        self.assertIsNotNone(self.stored(CompetenceRequirement, cited))



class RecordFlowTestCase(CompetenceRoutesBase):
    def setUp(self) -> None:
        super().setUp()
        self.ana = self.add_person(rol_ids=[self.calidad])
        self.luis = self.add_person("Luis Gil")
        self.add_person("Eva Inactiva", activo=False)
        self.requisito = self.add_requirement(self.calidad, "Auditor interno")
        self.curso = self.seed_with(training, {"tema": "Auditoría ISO", "fecha": date(2026, 1, 5),
                                               "personal": "Ana"})

    def form(self, **values) -> dict:
        return {"requisito_id": str(self.requisito), "evidencia": "Certificado AI-7",
                "capacitacion_id": str(self.curso), "fecha_obtencion": "2026-01-10",
                "fecha_caducidad": "2027-01-10", "evaluacion_eficacia": "eficaz",
                "fecha_evaluacion": "2026-03-01", "evaluador_id": str(self.luis)} | values

    def test_the_person_page_leads_to_the_record_form(self) -> None:
        html = self.page(f"{BASE}/personas/{self.ana}/nueva")
        self.assertIn("Ana Pérez", html)
        self.assertIn(f'<option value="{self.requisito}">Calidad · Auditor interno</option>',
                      html)
        self.assertIn(f'<option value="{self.curso}">05/01/2026 · Auditoría ISO</option>', html)
        self.assertIn(f'<option value="{self.luis}">Luis Gil</option>', html)
        self.assertNotIn("Eva Inactiva", html)
        self.assertIn('<option selected value="pendiente">Pendiente</option>', html)
        self.assertIn('<option value="no_eficaz">No eficaz</option>', html)

    def test_auditor_records_a_competence_for_the_person(self) -> None:
        self.login(AUDITOR)
        response = self.client.post(f"{BASE}/personas/{self.ana}/nueva", data=self.form())
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"/personas/{self.ana}", response.headers["Location"])
        with self.app.app_context():
            created = CompetenceRecord.query.one()
            self.assertEqual(
                (self.ana, self.requisito, "Certificado AI-7", self.curso, date(2026, 1, 10),
                 date(2027, 1, 10), CompetenceEvaluation.eficaz, date(2026, 3, 1), self.luis),
                (created.persona_id, created.requisito_id, created.evidencia,
                 created.capacitacion_id, created.fecha_obtencion, created.fecha_caducidad,
                 created.evaluacion_eficacia, created.fecha_evaluacion, created.evaluador_id))
        self.assertIn(("success", "Competencia registrada exitosamente"), self.flashes())

    def test_optional_selects_and_dates_may_stay_empty(self) -> None:
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/personas/{self.ana}/nueva", data=self.form(
            requisito_id="", capacitacion_id="", fecha_caducidad="",
            evaluacion_eficacia="pendiente", fecha_evaluacion="", evaluador_id=""))
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            created = CompetenceRecord.query.one()
            self.assertEqual((None, None, None, CompetenceEvaluation.pendiente, None, None),
                             (created.requisito_id, created.capacitacion_id,
                              created.fecha_caducidad, created.evaluacion_eficacia,
                              created.fecha_evaluacion, created.evaluador_id))

    def test_service_errors_are_shown_with_the_input_kept(self) -> None:
        self.login(ADMIN)
        for data, message in (
            (self.form(fecha_caducidad="2025-01-01"), competence.EXPIRY_BEFORE_OBTAINED),
            (self.form(evaluador_id=""), competence.EVALUATION_INCOMPLETE),
        ):
            with self.subTest(message=message):
                response = self.client.post(f"{BASE}/personas/{self.ana}/nueva", data=data)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn(message, html)
                self.assertIn('value="Certificado AI-7"', html)
                self.assertIn(f'<option selected value="{self.requisito}">', html)
        self.assertEqual(0, self.count(CompetenceRecord))

    def test_form_errors_keep_the_input(self) -> None:
        self.login(ADMIN)
        for data in (self.form(evidencia=""), self.form(fecha_obtencion=""),
                     self.form(evaluacion_eficacia="x"), self.form(evaluador_id="999")):
            with self.subTest(data=data):
                response = self.client.post(f"{BASE}/personas/{self.ana}/nueva", data=data)
                self.assertEqual(200, response.status_code)
                self.assertIn("field--invalid", response.get_data(as_text=True))
        self.assertEqual(0, self.count(CompetenceRecord))

    def test_edit_preselects_the_stored_values_and_saves(self) -> None:
        record = self.add_record(self.ana, requisito_id=self.requisito,
                                 evaluacion_eficacia="no_eficaz",
                                 fecha_evaluacion=date(2026, 2, 1), evaluador_id=self.luis)
        self.deactivate(self.luis)
        html = self.page(f"{BASE}/{record}/editar")
        self.assertIn('<option selected value="no_eficaz">No eficaz</option>', html)
        self.assertIn(f'<option selected value="{self.luis}">Luis Gil (desactivada)</option>',
                      html)
        self.assertIn('value="2026-01-10"', html)
        response = self.client.post(f"{BASE}/{record}/editar", data=self.form(
            evidencia="Diploma", capacitacion_id="", fecha_caducidad=""))
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"/personas/{self.ana}", response.headers["Location"])
        found = self.stored(CompetenceRecord, record)
        self.assertEqual((self.ana, "Diploma", None, None, CompetenceEvaluation.eficaz),
                         (found.persona_id, found.evidencia, found.capacitacion_id,
                          found.fecha_caducidad, found.evaluacion_eficacia))

    def deactivate(self, person_id: int) -> None:
        with self.app.app_context():
            people.update(db.session, SYSTEM, person_id, {"activo": False})
            db.session.commit()

    def test_unknown_people_and_records_are_not_found(self) -> None:
        self.login(ADMIN)
        for method, url in (("GET", f"{REQ}/999/editar"), ("POST", f"{REQ}/999/editar"),
                            ("POST", f"{REQ}/999/eliminar"),
                            ("GET", f"{BASE}/personas/999/nueva"),
                            ("POST", f"{BASE}/personas/999/nueva"),
                            ("GET", f"{BASE}/999/editar"), ("POST", f"{BASE}/999/editar"),
                            ("POST", f"{BASE}/999/eliminar")):
            with self.subTest(method=method, url=url):
                response = self.client.open(url, method=method, data=self.form())
                self.assertEqual(404, response.status_code)
        self.assertEqual(0, self.count(CompetenceRecord))


class CompetenceCsrfTestCase(CsrfRoutesBase):
    """The record delete form on the person's page posts its token and returns there."""

    def test_the_rendered_delete_form_posts_and_a_bare_post_is_rejected(self) -> None:
        with self.app.app_context():
            ana = people.create(db.session, SYSTEM, {"nombre": "Ana Pérez"}).id
            record = competence.records.create(db.session, SYSTEM, {
                "persona_id": ana, "evidencia": "Certificado",
                "fecha_obtencion": date(2026, 1, 10)}).id
            db.session.commit()
        url = f"{BASE}/{record}/eliminar"
        self.assertEqual(400, self.client.post(url).status_code)
        html = self.client.get(f"/personas/{ana}").get_data(as_text=True)
        (form,) = [f for f in _post_forms(html) if f["action"] == url]
        response = self.client.post(url, data=form["fields"])
        self.assertEqual((302, f"/personas/{ana}"),
                         (response.status_code, response.headers["Location"]))
        with self.client.session_transaction() as session:
            self.assertIn(("success", "Competencia eliminada exitosamente"), session["_flashes"])
        with self.app.app_context():
            self.assertEqual(0, CompetenceRecord.query.count())


if __name__ == "__main__":
    unittest.main()
