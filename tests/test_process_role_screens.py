"""Process and role screens (RG-2 of ``process-role-registers``).

Through the web (ISO 9001 clauses 4.4, 5.3 and 8.1): a process is created
with its type, owner and related processes; the relation is mutual, so each
side's page links the other, and a process's page lists the quality
objectives, risks and opportunities citing it (PR2). Roles are created in a
reporting line; a role's page shows the line up to the top, the roles
reporting directly to it, the people holding it and its competence
requirements (PR3), and a "reports to" choice that would close a cycle is
refused in the form. Every role reads; administrators and auditors write;
administrators delete, never a process related to or cited by others nor a
role with requirements or subordinates (PR5). A refused write re-renders the
form with the input kept. Every test runs with the audit flush guard.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM, ScreensBase
from test_delete_forms import _PostForms

from app.extensions import db
from app.models import Person, ProcesoOperacion, RoleEnum, RolResponsabilidad
from app.services import competence, people, process_operations, quality_objectives, risks
from app.services import roles_responsibilities as roles

PROCESSES, ROLES = "/procesos", "/roles"
DAY = date(2026, 10, 1)
PROCESS_FORM = {
    "proceso": "Producción", "tipo": "operativo", "responsable_id": "",
    "entradas": "Pedidos confirmados", "salidas": "Producto conforme",
    "recursos": "Línea de inyección", "criterio_calidad": "Plan de control PC-01",
    "criterios_aceptacion": "Cero defectos críticos", "control_proveedor": "y",
    "no_conformidad": "Revisar el molde 4",
}
ROLE_FORM = {
    "rol": "Responsable de calidad", "reporta_a_id": "",
    "responsabilidades": "Mantener el SGC", "autoridades": "Detener envíos no conformes",
    "compromiso_calidad": "y", "descripcion_politica_calidad": "Difunde la política",
}


class RegisterScreensBase(ScreensBase):
    """``ScreensBase`` with process, role and citation helpers."""

    def process(self, nombre: str = "Compras", **values) -> int:
        with self.app.app_context():
            found = process_operations.create(db.session, SYSTEM, {"proceso": nombre} | values)
            db.session.commit()
            return found.id_proceso

    def role(self, nombre: str = "Dirección", **values) -> int:
        with self.app.app_context():
            found = roles.create(db.session, SYSTEM, {"rol": nombre} | values)
            db.session.commit()
            return found.id_rol

    def hold(self, rol_id: int, *person_ids: int) -> None:
        with self.app.app_context():
            for person_id in person_ids:
                people.update(db.session, SYSTEM, person_id, {"rol_ids": [rol_id]})
            db.session.commit()

    def requirement(self, rol_id: int, descripcion: str = "Auditor interno ISO 9001") -> int:
        with self.app.app_context():
            found = competence.requirements.create(db.session, SYSTEM, {
                "rol_id": rol_id, "tipo": "formacion", "descripcion": descripcion,
                "criterio": "Certificado del curso"})
            db.session.commit()
            return found.id

    def objective(self, proceso_id: int, titulo: str = "Entregas a tiempo") -> int:
        with self.app.app_context():
            found = quality_objectives.objectives.create(db.session, SYSTEM, {
                "titulo": titulo, "indicador": "% a tiempo", "meta": 95,
                "sentido": "mayor_igual", "responsable_id": self.eva, "proceso_id": proceso_id,
                "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2026, 12, 31),
                "frecuencia": "mensual"})
            db.session.commit()
            return found.id

    def cite(self, register: str, proceso_id: int, titulo: str) -> int:
        """A risk or an opportunity citing the process."""
        with self.app.app_context():
            found = getattr(risks, register).create(db.session, SYSTEM, {
                "titulo": titulo, "descripcion": "d", "origen": "interno",
                "responsable_id": self.eva, "proceso_id": proceso_id}, today=DAY)
            db.session.commit()
            return found.id

    def created(self, base: str, data: dict) -> int:
        """Create a record through the web form; returns its id from the redirect."""
        response = self.post(f"{base}/nuevo", data)
        match = re.fullmatch(rf"{base}/(\d+)", response.headers["Location"])
        self.assertIsNotNone(match, response.headers["Location"])
        return int(match.group(1))

    def related_ids(self, process_id: int) -> list[int]:
        with self.app.app_context():
            return process_operations.related_ids(db.session, process_id)

    @staticmethod
    def block(html: str, name: str) -> str:
        """The card whose title carries ``id="name"``."""
        match = re.search(rf'id="{name}">(.*?)</article>', html, re.S)
        assert match is not None, f"no block {name}"
        return match.group(1)

    @staticmethod
    def text(fragment: str) -> str:
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())

    def rows(self, url: str) -> dict[int, list[str]]:
        """Each list row's cell texts by record id, in the list's order."""
        return {int(found): [self.text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>",
                                                                     row, re.S)]
                for found, row in re.findall(r'<tr id="fila-(\d+)">(.*?)</tr>',
                                             self.html(url), re.S)}


class ProcessFlowTestCase(RegisterScreensBase):
    def test_a_process_is_created_with_its_relations_and_both_pages_show_them(self) -> None:
        compras = self.process("Compras")
        self.login(ADMIN)
        form = self.html(f"{PROCESSES}/nuevo")
        for marker in ('name="tipo"', ">Operativo</option>", ">Estratégico</option>",
                       ">Eva Ruiz</option>", ">Compras</option>"):
            self.assertIn(marker, form)
        self.assertIn("multiple", self.select(form, "relacionados"))
        new = self.created(PROCESSES, PROCESS_FORM | {"responsable_id": str(self.eva),
                                                      "relacionados": [str(compras)]})
        self.assertIn(("success", "Proceso «Producción» creado."), self.flashes())
        page = self.html(f"{PROCESSES}/{new}")
        identification = self.block(page, "identificacion")
        for marker in ("Operativo", f'href="/personas/{self.eva}">Eva Ruiz</a>', "Sí"):
            self.assertIn(marker, identification)
        for marker in ("Pedidos confirmados", "Producto conforme", "Línea de inyección",
                       "Criterios de operación", "Plan de control PC-01",
                       "Criterios de aceptación", "Cero defectos críticos", "Revisar el molde 4"):
            self.assertIn(marker, page)
        self.assertIn(f'href="{PROCESSES}/{compras}">Compras</a>', self.block(page, "relacionados"))
        other = self.html(f"{PROCESSES}/{compras}")
        self.assertIn(f'href="{PROCESSES}/{new}">Producción</a>', self.block(other, "relacionados"))
        self.assertIn("Sin indicar", self.block(other, "identificacion"))
        rows = self.rows(f"{PROCESSES}/")
        self.assertEqual([compras, new], list(rows))  # by name
        self.assertEqual(["Producción", "Operativo", "Eva Ruiz", "1", "0", "0"], rows[new][:6])
        self.assertEqual(["Compras", "—", "—", "1", "0", "0"], rows[compras][:6])

    def test_the_list_filters_by_type_and_owner_and_ignores_values_naming_nothing(self) -> None:
        compras = self.process("Compras", tipo="soporte", responsable_id=self.eva)
        ventas = self.process("Ventas", tipo="operativo", responsable_id=self.luis)
        plan = self.process("Planificación", tipo="estrategico", responsable_id=self.eva)
        self.login(OPERATIVO)
        self.assertEqual([compras, plan, ventas], list(self.rows(f"{PROCESSES}/")))
        for query, expected in (("tipo=operativo", [ventas]),
                                (f"responsable_id={self.eva}", [compras, plan]),
                                (f"tipo=soporte&responsable_id={self.eva}", [compras]),
                                (f"tipo=otro&responsable_id={self.ines}", [compras, plan, ventas])):
            with self.subTest(query=query):
                self.assertEqual(expected, list(self.rows(f"{PROCESSES}/?{query}")))
        self.assertIn("2 activos", self.html(f"{PROCESSES}/?tipo=soporte&responsable_id={self.eva}"))
        owners = self.select(self.html(f"{PROCESSES}/"), "responsable_id")
        self.assertIn(">Eva Ruiz</option>", owners)
        self.assertNotIn("Inés Mora", owners)  # only owners filter
        self.assertIn("Ningún proceso coincide con los filtros.",
                      self.html(f"{PROCESSES}/?tipo=estrategico&responsable_id={self.luis}"))

    def test_a_process_page_lists_the_records_citing_it_and_the_list_counts_them(self) -> None:
        compras = self.process("Compras")
        objective = self.objective(compras)
        risk = self.cite("risks", compras, "Proveedor único")
        opportunity = self.cite("opportunities", compras, "Compra conjunta")
        from app.services import nonconformities
        with self.app.app_context():
            nc = nonconformities.create(db.session, SYSTEM, {
                "descripcion": "Nonconforming output", "fecha_detectada": DAY,
                "proceso_id": compras})
            db.session.commit()
            nc_id = nc.id
        self.login(OPERATIVO)
        page = self.html(f"{PROCESSES}/{compras}")
        self.assertIn(f'href="/no_conformidades/{nc_id}">Nonconforming output</a>',
                      self.block(page, "no-conformidades"))
        self.assertEqual("1", self.rows(f"{PROCESSES}/")[compras][6])
        for name, href, titulo in (("objetivos", f"/objetivos-calidad/{objective}",
                                    "Entregas a tiempo"),
                                   ("riesgos", f"/riesgos/{risk}", "Proveedor único"),
                                   ("oportunidades", f"/oportunidades/{opportunity}",
                                    "Compra conjunta")):
            with self.subTest(section=name):
                self.assertIn(f'href="{href}">{titulo}</a>', self.block(page, name))
        self.assertEqual(["0", "1", "2"], self.rows(f"{PROCESSES}/")[compras][3:6])
        ventas = self.process("Ventas")
        empty = self.html(f"{PROCESSES}/{ventas}")
        for name in ("relacionados", "objetivos", "riesgos", "oportunidades", "no-conformidades"):
            self.assertIn('class="empty"', self.block(empty, name))

    def test_the_related_picker_leaves_the_process_out_and_an_edit_replaces_relations(
            self) -> None:
        compras, ventas, almacen = (self.process(n) for n in ("Compras", "Ventas", "Almacén"))
        with self.app.app_context():
            process_operations.update(db.session, SYSTEM, compras, {"relacionados": [ventas]})
            db.session.commit()
        self.login(AUDITOR)
        picker = self.select(self.html(f"{PROCESSES}/{compras}/editar"), "relacionados")
        self.assertNotIn(f'value="{compras}"', picker)
        self.assertIn(f'<option selected value="{ventas}">Ventas</option>', picker)
        self.assertIn(f'<option value="{almacen}">Almacén</option>', picker)
        edited = PROCESS_FORM | {"proceso": "Compras"}
        self.post(f"{PROCESSES}/{compras}/editar", edited | {"relacionados": [str(almacen)]},
                  expect=f"{PROCESSES}/{compras}")
        self.assertIn(("success", "Proceso actualizado."), self.flashes())
        self.assertEqual([almacen], self.related_ids(compras))
        self.assertEqual("Plan de control PC-01",
                         self.load(ProcesoOperacion, compras).criterio_calidad)
        self.post(f"{PROCESSES}/{compras}/editar", edited, expect=f"{PROCESSES}/{compras}")
        self.assertEqual([], self.related_ids(compras))  # nothing picked: no relations
        html = self.rerendered(f"{PROCESSES}/{compras}/editar",
                               edited | {"relacionados": [str(compras)]})
        self.assertIn("field--invalid", html)  # no browser offers the process itself
        self.assertEqual([], self.related_ids(compras))

    def test_a_refused_write_keeps_what_was_typed(self) -> None:
        self.process("Producción")
        compras = self.process("Compras")
        self.login(ADMIN)
        data = PROCESS_FORM | {"responsable_id": str(self.eva), "relacionados": [str(compras)]}
        html = self.rerendered(f"{PROCESSES}/nuevo", data)
        self.assertIn("Ya existe un proceso con ese nombre.", html)
        for marker in ('value="Producción"', "Plan de control PC-01", "Revisar el molde 4",
                       '<option selected value="operativo">Operativo</option>',
                       f'<option selected value="{self.eva}">Eva Ruiz</option>'):
            self.assertIn(marker, html)
        self.assertIn(f'<option selected value="{compras}">Compras</option>',
                      self.select(html, "relacionados"))
        html = self.rerendered(f"{PROCESSES}/nuevo", data | {"proceso": ""})
        self.assertIn("field--invalid", html)
        self.assertIn("Pedidos confirmados", html)
        self.assertEqual(2, self.count(ProcesoOperacion))
        self.assertEqual([], self.related_ids(compras))

    def test_a_related_or_cited_process_offers_no_delete_and_a_refused_delete_says_why(
            self) -> None:
        related, other = self.process("Compras"), self.process("Ventas")
        with self.app.app_context():
            process_operations.update(db.session, SYSTEM, related, {"relacionados": [other]})
            db.session.commit()
        by_objective, by_risk = self.process("Planificación"), self.process("Almacén")
        self.objective(by_objective)
        self.cite("opportunities", by_risk, "Compra conjunta")
        free = self.process("Libre")
        self.login(ADMIN)
        for process_id, message in ((related, process_operations.HAS_RELATIONS),
                                    (by_objective, process_operations.CITED_BY_OBJECTIVES),
                                    (by_risk, process_operations.CITED_BY_RISKS)):
            with self.subTest(process=process_id):
                page = f"{PROCESSES}/{process_id}"
                self.assertNotIn(f'action="{page}/eliminar"', self.html(page))
                self.post(f"{page}/eliminar", expect=page)
                self.assertIn(("danger", message), self.flashes())
                self.assertIsNotNone(self.load(ProcesoOperacion, process_id))
        page = f"{PROCESSES}/{free}"
        self.assertIn(f'action="{page}/eliminar"', self.html(page))
        self.post(f"{page}/eliminar", expect=f"{PROCESSES}/")
        self.assertIn(("success", "Proceso eliminado."), self.flashes())
        self.assertIsNone(self.load(ProcesoOperacion, free))


class RoleFlowTestCase(RegisterScreensBase):
    def test_a_reporting_line_is_built_in_the_forms_and_shown_up_to_the_top(self) -> None:
        self.login(ADMIN)
        direccion = self.created(ROLES, ROLE_FORM | {"rol": "Dirección"})
        self.assertIn(("success", "Rol «Dirección» creado."), self.flashes())
        self.assertIn(f'<option value="{direccion}">Dirección</option>',
                      self.select(self.html(f"{ROLES}/nuevo"), "reporta_a_id"))
        calidad = self.created(ROLES, ROLE_FORM | {"reporta_a_id": str(direccion)})
        tecnico = self.created(ROLES, ROLE_FORM | {"rol": "Técnico de calidad",
                                                   "reporta_a_id": str(calidad)})
        page = self.html(f"{ROLES}/{tecnico}")
        for marker in ("Mantener el SGC", "Detener envíos no conformes", "Difunde la política"):
            self.assertIn(marker, page)
        line = self.block(page, "linea")
        self.assertLess(line.index(f'href="{ROLES}/{calidad}">Responsable de calidad</a>'),
                        line.index(f'href="{ROLES}/{direccion}">Dirección</a>'))
        self.assertIn('class="empty"', self.block(page, "subordinados"))
        page = self.html(f"{ROLES}/{calidad}")
        self.assertIn(f'href="{ROLES}/{direccion}"', self.block(page, "linea"))
        self.assertNotIn(f'href="{ROLES}/{tecnico}"', self.block(page, "linea"))
        self.assertIn(f'href="{ROLES}/{tecnico}">Técnico de calidad</a>',
                      self.block(page, "subordinados"))
        top = self.html(f"{ROLES}/{direccion}")
        self.assertIn("No reporta a ningún rol.", self.block(top, "linea"))
        self.assertIn(f'href="{ROLES}/{calidad}"', self.block(top, "subordinados"))
        self.assertNotIn(f'href="{ROLES}/{tecnico}"', self.block(top, "subordinados"))
        rows = self.rows(f"{ROLES}/")
        self.assertEqual([direccion, calidad, tecnico], list(rows))  # by name
        self.assertEqual(["Técnico de calidad", "Responsable de calidad", "0", "Sí"],
                         rows[tecnico][:4])
        self.assertEqual(["Dirección", "—", "0", "Sí"], rows[direccion][:4])

    def test_a_reporting_cycle_is_refused_in_the_form_and_the_role_is_not_offered(
            self) -> None:
        direccion = self.role("Dirección")
        calidad = self.role("Calidad", reporta_a_id=direccion)
        tecnico = self.role("Técnico", reporta_a_id=calidad)
        self.login(AUDITOR)
        picker = self.select(self.html(f"{ROLES}/{direccion}/editar"), "reporta_a_id")
        self.assertNotIn(f'value="{direccion}"', picker)
        self.assertIn(f'<option value="{tecnico}">Técnico</option>', picker)
        data = ROLE_FORM | {"rol": "Dirección", "responsabilidades": "Fijar la estrategia"}
        html = self.rerendered(f"{ROLES}/{direccion}/editar",
                               data | {"reporta_a_id": str(tecnico)})
        self.assertIn(roles.REPORTING_CYCLE, html)
        self.assertIn("Fijar la estrategia", html)
        self.assertIn(f'<option selected value="{tecnico}">Técnico</option>', html)
        stored = self.load(RolResponsabilidad, direccion)
        self.assertEqual((None, None), (stored.reporta_a_id, stored.responsabilidades))
        html = self.rerendered(f"{ROLES}/{direccion}/editar",
                               data | {"reporta_a_id": str(direccion)})
        self.assertIn("field--invalid", html)  # no browser offers the role itself
        self.assertIsNone(self.load(RolResponsabilidad, direccion).reporta_a_id)

    def test_a_role_page_lists_its_holders_and_competence_requirements(self) -> None:
        calidad = self.role("Calidad")
        self.hold(calidad, self.eva, self.luis)
        with self.app.app_context():
            people.update(db.session, SYSTEM, self.luis, {"activo": False})
            db.session.commit()
        self.requirement(calidad)
        self.login(OPERATIVO)
        page = self.html(f"{ROLES}/{calidad}")
        holders = self.block(page, "titulares")
        for person_id, nombre in ((self.eva, "Eva Ruiz"), (self.luis, "Luis Gil")):
            self.assertIn(f'href="/personas/{person_id}">{nombre}</a>', holders)
        self.assertIn("Inactivo", holders)
        self.assertNotIn("Inés Mora", holders)
        requirements = self.block(page, "requisitos")
        for marker in ("Formación", "Auditor interno ISO 9001", "Certificado del curso",
                       f'href="/competencias/requisitos/?rol_id={calidad}"'):
            self.assertIn(marker, requirements)
        self.assertEqual(["Calidad", "—", "2", "No"], self.rows(f"{ROLES}/")[calidad][:4])
        empty = self.html(f"{ROLES}/{self.role('Compras')}")
        for name in ("titulares", "requisitos"):
            self.assertIn('class="empty"', self.block(empty, name))

    def test_a_refused_write_keeps_what_was_typed(self) -> None:
        direccion = self.role("Dirección")
        self.role("Responsable de calidad")
        self.login(ADMIN)
        html = self.rerendered(f"{ROLES}/nuevo", ROLE_FORM | {"reporta_a_id": str(direccion)})
        self.assertIn("Ya existe un rol con ese nombre.", html)
        for marker in ('value="Responsable de calidad"', "Detener envíos no conformes",
                       f'<option selected value="{direccion}">Dirección</option>'):
            self.assertIn(marker, html)
        self.assertEqual(2, self.count(RolResponsabilidad))

    def test_a_role_with_requirements_or_subordinates_offers_no_delete(self) -> None:
        direccion = self.role("Dirección")
        calidad = self.role("Calidad", reporta_a_id=direccion)
        auditor = self.role("Auditor interno")
        self.requirement(auditor)
        self.hold(calidad, self.eva)
        self.login(ADMIN)
        for rol_id, message in ((direccion, roles.HAS_SUBORDINATES), (auditor, roles.IN_USE)):
            with self.subTest(role=rol_id):
                page = f"{ROLES}/{rol_id}"
                self.assertNotIn(f'action="{page}/eliminar"', self.html(page))
                self.post(f"{page}/eliminar", expect=page)
                self.assertIn(("danger", message), self.flashes())
                self.assertIsNotNone(self.load(RolResponsabilidad, rol_id))
        page = f"{ROLES}/{calidad}"  # held by Eva, who simply loses it
        self.assertIn(f'action="{page}/eliminar"', self.html(page))
        self.post(f"{page}/eliminar", expect=f"{ROLES}/")
        self.assertIn(("success", "Rol eliminado."), self.flashes())
        self.assertIsNone(self.load(RolResponsabilidad, calidad))
        with self.app.app_context():
            self.assertEqual([], db.session.get(Person, self.eva).roles)


class AccessTestCase(RegisterScreensBase):
    def test_every_role_reads_and_only_writers_reach_the_forms(self) -> None:
        process_id, rol_id = self.process("Compras"), self.role("Calidad")
        pages = [f"{PROCESSES}/", f"{PROCESSES}/{process_id}", f"{ROLES}/", f"{ROLES}/{rol_id}"]
        for role in RoleEnum:
            self.login(role)
            for url in pages:
                with self.subTest(role=role.name, url=url):
                    self.html(url)
        forms = [f"{PROCESSES}/nuevo", f"{PROCESSES}/{process_id}/editar", f"{ROLES}/nuevo",
                 f"{ROLES}/{rol_id}/editar"]
        deletes = [f"{PROCESSES}/{process_id}/eliminar", f"{ROLES}/{rol_id}/eliminar"]
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
        self.assertIsNotNone(self.load(ProcesoOperacion, process_id))
        self.assertIsNotNone(self.load(RolResponsabilidad, rol_id))

    def test_an_unknown_record_is_a_404(self) -> None:
        self.login(ADMIN)
        for url in (f"{PROCESSES}/999", f"{PROCESSES}/999/editar", f"{ROLES}/999",
                    f"{ROLES}/999/editar"):
            with self.subTest(url=url):
                self.assertEqual(404, self.client.get(url).status_code)

    def test_the_navigation_links_both_registers_with_their_clauses(self) -> None:
        for role in RoleEnum:
            self.login(role)
            html = self.html("/dashboard/")
            for link, clause in ((f"{PROCESSES}/", "4.4"), (f"{ROLES}/", "5.3")):
                with self.subTest(role=role.name, link=link):
                    self.assertRegex(html, rf'class="nav__link" href="{link}"\s*>\s*[^<]+'
                                           rf'<span class="nav__code">{clause}</span>')
        self.login(ADMIN)
        self.assertRegex(self.html(f"{ROLES}/"), rf'href="{ROLES}/"\s+aria-current="page"')


class CsrfTestCase(RegisterScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        process_id, rol_id = self.process("Compras"), self.role("Calidad")
        self.login(ADMIN)
        found = {}
        for page, action in ((f"{PROCESSES}/{process_id}", f"{PROCESSES}/{process_id}/eliminar"),
                             (f"{ROLES}/{rol_id}", f"{ROLES}/{rol_id}/eliminar")):
            forms = _PostForms()
            forms.feed(self.html(page))
            found |= {form["action"]: form for form in forms.forms}
            self.assertIn(action, found)
            with self.subTest(action=action):
                self.assertTrue(found[action]["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
        for url in (f"{PROCESSES}/nuevo", f"{PROCESSES}/{process_id}/editar", f"{ROLES}/nuevo",
                    f"{ROLES}/{rol_id}/editar"):
            with self.subTest(url=url):
                self.assertIn('name="csrf_token"', self.html(url))
                self.assertEqual(400, self.client.post(url, data={"proceso": "x", "rol": "x"})
                                 .status_code)
        self.assertEqual((1, 1), (self.count(ProcesoOperacion), self.count(RolResponsabilidad)))
        action = f"{ROLES}/{rol_id}/eliminar"
        self.post(action, found[action]["fields"], expect=f"{ROLES}/")
        self.assertIsNone(self.load(RolResponsabilidad, rol_id))
