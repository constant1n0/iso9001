"""Training-resource and audit-indicator screens (RG-3 of ``process-role-registers``).

Decision PR4: both legacy registers get list, detail and form screens with
their current fields only. A training resource (``/recursos-formacion/``,
ISO 9001 clause 7.1) holds the resource needed, whether staff training is
needed and a description; an audit indicator (``/indicadores-auditoria/``,
clause 9.1) holds an area, a date, a result, a corrective action and a
performance indicator, and its list says the register is legacy: new process
indicators belong in quality objectives. The audit date is a day on the
screens; an edit keeping the day keeps the stored time. Every role reads;
administrators and auditors write; administrators delete (PR5, each resource's
grant). The JSON registers at ``/recurso_capacitacion/`` and
``/auditoria_indicador/`` stay. Every test runs with the audit flush guard.
"""

from __future__ import annotations

import re
from datetime import datetime

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_delete_forms import _PostForms
from test_process_role_screens import RegisterScreensBase

from app.audit_notifications import local_today
from app.extensions import db
from app.models import AuditoriaIndicador, RecursoCapacitacion, RoleEnum
from app.services import audit_indicators, training_resources

RESOURCES, INDICATORS = "/recursos-formacion", "/indicadores-auditoria"
RESOURCE_FORM = {
    "recurso_necesario": "Sala de formación con proyector", "capacitacion_personal": "y",
    "descripcion_documentacion": "Reserva según el procedimiento PR-07",
}
INDICATOR_FORM = {
    "area_auditoria": "Compras", "fecha_auditoria": "2026-09-30",
    "resultado": "Dos proveedores sin evaluar",
    "accion_correctiva": "Evaluar los proveedores pendientes",
    "indicador_desempeno": "% de proveedores evaluados",
}


class LegacyScreensBase(RegisterScreensBase):
    """``RegisterScreensBase`` with training-resource and audit-indicator helpers."""

    def resource(self, recurso: str = "Sala de formación", **values) -> int:
        with self.app.app_context():
            found = training_resources.create(db.session, SYSTEM,
                                              {"recurso_necesario": recurso} | values)
            db.session.commit()
            return found.id_recurso

    def indicator(self, area: str = "Compras", **values) -> int:
        with self.app.app_context():
            found = audit_indicators.create(db.session, SYSTEM, {"area_auditoria": area} | values)
            db.session.commit()
            return found.id_auditoria


class TrainingResourceFlowTestCase(LegacyScreensBase):
    def test_a_resource_is_created_listed_shown_and_edited_on_the_web(self) -> None:
        self.login(AUDITOR)
        form = self.html(f"{RESOURCES}/nuevo")
        for name in ("recurso_necesario", "capacitacion_personal", "descripcion_documentacion"):
            self.assertIn(f'name="{name}"', form)
        new = self.created(RESOURCES, RESOURCE_FORM)
        self.assertIn(("success", "Recurso de capacitación creado."), self.flashes())
        page = self.html(f"{RESOURCES}/{new}")
        self.assertIn("Sala de formación con proyector", self.block(page, "recurso"))
        self.assertIn("Sí", self.block(page, "capacitacion"))
        self.assertIn("Reserva según el procedimiento PR-07", self.block(page, "documentacion"))
        self.assertEqual(["Sala de formación con proyector", "Sí",
                          "Reserva según el procedimiento PR-07"],
                         self.rows(f"{RESOURCES}/")[new][:3])
        edit = self.html(f"{RESOURCES}/{new}/editar")
        self.assertIn("Sala de formación con proyector", edit)
        self.assertRegex(edit, r'<input checked[^>]*name="capacitacion_personal"')
        self.post(f"{RESOURCES}/{new}/editar",
                  {"recurso_necesario": "Aula 2", "descripcion_documentacion": ""},
                  expect=f"{RESOURCES}/{new}")
        self.assertIn(("success", "Recurso de capacitación actualizado."), self.flashes())
        stored = self.load(RecursoCapacitacion, new)
        self.assertEqual(("Aula 2", False, None), (stored.recurso_necesario,
                                                   stored.capacitacion_personal,
                                                   stored.descripcion_documentacion))
        page = self.html(f"{RESOURCES}/{new}")
        self.assertIn("No", self.block(page, "capacitacion"))
        self.assertIn('class="empty"', self.block(page, "documentacion"))

    def test_the_list_is_by_resource_and_says_when_there_is_none(self) -> None:
        self.login(OPERATIVO)
        self.assertIn("Todavía no hay recursos de capacitación.", self.html(f"{RESOURCES}/"))
        proyector = self.resource("Proyector")
        aula = self.resource("aula de formación", capacitacion_personal=True)
        rows = self.rows(f"{RESOURCES}/")
        self.assertEqual([aula, proyector], list(rows))  # by name, ignoring case
        self.assertEqual(["aula de formación", "Sí", "—"], rows[aula][:3])
        self.assertEqual(["Proyector", "No", "—"], rows[proyector][:3])

    def test_a_blank_resource_is_refused_and_keeps_what_was_typed(self) -> None:
        self.login(ADMIN)
        html = self.rerendered(f"{RESOURCES}/nuevo", RESOURCE_FORM | {"recurso_necesario": " "})
        self.assertIn("field--invalid", html)
        self.assertIn("Reserva según el procedimiento PR-07", html)
        self.assertEqual(0, self.count(RecursoCapacitacion))

    def test_an_administrator_deletes_a_resource(self) -> None:
        found = self.resource()
        self.login(ADMIN)
        page = f"{RESOURCES}/{found}"
        self.assertIn(f'action="{page}/eliminar"', self.html(page))
        self.post(f"{page}/eliminar", expect=f"{RESOURCES}/")
        self.assertIn(("success", "Recurso de capacitación eliminado."), self.flashes())
        self.assertIsNone(self.load(RecursoCapacitacion, found))


