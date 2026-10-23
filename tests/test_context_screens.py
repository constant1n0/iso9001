"""Context issue and interested-party screens (OC-3 of ``organization-context``).

Through the web (ISO 9001 clauses 4.1 and 4.2): a context issue is registered
with its type and a category of that type (both groups are offered; the
service decides), its effect, relevance (a justification when it is not
relevant), the climate-change flag, an owner and its dates; a risk names it, and
the issue's page and the risk's page link each other. The list filters by every
attribute and flags an overdue review. Interested parties carry their type,
relevance (sí, no or sin evaluar), owner, next review and climate-related
requirements. The dashboard card links the overdue reviews to the filtered
lists. Every role reads; administrators and auditors write issues,
administrators delete them; every role writes interested parties. A refused
write re-renders the form with the input kept. Every test runs with the audit
flush guard.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_delete_forms import _PostForms
from test_process_role_screens import RegisterScreensBase

from app.extensions import db
from app.models import CuestionContexto, ParteInteresada, Riesgo, RoleEnum
from app.services import context_issues, risks, stakeholders

ISSUES, PARTIES = "/contexto", "/partes_interesadas"
TODAY = date(2026, 10, 23)
ROUTES = ("app.routes.context_routes", "app.routes.parte_interesada_routes",
          "app.routes.risk_routes", "app.routes.dashboard_routes")
ISSUE_FORM = {
    "tipo": "externa", "categoria": "ambiental", "titulo": "Sequías prolongadas",
    "descripcion": "Restricciones de agua en la planta", "efecto": "negativo",
    "pertinente": "1", "justificacion_pertinencia": "", "cambio_climatico": "y",
    "responsable_id": "", "fecha_identificacion": "2026-10-01",
    "fecha_revision": "2027-04-01", "estado": "vigente", "notas": "Plan de contingencia",
}
PARTY_FORM = {
    "nombre": "Ayuntamiento", "necesidades_expectativas": "Licencia de vertidos",
    "requisitos_identificados": "", "objetivo_estrategico": "", "tipo": "externa",
    "pertinente": "1", "responsable_id": "", "fecha_revision": "2026-12-01",
    "requisitos_climaticos": "y",
}
RISK_FORM = {
    "titulo": "Paradas por falta de agua", "descripcion": "La planta para sin agua",
    "origen": "externo", "parte_interesada_id": "", "proceso_id": "", "responsable_id": "",
    "probabilidad": "", "impacto": "", "tratamiento": "", "probabilidad_residual": "",
    "impacto_residual": "", "estado": "identificado", "fecha_identificacion": "2026-10-02",
    "fecha_revision": "", "notas": "",
}


class ContextScreensBase(RegisterScreensBase):
    """``RegisterScreensBase`` with today pinned and context helpers."""

    routes = ROUTES  # whose ``local_today`` is pinned to ``TODAY``

    def setUp(self) -> None:
        super().setUp()
        for module in self.routes:
            patcher = patch(f"{module}.local_today", return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def issue(self, titulo: str = "Sequías prolongadas", **values) -> int:
        data = {"tipo": "externa", "categoria": "ambiental", "titulo": titulo,
                "descripcion": "Descripción", "efecto": "negativo", "pertinente": True,
                "fecha_identificacion": date(2026, 10, 1)} | values
        with self.app.app_context():
            found = context_issues.issues.create(db.session, SYSTEM, data, today=TODAY)
            db.session.commit()
            return found.id

    def risk(self, titulo: str = "Paradas", register: str = "risks", **values) -> int:
        with self.app.app_context():
            found = getattr(risks, register).create(db.session, SYSTEM, {
                "titulo": titulo, "descripcion": "d", "origen": "externo",
                "responsable_id": self.eva} | values, today=TODAY)
            db.session.commit()
            return found.id

    def party(self, nombre: str = "Vecinos", today: date = TODAY, **values) -> int:
        with self.app.app_context():
            found = stakeholders.create(db.session, SYSTEM, {"nombre": nombre} | values,
                                        today=today)
            db.session.commit()
            return found.id_interesado

    def created_at(self, url: str, data: dict, base: str) -> int:
        """POST a creation form; returns the new record's id from the redirect."""
        response = self.post(url, data)
        match = re.fullmatch(rf"{base}/(\d+)", response.headers["Location"])
        self.assertIsNotNone(match, response.headers["Location"])
        return int(match.group(1))

    @staticmethod
    def field_html(html: str, name: str) -> str:
        """The ``field`` block holding the input named ``name``."""
        start = r'<div class="field[ "]'  # a field, not its ``field__error``
        match = re.search(rf'{start}[^>]*>(?:(?!{start}).)*?name="{name}"(?:(?!{start}).)*',
                          html, re.S)
        assert match is not None, f"no field {name}"
        return match.group(0)

    def records(self, url: str) -> list[int]:
        """The ids of the risks or opportunities a register list shows, in its order."""
        base = url.split("?")[0].rstrip("/")
        return [int(found) for found in re.findall(rf'<tr>\s*<td><a href="{base}/(\d+)">',
                                                   self.html(url))]


