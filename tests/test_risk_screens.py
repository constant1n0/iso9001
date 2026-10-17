"""Risk and opportunity screens and heat map (RO-3 of ``risks-opportunities``).

Through the web (ISO 9001 clause 6.1): a risk is created with its interested
party, process and owner pickers, possibly without an assessment ("Sin
valorar", RO4); once assessed it counts in its cell of the 5 × 5 heat map at
the top of the list, which links to the list filtered by that cell; its page
shows the level before and after the actions (residual) and its actions, each
added, completed and then evaluated for effectiveness under the risk. The
opportunities have the same screens with their priority and no heat map. The
dashboard card is tested in ``test_dashboard``. Every role reads (RO6);
administrators and auditors write; administrators delete, never a record with
actions. A refused write re-renders the form with the input kept. Every test
runs with the audit flush guard.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM, ScreensBase
from test_delete_forms import _PostForms

from app.extensions import db
from app.models import (
    AccionRiesgo, EficaciaAccion, EstadoAccionRiesgo, Oportunidad, Riesgo, RoleEnum,
)
from app.services import process_operations, risks, stakeholders

RISKS, OPPORTUNITIES = "/riesgos", "/oportunidades"
TODAY = date(2026, 10, 17)
ROUTES = "app.routes.risk_routes"
RISK_FORM = {
    "titulo": "Fallo de proveedor", "descripcion": "El proveedor único de resina falla",
    "origen": "externo", "parte_interesada_id": "", "proceso_id": "", "responsable_id": "",
    "probabilidad": "", "impacto": "", "tratamiento": "", "probabilidad_residual": "",
    "impacto_residual": "", "estado": "identificado", "fecha_identificacion": "2026-10-01",
    "fecha_revision": "2026-12-31", "notas": "Revisar con compras",
}
OPPORTUNITY_FORM = {
    "titulo": "Nuevo mercado", "descripcion": "Clientes del sector naval", "origen": "externo",
    "parte_interesada_id": "", "proceso_id": "", "responsable_id": "", "beneficio": "",
    "viabilidad": "", "decision": "", "estado": "identificada",
    "fecha_identificacion": "2026-10-01", "fecha_revision": "", "notas": "",
}
ACTION_FORM = {"descripcion": "Segundo proveedor homologado", "responsable_id": "",
               "fecha_limite": "2026-12-31", "estado": "pendiente", "fecha_completada": ""}
CELL = re.compile(r'<td id="celda-(\d)-(\d)" class="heatmap__cell heatmap__cell--(\w+)">(.*?)</td>',
                  re.S)


class RiskScreensBase(ScreensBase):
    """``ScreensBase`` with today pinned in the risk and dashboard routes."""

    def setUp(self) -> None:
        super().setUp()
        for target in (f"{ROUTES}.local_today", "app.routes.dashboard_routes.local_today"):
            patcher = patch(target, return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def process(self, nombre: str = "Compras") -> int:
        with self.app.app_context():
            found = process_operations.create(db.session, SYSTEM, {"proceso": nombre})
            db.session.commit()
            return found.id_proceso

    def party(self, nombre: str = "Proveedores") -> int:
        with self.app.app_context():
            found = stakeholders.create(db.session, SYSTEM, {"nombre": nombre})
            db.session.commit()
            return found.id_interesado

    def record(self, register: str, **values) -> int:
        """A risk or an opportunity owned by Eva, created through the service."""
        base = {"descripcion": "Descripción", "origen": "interno", "responsable_id": self.eva}
        with self.app.app_context():
            found = getattr(risks, register).create(db.session, SYSTEM, base | values,
                                                    today=TODAY)
            db.session.commit()
            return found.id

    def risk(self, **values) -> int:
        return self.record("risks", **{"titulo": "Rotación de personal"} | values)

    def opportunity(self, **values) -> int:
        return self.record("opportunities", **{"titulo": "Nuevo mercado"} | values)

    def action(self, **values) -> int:
        payload = {"descripcion": "Plan de carrera", "responsable_id": self.luis,
                   "fecha_limite": date(2026, 12, 31)} | values
        with self.app.app_context():
            found = risks.actions.create(db.session, SYSTEM, payload, today=TODAY)
            db.session.commit()
            return found.id

    def update(self, register: str, record_id: int, data: dict) -> None:
        with self.app.app_context():
            getattr(risks, register).update(db.session, SYSTEM, record_id, data, today=TODAY)
            db.session.commit()

    def create(self, base: str, path: str, data: dict) -> int:
        """Create a record through the web form (owned by Eva); returns its id."""
        response = self.post(f"{base}/{path}", data | {"responsable_id": str(self.eva)})
        match = re.fullmatch(rf"{base}/(\d+)", response.headers["Location"])
        self.assertIsNotNone(match, response.headers["Location"])
        return int(match.group(1))

    def rows(self, url: str) -> dict[int, str]:
        """The list's rows by record id."""
        base = url.split("?")[0].rstrip("/")
        return {int(found): row for found, row in re.findall(
            rf'<tr>\s*<td><a href="{base}/(\d+)">(.*?)</tr>', self.html(url), re.S)}

    def heat_map(self) -> dict[tuple[int, int], tuple[int, str, str, str]]:
        """Each cell of the risks' heat map: (count, band, link, text), top row first."""
        cells = {}
        for likelihood, impact, band, body in CELL.findall(self.html(f"{RISKS}/")):
            href = unescape(re.search(r'href="([^"]+)"', body).group(1))
            count = int(re.search(r">(\d+)<", body).group(1))
            cells[int(likelihood), int(impact)] = (count, band, href, body)
        self.assertEqual(25, len(cells))
        return cells

    def action_ids(self, **parent) -> list[int]:
        with self.app.app_context():
            return [a.id for a in db.session.query(AccionRiesgo).filter_by(**parent)
                    .order_by(AccionRiesgo.id)]