class AuditIndicatorFlowTestCase(LegacyScreensBase):
    def test_an_indicator_is_created_listed_and_shown_with_its_date(self) -> None:
        self.login(ADMIN)
        form = self.html(f"{INDICATORS}/nuevo")
        with self.app.app_context():
            today = local_today()
        self.assertIn(f'value="{today.isoformat()}"', form)  # the date starts at today
        for name in ("area_auditoria", "resultado", "accion_correctiva", "indicador_desempeno"):
            self.assertIn(f'name="{name}"', form)
        new = self.created(INDICATORS, INDICATOR_FORM)
        self.assertIn(("success", "Indicador de auditoría creado."), self.flashes())
        self.assertEqual(datetime(2026, 9, 30), self.load(AuditoriaIndicador, new).fecha_auditoria)
        page = self.html(f"{INDICATORS}/{new}")
        self.assertIn("30/09/2026", self.block(page, "identificacion"))
        for name, text in (("resultado", "Dos proveedores sin evaluar"),
                           ("accion", "Evaluar los proveedores pendientes"),
                           ("indicador", "% de proveedores evaluados")):
            with self.subTest(block=name):
                self.assertIn(text, self.block(page, name))
        self.assertEqual(["30/09/2026", "Compras", "Dos proveedores sin evaluar",
                          "% de proveedores evaluados"], self.rows(f"{INDICATORS}/")[new][:4])

    def test_the_list_is_newest_first_and_says_the_register_is_legacy(self) -> None:
        self.login(OPERATIVO)
        empty = self.html(f"{INDICATORS}/")
        self.assertIn("Todavía no hay indicadores de auditoría.", empty)
        older = self.indicator("Ventas", fecha_auditoria=datetime(2026, 3, 1, 9, 0))
        newer = self.indicator("Compras", fecha_auditoria=datetime(2026, 9, 1, 9, 0))
        same_day = self.indicator("Almacén", fecha_auditoria=datetime(2026, 9, 1, 9, 0))
        self.assertEqual([same_day, newer, older], list(self.rows(f"{INDICATORS}/")))
        for html in (empty, self.html(f"{INDICATORS}/")):
            self.assertIn("Registro heredado: los indicadores nuevos de los procesos se registran"
                          ' como <a href="/objetivos-calidad/">objetivos de la calidad</a>.', html)

    def test_an_edit_keeps_the_stored_time_unless_the_day_changes(self) -> None:
        found = self.indicator("Compras", fecha_auditoria=datetime(2026, 9, 30, 14, 45))
        self.login(AUDITOR)
        self.assertIn('value="2026-09-30"', self.html(f"{INDICATORS}/{found}/editar"))
        self.post(f"{INDICATORS}/{found}/editar", INDICATOR_FORM | {"resultado": "Sin hallazgos"},
                  expect=f"{INDICATORS}/{found}")
        self.assertIn(("success", "Indicador de auditoría actualizado."), self.flashes())
        stored = self.load(AuditoriaIndicador, found)
        self.assertEqual((datetime(2026, 9, 30, 14, 45), "Sin hallazgos"),
                         (stored.fecha_auditoria, stored.resultado))
        self.post(f"{INDICATORS}/{found}/editar", INDICATOR_FORM | {"fecha_auditoria": "2026-10-02"},
                  expect=f"{INDICATORS}/{found}")
        self.assertEqual(datetime(2026, 10, 2), self.load(AuditoriaIndicador, found).fecha_auditoria)

    def test_a_refused_write_keeps_what_was_typed(self) -> None:
        self.login(ADMIN)
        for bad in ({"area_auditoria": "x" * 51}, {"area_auditoria": ""},
                    {"fecha_auditoria": ""}, {"fecha_auditoria": "30/09/2026"}):
            with self.subTest(bad=bad):
                html = self.rerendered(f"{INDICATORS}/nuevo", INDICATOR_FORM | bad)
                self.assertIn("field--invalid", html)
                self.assertIn("Dos proveedores sin evaluar", html)
        self.assertEqual(0, self.count(AuditoriaIndicador))

    def test_an_administrator_deletes_an_indicator(self) -> None:
        found = self.indicator()
        self.login(ADMIN)
        page = f"{INDICATORS}/{found}"
        self.assertIn(f'action="{page}/eliminar"', self.html(page))
        self.post(f"{page}/eliminar", expect=f"{INDICATORS}/")
        self.assertIn(("success", "Indicador de auditoría eliminado."), self.flashes())
        self.assertIsNone(self.load(AuditoriaIndicador, found))