class IssueFlowTestCase(ContextScreensBase):
    def test_an_issue_is_created_a_risk_links_it_and_both_pages_show_it(self) -> None:
        superseded = self.issue("Normativa derogada", tipo="externa", categoria="legal",
                                estado="superada")
        self.login(AUDITOR)
        form = self.html(f"{ISSUES}/nueva")
        for marker in ('<optgroup label="Externa">', '<optgroup label="Interna">',
                       ">Ambiental</option>", ">Cultura</option>", 'name="cambio_climatico"',
                       'name="justificacion_pertinencia"', ">Eva Ruiz</option>",
                       'value="2026-10-23"'):
            self.assertIn(marker, form)
        issue = self.created_at(f"{ISSUES}/nueva", ISSUE_FORM | {"responsable_id": str(self.eva)},
                                ISSUES)
        self.assertIn(("success", "Cuestión «Sequías prolongadas» creada."), self.flashes())
        page = self.html(f"{ISSUES}/{issue}")
        identification = self.text(self.block(page, "identificacion"))
        for marker in ("Externa", "Ambiental", "Negativo", "Pertinente Sí",
                       "Cambio climático Sí", "Eva Ruiz", "01/10/2026", "01/04/2027", "Vigente"):
            self.assertIn(marker, identification)
        for marker in ("Restricciones de agua en la planta", "Plan de contingencia"):
            self.assertIn(marker, page)
        self.assertIn("Sin riesgos vinculados.", self.block(page, "riesgos"))

        # The risk form offers the issues in force only; the risk names one.
        picker = self.select(self.html("/riesgos/nuevo"), "cuestion_id")
        self.assertIn(">Sequías prolongadas</option>", picker)
        self.assertNotIn("Normativa derogada", picker)
        risk = self.created_at("/riesgos/nuevo", RISK_FORM | {
            "responsable_id": str(self.eva), "cuestion_id": str(issue)}, "/riesgos")
        other = self.risk("Sin cuestión")
        opportunity = self.risk("Riego eficiente", register="opportunities", cuestion_id=issue)
        risk_page = self.html(f"/riesgos/{risk}")
        self.assertIn(f'href="{ISSUES}/{issue}">Sequías prolongadas</a>', risk_page)
        self.assertIn("Cuestión del contexto —", self.text(self.block(
            self.html(f"/riesgos/{other}"), "identificacion")))
        page = self.html(f"{ISSUES}/{issue}")
        self.assertIn(f'href="/riesgos/{risk}">Paradas por falta de agua</a>',
                      self.block(page, "riesgos"))
        self.assertIn(f'href="/riesgos/?cuestion_id={issue}"', self.block(page, "riesgos"))
        self.assertIn(f'href="/oportunidades/{opportunity}">Riego eficiente</a>',
                      self.block(page, "oportunidades"))
        self.assertIn(f'href="/oportunidades/?cuestion_id={issue}"',
                      self.block(page, "oportunidades"))
        self.assertEqual([risk], self.records(f"/riesgos/?cuestion_id={issue}"))
        self.assertEqual({risk, other}, set(self.records("/riesgos/")))
        self.assertEqual([opportunity], self.records(f"/oportunidades/?cuestion_id={issue}"))
        self.assertIn(">Normativa derogada</option>",
                      self.select(self.html("/riesgos/"), "cuestion_id"))  # the filter: all

        rows = self.rows(f"{ISSUES}/")
        self.assertEqual([issue, superseded], list(rows))  # by type, category, title
        self.assertEqual(["Externa", "Ambiental", "Sequías prolongadas", "Negativo", "Pertinente",
                          "Cambio climático", "Eva Ruiz", "01/04/2027", "Vigente", "1", "1"],
                         rows[issue][:11])
        self.assertEqual(["Externa", "Legal", "Normativa derogada", "Negativo", "Pertinente", "—",
                          "—", "—", "Superada", "0", "0"], rows[superseded][:11])

        # Editing keeps a superseded link offered; an administrator cannot delete a cited issue.
        self.post(f"{ISSUES}/{issue}/editar", ISSUE_FORM | {"estado": "superada"},
                  expect=f"{ISSUES}/{issue}")
        self.assertIn(("success", "Cuestión actualizada."), self.flashes())
        edit = self.html(f"/riesgos/{risk}/editar")
        self.assertIn(f'<option selected value="{issue}">Sequías prolongadas (superada)</option>',
                      self.select(edit, "cuestion_id"))
        self.login(ADMIN)
        self.assertNotIn(f'action="{ISSUES}/{issue}/eliminar"', self.html(f"{ISSUES}/{issue}"))
        self.post(f"{ISSUES}/{issue}/eliminar", expect=f"{ISSUES}/{issue}")
        self.assertIn(("danger", context_issues.CITED_BY_RISKS), self.flashes())
        self.assertEqual(2, self.count(CuestionContexto))
        self.post(f"{ISSUES}/{superseded}/eliminar", expect=f"{ISSUES}/")
        self.assertIn(("success", "Cuestión eliminada."), self.flashes())
        self.assertEqual(issue, self.load(Riesgo, risk).cuestion_id)

    def test_the_list_filters_and_flags_overdue_reviews(self) -> None:
        drought = self.issue("Sequías", cambio_climatico=True, responsable_id=self.eva,
                             fecha_identificacion=date(2026, 1, 10),
                             fecha_revision=date(2026, 6, 1))
        culture = self.issue("Cultura de calidad", tipo="interna", categoria="cultura",
                             efecto="positivo", pertinente=False,
                             justificacion_pertinencia="Ya consolidada",
                             fecha_revision=date(2027, 1, 1))
        old = self.issue("Arancel derogado", categoria="economica", estado="superada",
                         fecha_identificacion=date(2026, 1, 10),
                         fecha_revision=date(2026, 2, 1))
        self.login(OPERATIVO)
        self.assertEqual([drought, old, culture], list(self.rows(f"{ISSUES}/")))
        for query, expected in (
                ("tipo=interna", [culture]), ("categoria=ambiental", [drought]),
                ("pertinente=0", [culture]), ("pertinente=1", [drought, old]),
                ("cambio_climatico=1", [drought]), ("estado=superada", [old]),
                (f"responsable_id={self.eva}", [drought]), ("revision_vencida=1", [drought]),
                ("revision_vencida=0", [old, culture]),
                ("tipo=externa&pertinente=1&estado=vigente", [drought]),
                (f"tipo=otra&responsable_id={self.ines}&cambio_climatico=x",
                 [drought, old, culture])):
            with self.subTest(query=query):
                self.assertEqual(expected, list(self.rows(f"{ISSUES}/?{query}")))
        self.assertIn("3 activos", self.html(f"{ISSUES}/?tipo=externa&pertinente=1&estado=vigente"))
        rows = self.rows(f"{ISSUES}/")
        self.assertEqual("01/06/2026 Revisión vencida", rows[drought][7])
        self.assertEqual("01/02/2026", rows[old][7])  # a superseded issue is never overdue
        self.assertEqual("No pertinente", rows[culture][4])
        owners = self.select(self.html(f"{ISSUES}/"), "responsable_id")
        self.assertIn(">Eva Ruiz</option>", owners)
        self.assertNotIn("Luis Gil", owners)  # owners of some issue only
        page = self.text(self.block(self.html(f"{ISSUES}/{culture}"), "identificacion"))
        self.assertIn("Pertinente No", page)
        self.assertIn("Ya consolidada", page)