class RiskFlowTestCase(RiskScreensBase):
    def test_an_unassessed_risk_is_assessed_and_counted_in_its_heat_map_cell(self) -> None:
        process_id, party_id = self.process("Compras"), self.party("Proveedores")
        self.login(ADMIN)
        html = self.html(f"{RISKS}/nuevo")
        for marker in ('name="parte_interesada_id"', 'name="proceso_id"',
                       'name="responsable_id"', ">Proveedores</option>", ">Compras</option>",
                       ">Eva Ruiz</option>", '<option selected value="">Sin valorar</option>',
                       'name="probabilidad_residual"', ">Mitigar</option>",
                       'value="2026-10-17"'):
            self.assertIn(marker, html)

        form = RISK_FORM | {"proceso_id": str(process_id), "parte_interesada_id": str(party_id)}
        rid = self.create(RISKS, "nuevo", form)
        stored = self.load(Riesgo, rid)
        self.assertEqual((None, None, self.eva, process_id, party_id),
                         (stored.probabilidad, stored.impacto, stored.responsable_id,
                          stored.proceso_id, stored.parte_interesada_id))
        row = self.rows(f"{RISKS}/")[rid]
        for text in ("Fallo de proveedor", "Compras", "Eva Ruiz",
                     '<span class="badge">Sin valorar</span>', "Identificado", "31/12/2026"):
            self.assertIn(text, row)
        self.assertEqual(0, sum(count for count, *_ in self.heat_map().values()))

        self.post(f"{RISKS}/{rid}/editar",
                  form | {"responsable_id": str(self.eva), "probabilidad": "4", "impacto": "5",
                          "tratamiento": "mitigar"},
                  expect=f"{RISKS}/{rid}")
        cells = self.heat_map()
        self.assertEqual((5, 1), next(iter(cells)), "likelihood 5 is the top row")
        self.assertEqual((1, 5), list(cells)[-1])
        count, band, href, text = cells[4, 5]
        self.assertEqual((1, "critico"), (count, band))
        self.assertEqual(f"{RISKS}/?probabilidad=4&impacto=5&abierto=1", href)
        self.assertIn("nivel Crítico", text)
        self.assertEqual(1, sum(count for count, *_ in cells.values()))
        self.assertEqual({(1, 1): "bajo", (2, 2): "bajo", (3, 3): "medio", (2, 5): "alto",
                          (4, 4): "alto", (5, 4): "critico"},
                         {cell: cells[cell][1] for cell in ((1, 1), (2, 2), (3, 3), (2, 5),
                                                            (4, 4), (5, 4))})
        self.assertEqual({rid}, set(self.rows(href)))
        self.assertEqual(set(), set(self.rows(cells[3, 3][2])))
        self.assertIn('badge--danger">Crítico', self.rows(f"{RISKS}/")[rid])

        page = self.html(f"{RISKS}/{rid}")
        for text in ("Fallo de proveedor", "El proveedor único de resina falla", "Proveedores",
                     "Compras", "Eva Ruiz", 'badge--danger">Crítico', "Mitigar", "Externo",
                     "01/10/2026", "31/12/2026", "Revisar con compras", "Sin acciones"):
            self.assertIn(text, page)

        # A closed risk leaves the heat map but stays in the list.
        self.update("risks", rid, {"estado": "cerrado"})
        self.assertEqual(0, self.heat_map()[4, 5][0])
        self.assertIn(rid, self.rows(f"{RISKS}/"))

    def test_the_residual_level_follows_the_residual_pair(self) -> None:
        rid = self.risk(probabilidad=4, impacto=4)
        self.login(AUDITOR)
        self.post(f"{RISKS}/{rid}/editar",
                  RISK_FORM | {"responsable_id": str(self.eva), "probabilidad": "4",
                               "impacto": "4", "probabilidad_residual": "2",
                               "impacto_residual": "2"},
                  expect=f"{RISKS}/{rid}")
        page = self.html(f"{RISKS}/{rid}")
        self.assertRegex(page, r'Nivel</span>\s*<span class="list__secondary">\s*'
                               r'<span class="badge badge--alto">Alto')
        self.assertRegex(page, r'Nivel residual</span>\s*<span class="list__secondary">\s*'
                               r'<span class="badge badge--ok">Bajo')
        row = self.rows(f"{RISKS}/")[rid]
        self.assertIn('badge--alto">Alto', row)
        self.assertIn('badge--ok">Bajo', row)

    def test_an_action_is_added_completed_and_evaluated(self) -> None:
        rid = self.risk(probabilidad=4, impacto=5)
        self.login(AUDITOR)
        html = self.html(f"{RISKS}/{rid}/acciones/nueva")
        for marker in ('name="descripcion"', 'name="estado"', 'name="fecha_completada"',
                       ">Luis Gil</option>"):
            self.assertIn(marker, html)
        self.assertNotIn('name="eficacia"', html)
        self.post(f"{RISKS}/{rid}/acciones/nueva", ACTION_FORM | {"responsable_id": str(self.luis)},
                  expect=f"{RISKS}/{rid}")
        (aid,) = self.action_ids(riesgo_id=rid)
        item = f"{RISKS}/{rid}/acciones/{aid}"
        page = self.html(f"{RISKS}/{rid}")
        for text in ("Segundo proveedor homologado", "Luis Gil", "31/12/2026", "Pendiente",
                     f'href="{item}/editar"'):
            self.assertIn(text, page)
        self.assertNotIn(f'href="{item}/eficacia"', page)
        self.post(f"{item}/eficacia", {"eficacia": "eficaz", "fecha_evaluacion": "2026-10-16"},
                  expect=f"{RISKS}/{rid}")
        self.assertIn(("danger", risks.EVALUATION_NOT_DONE), self.flashes())

        self.post(f"{item}/editar", ACTION_FORM | {"responsable_id": str(self.luis),
                                                   "estado": "completada",
                                                   "fecha_completada": "2026-10-15"},
                  expect=f"{RISKS}/{rid}")
        page = self.html(f"{RISKS}/{rid}")
        self.assertIn("Completada", page)
        self.assertIn("15/10/2026", page)
        self.assertIn(f'href="{item}/eficacia"', page)

        html = self.html(f"{item}/eficacia")
        self.assertIn('value="2026-10-17"', html)
        self.assertIn("Segundo proveedor homologado", html)
        self.post(f"{item}/eficacia", {"eficacia": "eficaz", "fecha_evaluacion": "2026-10-16",
                                       "comentario_eficacia": "Sin roturas de stock"},
                  expect=f"{RISKS}/{rid}")
        stored = self.load(AccionRiesgo, aid)
        self.assertEqual((EstadoAccionRiesgo.completada, EficaciaAccion.eficaz,
                          date(2026, 10, 16), "Sin roturas de stock"),
                         (stored.estado, stored.eficacia, stored.fecha_evaluacion,
                          stored.comentario_eficacia))
        page = self.html(f"{RISKS}/{rid}")
        for text in ('badge--ok">Eficaz', "16/10/2026", "Sin roturas de stock"):
            self.assertIn(text, page)

        # An overdue action carries its badge, and the list counts the open ones.
        late = self.action(riesgo_id=rid, descripcion="Auditar al proveedor",
                           fecha_limite=date(2026, 10, 1))
        rows = dict(re.findall(r'<tr id="accion-(\d+)">(.*?)</tr>',
                               self.html(f"{RISKS}/{rid}"), re.S))
        self.assertIn('badge--danger">Vencida', rows[str(late)])
        self.assertNotIn("Vencida", rows[str(aid)])
        self.assertIn('<td class="num">1</td>', self.rows(f"{RISKS}/")[rid])

    def test_the_risk_list_filters(self) -> None:
        process_id = self.process()
        low = self.risk(titulo="Bajo", probabilidad=1, impacto=2, proceso_id=process_id)
        high = self.risk(titulo="Alto", probabilidad=4, impacto=4, origen="externo",
                         responsable_id=self.luis)
        late = self.risk(titulo="Revisión vencida", fecha_identificacion=date(2026, 1, 1),
                         fecha_revision=date(2026, 10, 16))
        closed = self.risk(titulo="Cerrado")
        self.update("risks", closed, {"estado": "cerrado"})
        treated = self.risk(titulo="Tratado", probabilidad=5, impacto=5)
        self.action(riesgo_id=treated)
        self.login(OPERATIVO)
        self.assertIn('badge--warn">Revisión vencida', self.rows(f"{RISKS}/")[late])
        for query, expected in (
            ("estado=cerrado", {closed}), ("nivel=alto", {high}), ("nivel=critico", {treated}),
            (f"responsable_id={self.luis}", {high}), (f"proceso_id={process_id}", {low}),
            ("origen=externo", {high}), ("sin_valorar=1", {late, closed}),
            ("sin_valorar=1&abierto=1", {late}), ("sin_valorar=0", {low, high, treated}),
            ("revision_vencida=1", {late}), ("abierto=0", {closed}),
            ("probabilidad=4&impacto=4", {high}), ("alto_sin_accion=1", {high}),
            ("nivel=enorme&probabilidad=9&abierto=quizá", {low, high, late, closed, treated}),
        ):
            with self.subTest(query=query):
                self.assertEqual(expected, set(self.rows(f"{RISKS}/?{query}")))
        html = self.html(f"{RISKS}/?nivel=alto&origen=externo")
        self.assertIn("2 activos", html)
        self.assertIn('<option selected value="alto">Alto</option>', html)