class AccessTestCase(LegacyScreensBase):
    def test_every_role_reads_and_only_writers_reach_the_forms(self) -> None:
        resource_id, indicator_id = self.resource(), self.indicator()
        writers = {ADMIN, AUDITOR}
        for role in RoleEnum:
            self.login(role)
            for base, record in ((RESOURCES, resource_id), (INDICATORS, indicator_id)):
                with self.subTest(role=role.name, base=base):
                    listing, page = self.html(f"{base}/"), self.html(f"{base}/{record}")
                    self.assertEqual(role in writers, f'href="{base}/nuevo"' in listing)
                    self.assertEqual(role in writers, f'href="{base}/{record}/editar"' in listing)
                    self.assertNotIn("/eliminar", listing)
                    self.assertEqual(role in writers, f'href="{base}/{record}/editar"' in page)
                    self.assertEqual(role is ADMIN, f'action="{base}/{record}/eliminar"' in page)
        forms = [f"{RESOURCES}/nuevo", f"{RESOURCES}/{resource_id}/editar",
                 f"{INDICATORS}/nuevo", f"{INDICATORS}/{indicator_id}/editar"]
        deletes = [f"{RESOURCES}/{resource_id}/eliminar", f"{INDICATORS}/{indicator_id}/eliminar"]
        self.login(OPERATIVO)
        for method, url in [*((m, u) for u in forms for m in ("get", "post")),
                            *(("post", u) for u in deletes)]:
            with self.subTest(role="OPERATIVO", method=method, url=url):
                self.denied(method, url)
        self.login(AUDITOR)
        for url in forms:
            with self.subTest(role="AUDITOR", url=url):
                self.html(url)
        for url in deletes:
            with self.subTest(role="AUDITOR", url=url):
                self.denied("post", url)
        self.assertIsNotNone(self.load(RecursoCapacitacion, resource_id))
        self.assertIsNotNone(self.load(AuditoriaIndicador, indicator_id))

    def test_an_unknown_record_is_a_404(self) -> None:
        self.login(ADMIN)
        for url in (f"{RESOURCES}/999", f"{RESOURCES}/999/editar", f"{INDICATORS}/999",
                    f"{INDICATORS}/999/editar"):
            with self.subTest(url=url):
                self.assertEqual(404, self.client.get(url).status_code)
        for url in (f"{RESOURCES}/999/eliminar", f"{INDICATORS}/999/eliminar"):
            with self.subTest(url=url):
                self.assertEqual(404, self.client.post(url).status_code)

    def test_the_navigation_lists_both_registers_in_their_groups(self) -> None:
        for role in RoleEnum:
            self.login(role)
            html = self.html("/dashboard/")
            groups = dict(zip(*[iter(re.split(r'class="nav__group">([^<]+)<', html)[1:])] * 2))
            for link, clause, group in ((f"{RESOURCES}/", "7.1", "Personas y competencia"),
                                        (f"{INDICATORS}/", "9.1", "Calidad")):
                with self.subTest(role=role.name, link=link):
                    self.assertRegex(groups[group],
                                     rf'class="nav__link" href="{link}"\s*>\s*[^<]+'
                                     rf'<span class="nav__code">{clause}</span>')
        self.login(ADMIN)
        for link in (f"{RESOURCES}/", f"{INDICATORS}/"):
            self.assertRegex(self.html(link), rf'href="{link}"\s+aria-current="page"')


class CsrfTestCase(LegacyScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        resource_id, indicator_id = self.resource(), self.indicator()
        self.login(ADMIN)
        found = {}
        for base, record in ((RESOURCES, resource_id), (INDICATORS, indicator_id)):
            page, action = f"{base}/{record}", f"{base}/{record}/eliminar"
            forms = _PostForms()
            forms.feed(self.html(page))
            found |= {form["action"]: form for form in forms.forms}
            with self.subTest(action=action):
                self.assertIn(action, found)
                self.assertTrue(found[action]["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
            for url in (f"{base}/nuevo", f"{page}/editar"):
                with self.subTest(url=url):
                    self.assertIn('name="csrf_token"', self.html(url))
                    self.assertEqual(400, self.client.post(url, data={
                        "recurso_necesario": "x", "area_auditoria": "x",
                        "fecha_auditoria": "2026-10-01"}).status_code)
        self.assertEqual((1, 1), (self.count(RecursoCapacitacion), self.count(AuditoriaIndicador)))
        action = f"{INDICATORS}/{indicator_id}/eliminar"
        self.post(action, found[action]["fields"], expect=f"{INDICATORS}/")
        self.assertIsNone(self.load(AuditoriaIndicador, indicator_id))