class IssueFormTestCase(ContextScreensBase):
    def test_a_refused_issue_shows_the_error_on_its_field_and_keeps_what_was_typed(self) -> None:
        self.login(AUDITOR)
        html = self.rerendered(f"{ISSUES}/nueva", ISSUE_FORM | {
            "tipo": "interna", "categoria": "politica", "titulo": "Cambio de gobierno"})
        category = self.field_html(html, "categoria")
        self.assertIn("field--invalid", category)
        self.assertIn(context_issues.CATEGORY_MISMATCH, category)
        self.assertIn('<option selected value="politica">', category)
        self.assertIn('value="Cambio de gobierno"', html)
        self.assertIn('<option selected value="interna">', self.select(html, "tipo"))

        html = self.rerendered(f"{ISSUES}/nueva", ISSUE_FORM | {"pertinente": "0"})
        reason = self.field_html(html, "justificacion_pertinencia")
        self.assertIn(context_issues.JUSTIFICATION_REQUIRED, reason)
        self.assertIn("Restricciones de agua en la planta", html)
        self.assertNotIn(context_issues.JUSTIFICATION_REQUIRED, self.field_html(html, "categoria"))

        html = self.rerendered(f"{ISSUES}/nueva",
                               ISSUE_FORM | {"fecha_identificacion": "2026-11-01"})
        self.assertIn(context_issues.IDENTIFIED_IN_FUTURE,
                      self.field_html(html, "fecha_identificacion"))
        html = self.rerendered(f"{ISSUES}/nueva", ISSUE_FORM | {"fecha_revision": "2026-09-01"})
        self.assertIn(context_issues.REVIEW_BEFORE_IDENTIFICATION,
                      self.field_html(html, "fecha_revision"))
        self.assertEqual(0, self.count(CuestionContexto))

        issue = self.issue()
        html = self.rerendered(f"{ISSUES}/{issue}/editar", ISSUE_FORM | {
            "pertinente": "0", "titulo": "Sequías severas"})
        self.assertIn(context_issues.JUSTIFICATION_REQUIRED,
                      self.field_html(html, "justificacion_pertinencia"))
        self.assertIn('value="Sequías severas"', html)
        self.post(f"{ISSUES}/{issue}/editar", ISSUE_FORM | {
            "pertinente": "0", "justificacion_pertinencia": "Sin efecto en la planta"},
            expect=f"{ISSUES}/{issue}")
        found = self.load(CuestionContexto, issue)
        self.assertEqual((False, "Sin efecto en la planta", True),
                         (found.pertinente, found.justificacion_pertinencia,
                          found.cambio_climatico))