class OpportunityFlowTestCase(RiskScreensBase):
    def test_an_opportunity_with_its_priority_and_actions(self) -> None:
        self.login(ADMIN)
        html = self.html(f"{OPPORTUNITIES}/nueva")
        for marker in ('name="beneficio"', 'name="viabilidad"', ">Aprovechar</option>",
                       '<option selected value="">Sin valorar</option>'):
            self.assertIn(marker, html)
        self.assertNotIn('name="probabilidad"', html)
        oid = self.create(OPPORTUNITIES, "nueva",
                          OPPORTUNITY_FORM | {"beneficio": "5", "viabilidad": "3",
                                              "decision": "aprovechar"})
        listed = self.html(f"{OPPORTUNITIES}/")
        self.assertNotIn('class="heatmap"', listed)
        row = self.rows(f"{OPPORTUNITIES}/")[oid]
        for text in ("Nuevo mercado", 'badge--ok">Alta', "Aprovechar", "Eva Ruiz", "Identificada"):
            self.assertIn(text, row)
        self.assertEqual({oid}, set(self.rows(f"{OPPORTUNITIES}/?prioridad=alta")))
        self.assertEqual(set(), set(self.rows(f"{OPPORTUNITIES}/?prioridad=baja")))
        page = self.html(f"{OPPORTUNITIES}/{oid}")
        for text in ("Clientes del sector naval", "Beneficio", "Viabilidad", 'badge--ok">Alta',
                     "Aprovechar"):
            self.assertIn(text, page)

        self.post(f"{OPPORTUNITIES}/{oid}/acciones/nueva",
                  ACTION_FORM | {"responsable_id": str(self.ines), "estado": "completada",
                                 "fecha_completada": "2026-10-10"},
                  expect=f"{OPPORTUNITIES}/{oid}")
        (aid,) = self.action_ids(oportunidad_id=oid)
        self.post(f"{OPPORTUNITIES}/{oid}/acciones/{aid}/eficacia",
                  {"eficacia": "no_eficaz", "fecha_evaluacion": "2026-10-12",
                   "comentario_eficacia": "No hubo pedidos"}, expect=f"{OPPORTUNITIES}/{oid}")
        page = self.html(f"{OPPORTUNITIES}/{oid}")
        for text in ("Inés Mora", 'badge--danger">No eficaz', "No hubo pedidos", "12/10/2026"):
            self.assertIn(text, page)


class RulesTestCase(RiskScreensBase):
    def test_deletes_follow_the_service_rules(self) -> None:
        rid = self.risk()
        aid = self.action(riesgo_id=rid)
        oid = self.opportunity()
        self.login(ADMIN)
        page = self.html(f"{RISKS}/{rid}")
        self.assertNotIn(f'action="{RISKS}/{rid}/eliminar"', page)
        self.assertIn(f'action="{RISKS}/{rid}/acciones/{aid}/eliminar"', page)
        self.post(f"{RISKS}/{rid}/eliminar", expect=f"{RISKS}/{rid}")
        self.assertIn(("danger", risks.RISK_HAS_ACTIONS), self.flashes())
        self.post(f"{RISKS}/{rid}/acciones/{aid}/eliminar", expect=f"{RISKS}/{rid}")
        self.assertEqual([], self.action_ids(riesgo_id=rid))
        self.assertIn(f'action="{RISKS}/{rid}/eliminar"', self.html(f"{RISKS}/{rid}"))
        self.post(f"{RISKS}/{rid}/eliminar", expect=f"{RISKS}/")
        self.assertIsNone(self.load(Riesgo, rid))
        self.post(f"{OPPORTUNITIES}/{oid}/eliminar", expect=f"{OPPORTUNITIES}/")
        self.assertIsNone(self.load(Oportunidad, oid))

    def test_an_action_under_another_record_is_not_found(self) -> None:
        rid, other, oid = self.risk(), self.risk(titulo="Otro"), self.opportunity()
        aid = self.action(riesgo_id=rid)
        self.login(ADMIN)
        for base, parent in ((RISKS, other), (OPPORTUNITIES, oid)):
            item = f"{base}/{parent}/acciones/{aid}"
            for method, url in (("get", f"{item}/editar"), ("post", f"{item}/editar"),
                                ("get", f"{item}/eficacia"), ("post", f"{item}/eliminar")):
                with self.subTest(method=method, url=url):
                    self.assertEqual(404, getattr(self.client, method)(url).status_code)
        self.assertEqual(404, self.client.get(f"{RISKS}/{2 ** 40}").status_code)
        self.assertEqual([aid], self.action_ids(riesgo_id=rid))

    def test_a_closed_record_takes_no_new_actions(self) -> None:
        rid, oid = self.risk(), self.opportunity()
        self.update("risks", rid, {"estado": "cerrado"})
        self.update("opportunities", oid, {"estado": "aprovechada"})
        self.login(ADMIN)
        for base, record, message in ((RISKS, rid, risks.RISK_CLOSED),
                                      (OPPORTUNITIES, oid, risks.OPPORTUNITY_CLOSED)):
            with self.subTest(base=base):
                self.assertNotIn(f'href="{base}/{record}/acciones/nueva"',
                                 self.html(f"{base}/{record}"))
                response = self.client.get(f"{base}/{record}/acciones/nueva")
                self.assertEqual(f"{base}/{record}", response.headers["Location"])
                self.assertIn(("danger", message), self.flashes())
        self.assertEqual(0, self.count(AccionRiesgo))