class PartyScreensTestCase(ContextScreensBase):
    def test_the_party_fields_round_trip(self) -> None:
        self.login(OPERATIVO)  # every role writes interested parties
        form = self.html(f"{PARTIES}/nueva")
        for marker in ('name="tipo"', ">Sin evaluar</option>", ">Eva Ruiz</option>",
                       'name="fecha_revision"', 'name="requisitos_climaticos"'):
            self.assertIn(marker, form)
        self.post(f"{PARTIES}/nueva", PARTY_FORM | {"responsable_id": str(self.eva)},
                  expect=f"{PARTIES}/")
        with self.app.app_context():
            found = ParteInteresada.query.one()
            db.session.expunge(found)
        self.assertEqual(("externa", True, self.eva, date(2026, 12, 1), True),
                         (found.tipo.name, found.pertinente, found.responsable_id,
                          found.fecha_revision, found.requisitos_climaticos))
        party = found.id_interesado
        row = " ".join(self.rows(f"{PARTIES}/")[party])
        for marker in ("Ayuntamiento", "Externa", "Pertinente", "Requisitos climáticos",
                       "Eva Ruiz", "01/12/2026"):
            self.assertIn(marker, row)
        self.assertIn(f'href="{PARTIES}/{party}">Ayuntamiento</a>', self.html(f"{PARTIES}/"))
        page = self.text(self.block(self.html(f"{PARTIES}/{party}"), "identificacion"))
        for marker in ("Externa", "Pertinente Sí", "Eva Ruiz", "01/12/2026",
                       "Requisitos relacionados con el cambio climático Sí"):
            self.assertIn(marker, page)
        self.assertIn("Licencia de vertidos", self.html(f"{PARTIES}/{party}"))

        edit = self.html(f"{PARTIES}/editar/{party}")
        self.assertIn('<option selected value="externa">', self.select(edit, "tipo"))
        self.assertIn('<option selected value="1">', self.select(edit, "pertinente"))
        self.assertIn(f'<option selected value="{self.eva}">', self.select(edit, "responsable_id"))
        self.post(f"{PARTIES}/editar/{party}", PARTY_FORM | {
            "tipo": "", "pertinente": "", "fecha_revision": "", "requisitos_climaticos": ""},
            expect=f"{PARTIES}/")
        found = self.load(ParteInteresada, party)
        self.assertEqual((None, None, None, None, False),
                         (found.tipo, found.pertinente, found.responsable_id,
                          found.fecha_revision, found.requisitos_climaticos))
        row = " ".join(self.rows(f"{PARTIES}/")[party])
        self.assertIn("Sin evaluar", row)
        self.assertNotIn("Requisitos climáticos", row)
        self.post(f"{PARTIES}/editar/{party}", PARTY_FORM | {"pertinente": "0"})
        self.assertIn("No pertinente", " ".join(self.rows(f"{PARTIES}/")[party]))

        html = self.rerendered(f"{PARTIES}/editar/{party}", PARTY_FORM | {
            "fecha_revision": "2026-10-01", "necesidades_expectativas": "Nuevo texto"})
        self.assertIn(stakeholders.REVIEW_IN_PAST, html)
        self.assertIn("Nuevo texto", html)
        self.assertEqual(date(2026, 12, 1), self.load(ParteInteresada, party).fecha_revision)


class ContextCardTestCase(ContextScreensBase):
    def card(self, role: RoleEnum) -> str:
        self.login(role)
        match = re.search(r'<article class="card" id="contexto">(.*?)</article>',
                          self.html("/dashboard/"), re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_the_card_links_overdue_reviews_to_the_filtered_lists(self) -> None:
        for role in RoleEnum:
            with self.subTest(empty=role.name):
                self.assertIn("Ninguna cuestión ni parte interesada tiene la revisión vencida.",
                              self.card(role))
        self.issue("Sequías", fecha_identificacion=date(2026, 1, 10),
                   fecha_revision=date(2026, 6, 1))
        self.issue("Al día", fecha_revision=date(2027, 1, 1))
        late = self.party("Vecinos", today=date(2026, 1, 1), fecha_revision=date(2026, 6, 1))
        on_time = self.party("Bancos", fecha_revision=date(2027, 1, 1))
        for role in RoleEnum:
            with self.subTest(role=role.name):
                card = self.text(self.card(role))
                self.assertIn("Cuestiones del contexto con la revisión vencida 1", card)
                self.assertIn("Partes interesadas con la revisión vencida 1", card)
        html = self.card(OPERATIVO)
        self.assertIn(f'href="{ISSUES}/?revision_vencida=1"', html)
        self.assertIn(f'href="{PARTIES}/?revision_vencida=1"', html)
        self.assertEqual([late], list(self.rows(f"{PARTIES}/?revision_vencida=1")))
        self.assertEqual({late, on_time}, set(self.rows(f"{PARTIES}/")))
        self.assertIn("Revisión vencida", " ".join(self.rows(f"{PARTIES}/")[late]))


class AccessTestCase(ContextScreensBase):
    def test_every_role_reads_writers_write_and_administrators_delete(self) -> None:
        issue = self.issue()
        for role in RoleEnum:
            self.login(role)
            for url in (f"{ISSUES}/", f"{ISSUES}/{issue}"):
                with self.subTest(role=role.name, url=url):
                    self.html(url)
        self.login(OPERATIVO)
        for method, url in (("get", f"{ISSUES}/nueva"), ("get", f"{ISSUES}/{issue}/editar"),
                            ("post", f"{ISSUES}/{issue}/eliminar")):
            with self.subTest(denied=url):
                self.denied(method, url)
        self.login(AUDITOR)
        self.denied("post", f"{ISSUES}/{issue}/eliminar")
        self.assertEqual(1, self.count(CuestionContexto))


class CsrfTestCase(ContextScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        issue = self.issue()
        party = self.party()
        self.login(ADMIN)
        found = {}
        for page in (f"{ISSUES}/{issue}", f"{PARTIES}/{party}"):
            forms = _PostForms()
            forms.feed(self.html(page))
            found |= {form["action"]: form for form in forms.forms}
        self.assertEqual({f"{ISSUES}/{issue}/eliminar", f"{PARTIES}/eliminar/{party}"},
                         set(found))
        for action, form in found.items():
            with self.subTest(action=action):
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
        for url in (f"{ISSUES}/nueva", f"{ISSUES}/{issue}/editar", f"{PARTIES}/nueva",
                    f"{PARTIES}/editar/{party}"):
            with self.subTest(url=url):
                self.assertIn('name="csrf_token"', self.html(url))
                self.assertEqual(400, self.client.post(url, data={"titulo": "x"}).status_code)
        self.assertEqual(1, self.count(CuestionContexto))
        action = f"{ISSUES}/{issue}/eliminar"
        self.post(action, found[action]["fields"], expect=f"{ISSUES}/")
        self.assertEqual(0, self.count(CuestionContexto))