class RolesTestCase(RiskScreensBase):
    def test_every_role_reads_writers_write_and_administrators_delete(self) -> None:
        rid, empty, oid = self.risk(), self.risk(titulo="Sin acciones"), self.opportunity()
        aid = self.action(riesgo_id=rid, estado="completada", fecha_completada=TODAY)
        oaid = self.action(oportunidad_id=oid)
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                self.assertIn("Rotación de personal", self.html(f"{RISKS}/"))
                self.assertIn("Plan de carrera", self.html(f"{RISKS}/{rid}"))
                self.assertIn("Nuevo mercado", self.html(f"{OPPORTUNITIES}/"))
                self.assertIn("Plan de carrera", self.html(f"{OPPORTUNITIES}/{oid}"))
                nav = self.html("/dashboard/")
                self.assertIn(f'class="nav__link" href="{RISKS}/"', nav)
                self.assertIn(f'class="nav__link" href="{OPPORTUNITIES}/"', nav)

        writes = []
        for base, record, path, action in ((RISKS, rid, "nuevo", aid),
                                           (OPPORTUNITIES, oid, "nueva", oaid)):
            item = f"{base}/{record}/acciones/{action}"
            writes += [("get", f"{base}/{path}"), ("post", f"{base}/{path}"),
                       ("get", f"{base}/{record}/editar"), ("post", f"{base}/{record}/editar"),
                       ("get", f"{base}/{record}/acciones/nueva"),
                       ("post", f"{base}/{record}/acciones/nueva"),
                       ("post", f"{item}/editar"), ("get", f"{item}/eficacia"),
                       ("post", f"{item}/eficacia"), ("post", f"{item}/eliminar")]
        self.login(OPERATIVO)
        for method, url in [*writes, ("post", f"{RISKS}/{empty}/eliminar")]:
            with self.subTest(role="OPERATIVO", method=method, url=url):
                self.denied(method, url)
        self.login(AUDITOR)
        for url in (f"{RISKS}/{empty}/eliminar", f"{RISKS}/{rid}/acciones/{aid}/eliminar",
                    f"{OPPORTUNITIES}/{oid}/acciones/{oaid}/eliminar"):
            with self.subTest(role="AUDITOR", url=url):
                self.denied("post", url)
        self.assertEqual(3, self.count(Riesgo) + self.count(Oportunidad))
        self.assertEqual(2, self.count(AccionRiesgo))


class InputKeptTestCase(RiskScreensBase):
    def test_a_refused_risk_keeps_what_was_typed(self) -> None:
        self.login(AUDITOR)
        cases = (
            ({"probabilidad": "4"}, risks.HALF_ASSESSMENT.format(first="probabilidad",
                                                                 second="impacto")),
            ({"probabilidad_residual": "2", "impacto_residual": "2"},
             risks.RESIDUAL_WITHOUT_ASSESSMENT),
            ({"fecha_identificacion": "2026-10-18"}, risks.IDENTIFIED_IN_FUTURE),
            ({"fecha_revision": "2026-09-01"}, risks.REVIEW_BEFORE_IDENTIFICATION),
            ({"probabilidad": "9", "impacto": "1"}, None),
            ({"fecha_identificacion": ""}, None),
        )
        for values, flashed in cases:
            with self.subTest(values=values):
                data = RISK_FORM | {"responsable_id": str(self.eva)} | values
                html = self.rerendered(f"{RISKS}/nuevo", data)
                self.assertIn('value="Fallo de proveedor"', html)
                self.assertIn("Revisar con compras", html)
                self.assertIn(f'<option selected value="{self.eva}">Eva Ruiz</option>', html)
                if flashed:
                    self.assertIn(flashed, html)
                else:
                    self.assertIn("field--invalid", html)
        self.assertEqual(0, self.count(Riesgo))

    def test_a_refused_action_or_evaluation_keeps_what_was_typed(self) -> None:
        rid = self.risk()
        aid = self.action(riesgo_id=rid, estado="completada", fecha_completada=date(2026, 10, 10))
        self.login(AUDITOR)
        for url, values, flashed in (
            (f"{RISKS}/{rid}/acciones/nueva", {"estado": "completada"},
             risks.COMPLETION_REQUIRED),
            (f"{RISKS}/{rid}/acciones/nueva", {"estado": "completada",
                                               "fecha_completada": "2026-10-18"},
             risks.COMPLETION_IN_FUTURE),
            (f"{RISKS}/{rid}/acciones/{aid}/editar", {"fecha_completada": "2026-10-01"},
             risks.COMPLETION_ONLY_WHEN_DONE),
        ):
            with self.subTest(url=url, values=values):
                data = ACTION_FORM | {"responsable_id": str(self.ines)} | values
                html = self.rerendered(url, data)
                self.assertIn("Segundo proveedor homologado", html)
                self.assertIn(f'<option selected value="{self.ines}">Inés Mora</option>', html)
                self.assertIn(flashed, html)
        html = self.rerendered(f"{RISKS}/{rid}/acciones/{aid}/eficacia",
                               {"eficacia": "eficaz", "fecha_evaluacion": "2026-10-09",
                                "comentario_eficacia": "Revisado con producción"})
        self.assertIn(risks.EVALUATION_BEFORE_COMPLETION, html)
        self.assertIn("Revisado con producción", html)
        self.assertIn('<option selected value="eficaz">Eficaz</option>', html)
        stored = self.load(AccionRiesgo, aid)
        self.assertEqual(("Plan de carrera", None), (stored.descripcion, stored.eficacia))
        self.assertEqual([aid], self.action_ids(riesgo_id=rid))


class CsrfTestCase(RiskScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        rid = self.risk()
        aid = self.action(riesgo_id=rid)
        empty = self.opportunity()
        self.login(ADMIN)
        for page, expected in ((f"{RISKS}/{rid}", {f"{RISKS}/{rid}/acciones/{aid}/eliminar"}),
                               (f"{OPPORTUNITIES}/{empty}", {f"{OPPORTUNITIES}/{empty}/eliminar"})):
            forms = _PostForms()
            forms.feed(self.html(page))
            actions = {form["action"]: form for form in forms.forms}
            self.assertEqual(expected, set(actions))
            for action, form in actions.items():
                with self.subTest(action=action):
                    self.assertTrue(form["fields"].get("csrf_token"))
                    self.assertEqual(400, self.client.post(action).status_code)
        item = f"{RISKS}/{rid}/acciones/{aid}"
        for url in (f"{RISKS}/nuevo", f"{RISKS}/{rid}/editar", f"{RISKS}/{rid}/acciones/nueva",
                    f"{item}/editar", f"{item}/eficacia", f"{OPPORTUNITIES}/nueva",
                    f"{OPPORTUNITIES}/{empty}/editar"):
            with self.subTest(url=url):
                self.assertEqual(400, self.client.post(url, data={"titulo": "x"}).status_code)
        self.assertEqual([aid], self.action_ids(riesgo_id=rid))
        self.post(f"{OPPORTUNITIES}/{empty}/eliminar",
                  actions[f"{OPPORTUNITIES}/{empty}/eliminar"]["fields"],
                  expect=f"{OPPORTUNITIES}/")
        self.assertIsNone(self.load(Oportunidad, empty))
