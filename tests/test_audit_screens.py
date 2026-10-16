"""Audit programme and findings screens (task AP-3 of ``audit-programme``).

Through the web: a yearly programme is created, approved by an administrator
(the approver picker defaults to the person linked to the user, the date is
``local_today``) and lists its audits with their findings by type; an audit in
it gets its team and planning fields, refused with the input kept when its
auditors own the audited area or its date falls outside the programme's year;
its page lists the findings, links each nonconformity finding to its
nonconformity and offers the finding forms (deleting is for administrators; a
cancelled audit shows its findings read-only). Programmes and findings share
the ``AUDITS`` grant: administrators and auditors read and write,
administrators approve and delete, ``OPERATIVO`` sees nothing. Every test runs
with the audit flush guard, so a web write that skips its audit row fails.

The audit and programme PDFs (task AP-4) are checked on the HTML their views
render: ``render_pdf`` is replaced so the test keeps that HTML
(``test_pdf_exports`` renders the real PDFs).
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from test_delete_forms import _PostForms
from werkzeug.security import generate_password_hash

from app import session_lifetime
from app.extensions import db
from app.models import (Auditoria, AuditLog, EstadoProgramaAuditoria, HallazgoAuditoria,
                        NoConformidad, Person, ProgramaAuditoria, RoleEnum, TipoHallazgo, User)
from app.services import audit, audit_findings, audit_programmes, audits
from app.services.actor import Actor

PASSWORD_HASH = generate_password_hash("StrongPassword123!")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
SYSTEM = Actor(None, "seed", ADMIN, "system")
PROG = "/programas-auditoria"
AUD = "/auditorias"
TODAY = date(2026, 10, 30)
AUDIT_ROUTES, PROG_ROUTES = "app.routes.auditoria_routes", "app.routes.audit_programme_routes"
TODAY_IN_ROUTES = f"{PROG_ROUTES}.local_today"
DENIED = ("danger", "No tienes permiso para acceder a esta página.")
AUDIT_FORM = {
    "area_auditada": "Compras",
    "fecha": "2026-10-05",
    "auditor": "Luis",
    "resultado": "Dos hallazgos",
    "accion_correctiva": "",
    "estado": "PENDIENTE",
    "criterios": "ISO 9001:2026",
}


class ScreensBase(unittest.TestCase):
    """Users of every role and four people: Ana (the administrator's own), Eva, Luis, Inés."""

    overrides: dict = {}

    def setUp(self) -> None:
        self.app = bootstrap.build_app(**self.overrides)
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(),
                                    email=f"{role.name.lower()}@example.com",
                                    password=PASSWORD_HASH, role=role))
            db.session.flush()
            self.ids = {u.role: u.id for u in User.query.all()}
            people = [Person(nombre="Ana Pérez", user_id=self.ids[ADMIN]),
                      Person(nombre="Eva Ruiz"), Person(nombre="Luis Gil"),
                      Person(nombre="Inés Mora")]
            db.session.add_all(people)
            db.session.commit()
            self.ana, self.eva, self.luis, self.ines = (p.id for p in people)
            self.remove_guard = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        self.remove_guard()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # -- helpers ------------------------------------------------------------

    def login(self, role: RoleEnum = ADMIN) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session_lifetime.start(session)
            session["_fresh"] = True

    def flashes(self) -> list:
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def html(self, url: str) -> str:
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, url)
        return response.get_data(as_text=True)

    def post(self, url: str, data: dict | None = None, expect: str | None = None):
        """POST ``data`` and check it redirects to ``expect`` (a path)."""
        response = self.client.post(url, data=data or {})
        self.assertEqual(302, response.status_code, response.get_data(as_text=True)[:800])
        if expect is not None:
            self.assertEqual(expect, response.headers["Location"])
        return response

    def rerendered(self, url: str, data: dict) -> str:
        """POST ``data`` expecting the form again (200); returns the page."""
        response = self.client.post(url, data=data)
        self.assertEqual(200, response.status_code, response.headers.get("Location"))
        return response.get_data(as_text=True)

    def denied(self, method: str, url: str) -> None:
        response = getattr(self.client, method)(url, data={})
        self.assertEqual(302, response.status_code, f"{method} {url}")
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"), url)
        self.assertIn(DENIED, self.flashes())

    def programme(self, anio: int = 2026, approved: bool = False) -> int:
        """A programme for ``anio``; an approved one was approved by Luis."""
        with self.app.app_context():
            found = audit_programmes.create(db.session, SYSTEM, {
                "anio": anio, "objetivos": f"Objetivos {anio}", "alcance": f"Alcance {anio}"})
            if approved:
                audit_programmes.approve(db.session, SYSTEM, found.id,
                                         aprobado_por_id=self.luis, today=date(anio, 1, 15))
            db.session.commit()
            return found.id

    def audit(self, **values) -> int:
        with self.app.app_context():
            found = audits.create(db.session, SYSTEM, {
                "area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Luis",
                "resultado": "Correcta"} | values)
            db.session.commit()
            return found.id

    def finding(self, audit_id: int, **values) -> tuple[int, int | None]:
        """A finding (an observation unless said otherwise); its id and nonconformity's."""
        with self.app.app_context():
            found = audit_findings.create(db.session, SYSTEM, {
                "auditoria_id": audit_id, "tipo": "observacion",
                "descripcion": "Registro sin firmar"} | values)
            db.session.commit()
            return found.id, found.no_conformidad_id

    def load(self, model, record_id: int):
        with self.app.app_context():
            found = db.session.get(model, record_id)
            if found is not None:
                db.session.expunge(found)
            return found

    def count(self, model) -> int:
        with self.app.app_context():
            return db.session.query(model).count()

    def audit_rows(self) -> int:
        return self.count(AuditLog)

    def pdf_html(self, url: str, routes: str) -> str:
        """The HTML the PDF view of ``routes`` renders for ``url``, instead of the PDF."""
        from flask import render_template

        rendered: list[str] = []

        def as_html(template: str, **context) -> bytes:
            rendered.append(render_template(template, generado=TODAY, **context))
            return b"%PDF-1.7"

        with patch(f"{routes}.render_pdf", side_effect=as_html):
            response = self.client.get(url)
        self.assertEqual(200, response.status_code, url)
        self.assertEqual("application/pdf", response.mimetype)
        (html,) = rendered
        return html

    @staticmethod
    def pdf_fields(html: str) -> dict[str, str]:
        """The label/value rows of the PDF's key/value tables."""
        return dict(re.findall(r"<tr><th>([^<]+)</th><td>([^<]*)</td></tr>", html))

    @staticmethod
    def select(html: str, name: str) -> str:
        """The ``<select>`` named ``name`` in ``html``."""
        match = re.search(rf'<select[^>]*name="{name}"[^>]*>.*?</select>', html, re.S)
        assert match is not None, f"no select {name}"
        return match.group(0)

    @staticmethod
    def detail(audit_id: int) -> str:
        return f"{AUD}/{audit_id}"

    @staticmethod
    def finding_url(audit_id: int, finding_id: int | None = None, verb: str = "") -> str:
        if finding_id is None:
            return f"{AUD}/{audit_id}/hallazgos/nuevo"
        return f"{AUD}/{audit_id}/hallazgos/{finding_id}/{verb}"


class FlowTestCase(ScreensBase):
    def test_a_programme_its_audit_and_the_findings_through_the_web(self) -> None:
        self.login(ADMIN)
        # The programme: created as a draft, then approved by default by Ana.
        response = self.post(f"{PROG}/nuevo", {"anio": "2026",
                                               "objetivos": "Auditar los procesos clave",
                                               "alcance": "Compras y ventas"})
        with self.app.app_context():
            prog_id = ProgramaAuditoria.query.one().id
        self.assertEqual(f"{PROG}/{prog_id}", response.headers["Location"])
        self.assertIn(("success", "Programa de auditoría 2026 creado."), self.flashes())
        approve = f"{PROG}/{prog_id}/aprobar"
        self.assertRegex(self.select(self.html(approve), "aprobado_por_id"),
                         rf'<option selected value="{self.ana}">Ana Pérez</option>')
        with patch(TODAY_IN_ROUTES, return_value=TODAY):
            self.post(approve, {"aprobado_por_id": str(self.ana)}, expect=f"{PROG}/{prog_id}")
        programme = self.load(ProgramaAuditoria, prog_id)
        self.assertEqual((EstadoProgramaAuditoria.aprobado, TODAY, self.ana),
                         (programme.estado, programme.aprobado_en, programme.aprobado_por_id))

        # An audit in it with a team: the area owner may not be in the team.
        form = AUDIT_FORM | {"programa_id": str(prog_id), "auditor_id": str(self.luis),
                             "equipo_ids": [str(self.eva), str(self.ines)],
                             "responsable_area_id": str(self.eva),
                             "alcance": "Proceso de compras"}
        page = self.rerendered(f"{AUD}/nueva", form)
        self.assertIn(audits.TEAM_OWNS_AREA, page)
        self.assertIn("Proceso de compras", page)
        self.assertRegex(self.select(page, "equipo_ids"),
                         rf'<option selected value="{self.ines}">Inés Mora</option>')
        self.assertEqual(0, self.count(Auditoria))
        self.post(f"{AUD}/nueva", form | {"responsable_area_id": str(self.ana)},
                  expect=f"{AUD}/")
        with self.app.app_context():
            created = Auditoria.query.one()
            audit_id = created.id
            self.assertEqual((prog_id, self.luis, self.ana, sorted([self.eva, self.ines])),
                             (created.programa_id, created.auditor_id,
                              created.responsable_area_id, created.equipo_ids))

        # Its page shows the planning fields and links back to the programme.
        page = self.html(self.detail(audit_id))
        for text in ("Programa 2026", "Proceso de compras", "ISO 9001:2026", "Eva Ruiz",
                     "Inés Mora", "Ana Pérez", "Luis Gil", "Dos hallazgos"):
            with self.subTest(text=text):
                self.assertIn(text, page)
        self.assertIn(f'href="{PROG}/{prog_id}"', page)

        # Findings of each type; the nonconformity ones open their nonconformities.
        for tipo, clause in (("nc_mayor", "8.5.1"), ("nc_menor", "7.1.5"),
                             ("observacion", ""), ("oportunidad_mejora", "10.3")):
            self.post(self.finding_url(audit_id), {
                "tipo": tipo, "clausula": clause, "descripcion": f"Hallazgo {tipo}",
                "evidencia": f"Evidencia {tipo}"}, expect=self.detail(audit_id))
            if tipo == "nc_mayor":
                with self.app.app_context():
                    nc_id = HallazgoAuditoria.query.one().no_conformidad_id
                self.assertIn(("success", "Hallazgo registrado. Se ha abierto la no "
                                          f"conformidad n.º {nc_id}."), self.flashes())
        with self.app.app_context():
            found = {f.tipo.name: (f.id, f.no_conformidad_id)
                     for f in HallazgoAuditoria.query}
        self.assertIsNone(found["observacion"][1])
        self.assertIsNone(found["oportunidad_mejora"][1])
        page = self.html(self.detail(audit_id))
        for label in ("No conformidad mayor", "No conformidad menor", "Observación",
                      "Oportunidad de mejora", "8.5.1", "7.1.5", "10.3",
                      "Evidencia observacion", "Hallazgo oportunidad_mejora"):
            with self.subTest(label=label):
                self.assertIn(label, page)
        self.assertEqual(2, len(re.findall(r'href="/no_conformidades/\d+"', page)))
        for tipo in ("nc_mayor", "nc_menor"):
            self.assertIn(f'href="/no_conformidades/{found[tipo][1]}"', page)

        # Edit the observation, then delete the major one: its nonconformity stays.
        obs_id = found["observacion"][0]
        self.assertIn("Hallazgo observacion", self.html(self.finding_url(audit_id, obs_id,
                                                                         "editar")))
        self.post(self.finding_url(audit_id, obs_id, "editar"), {
            "tipo": "observacion", "clausula": "7.5.3", "descripcion": "Acta sin firmar",
            "evidencia": "Acta 12"}, expect=self.detail(audit_id))
        self.assertIn(("success", "Hallazgo actualizado."), self.flashes())
        major_id, major_nc = found["nc_mayor"]
        self.post(self.finding_url(audit_id, major_id, "eliminar"),
                  expect=self.detail(audit_id))
        self.assertIsNone(self.load(HallazgoAuditoria, major_id))
        self.assertIsNotNone(self.load(NoConformidad, major_nc))
        page = self.html(self.detail(audit_id))
        self.assertIn("Acta sin firmar", page)
        self.assertIn("7.5.3", page)
        self.assertNotIn(f'href="/no_conformidades/{major_nc}"', page)

        # The programme counts its audit's findings by type.
        page = self.html(f"{PROG}/{prog_id}")
        for label, total in (("No conformidad mayor", 0), ("No conformidad menor", 1),
                             ("Observación", 1), ("Oportunidad de mejora", 1)):
            with self.subTest(count=label):
                self.assertIn(f'title="{label}">{total}</td>', page)
        self.assertIn(f'href="{self.detail(audit_id)}"', page)
        self.assertIn("Compras", page)
        listing = self.html(f"{PROG}/")
        self.assertIn("<td>1 auditoría</td>", listing)
        self.assertIn("Aprobado", listing)


class ProgrammeScreensTestCase(ScreensBase):
    def test_a_taken_or_invalid_year_shows_the_form_again_with_the_input(self) -> None:
        self.programme(2026)
        self.login(AUDITOR)
        page = self.rerendered(f"{PROG}/nuevo", {"anio": "2026", "objetivos": "Texto conservado",
                                                 "alcance": "Alcance conservado"})
        self.assertIn("Ya existe un programa de auditoría para 2026.", page)
        self.assertIn("Texto conservado", page)
        self.assertIn("Alcance conservado", page)
        page = self.rerendered(f"{PROG}/nuevo", {"anio": "1999", "objetivos": "Otro texto",
                                                 "alcance": "A"})
        self.assertIn("Otro texto", page)
        self.assertEqual(1, self.count(ProgramaAuditoria))

    def test_editing_keeps_an_approved_programme_year(self) -> None:
        pid = self.programme(2026, approved=True)
        self.login(AUDITOR)
        url = f"{PROG}/{pid}/editar"
        self.assertIn("Objetivos 2026", self.html(url))
        page = self.rerendered(url, {"anio": "2027", "objetivos": "Nuevos objetivos",
                                     "alcance": "Alcance 2026"})
        self.assertIn(audit_programmes.YEAR_LOCKED, page)
        self.assertIn("Nuevos objetivos", page)
        self.assertEqual("Objetivos 2026", self.load(ProgramaAuditoria, pid).objetivos)
        self.post(url, {"anio": "2026", "objetivos": "Nuevos objetivos", "alcance": "Alcance 2026"},
                  expect=f"{PROG}/{pid}")
        self.assertEqual("Nuevos objetivos",
                         self.load(ProgramaAuditoria, pid).objetivos)

    def test_only_administrators_approve_and_only_drafts(self) -> None:
        pid = self.programme(2026)
        url = f"{PROG}/{pid}/aprobar"
        for role in (AUDITOR, OPERATIVO):
            self.login(role)
            for method in ("get", "post"):
                with self.subTest(role=role.name, method=method):
                    self.denied(method, url)
        self.login(ADMIN)
        self.html(url)
        page = self.rerendered(url, {"aprobado_por_id": ""})
        self.assertIn("aprobado_por_id", page)
        self.assertIs(EstadoProgramaAuditoria.borrador,
                      self.load(ProgramaAuditoria, pid).estado)
        with patch(TODAY_IN_ROUTES, return_value=TODAY):
            self.post(url, {"aprobado_por_id": str(self.luis)}, expect=f"{PROG}/{pid}")
        self.assertIn(("success", "Programa de auditoría 2026 aprobado."), self.flashes())
        self.assertEqual(self.luis, self.load(ProgramaAuditoria, pid).aprobado_por_id)
        page = self.html(f"{PROG}/{pid}")
        self.assertIn("30/10/2026", page)
        self.assertIn("Luis Gil", page)
        for method in ("get", "post"):
            with self.subTest(again=method):
                response = getattr(self.client, method)(url, data={"aprobado_por_id": self.eva})
                self.assertEqual(f"{PROG}/{pid}", response.headers["Location"])
                self.assertIn(("danger", audit_programmes.NOT_DRAFT),
                              self.flashes())
        self.assertEqual(self.luis, self.load(ProgramaAuditoria, pid).aprobado_por_id)

    def test_only_administrators_delete_and_never_a_programme_with_audits(self) -> None:
        empty, used = self.programme(2025), self.programme(2026)
        self.audit(programa_id=used)
        self.login(AUDITOR)
        self.denied("post", f"{PROG}/{empty}/eliminar")
        self.login(ADMIN)
        self.post(f"{PROG}/{used}/eliminar", expect=f"{PROG}/{used}")
        self.assertIn(("danger", audit_programmes.HAS_AUDITS), self.flashes())
        self.post(f"{PROG}/{empty}/eliminar", expect=f"{PROG}/")
        self.assertIn(("success", "Programa de auditoría eliminado."), self.flashes())
        self.assertIsNone(self.load(ProgramaAuditoria, empty))
        self.assertIsNotNone(self.load(ProgramaAuditoria, used))

    def test_a_programme_page_says_when_it_shows_only_its_first_audits(self) -> None:
        # AP-3 review: which audit comes first is not part of this contract, so
        # the test counts the audit links instead of naming the one left out.
        pid = self.programme(2026)
        for area in ("Compras", "Ventas"):
            self.audit(programa_id=pid, area_auditada=area)
        self.login(AUDITOR)
        audit_links = rf'href="{AUD}/\d+"'
        with patch("app.services.crud.MAX_PER_PAGE", 1):
            page = self.html(f"{PROG}/{pid}")
        self.assertIn("Se muestran las 1 primeras de 2 auditorías.", page)
        self.assertEqual(1, len(re.findall(audit_links, page)))
        page = self.html(f"{PROG}/{pid}")
        self.assertNotIn("primeras", page)
        self.assertEqual(2, len(re.findall(audit_links, page)))

    def test_missing_programmes_answer_404(self) -> None:
        self.login(ADMIN)
        for method, url in (("get", f"{PROG}/999"), ("get", f"{PROG}/999/editar"),
                            ("post", f"{PROG}/999/editar"), ("get", f"{PROG}/999/aprobar"),
                            ("post", f"{PROG}/999/aprobar"), ("post", f"{PROG}/999/eliminar")):
            with self.subTest(method=method, url=url):
                self.assertEqual(404, getattr(self.client, method)(url).status_code)


class AuditFormTestCase(ScreensBase):
    def test_the_programme_picker_offers_draft_and_approved_programmes(self) -> None:
        self.programme(2026, approved=True)
        draft = self.programme(2027)
        self.login(AUDITOR)
        picker = self.select(self.html(f"{AUD}/nueva"), "programa_id")
        self.assertIn("Programa 2026 (Aprobado)", picker)
        self.assertIn("Programa 2027 (Borrador)", picker)
        picker = self.select(self.html(f"{AUD}/nueva?programa_id={draft}"), "programa_id")
        self.assertIn(f'<option selected value="{draft}">', picker)

    def test_a_date_outside_the_programme_year_shows_the_form_again_with_the_input(self) -> None:
        pid = self.programme(2026)
        existing = self.audit()
        self.login(AUDITOR)
        form = AUDIT_FORM | {"programa_id": str(pid), "fecha": "2027-01-15",
                             "alcance": "Alcance conservado", "equipo_ids": [str(self.eva)]}
        for url in (f"{AUD}/nueva", f"{AUD}/editar/{existing}"):
            with self.subTest(url=url):
                page = self.rerendered(url, form)
                self.assertIn("La fecha de la auditoría debe estar en 2026, el año de su "
                              "programa.", page)
                self.assertIn("Alcance conservado", page)
                self.assertRegex(self.select(page, "equipo_ids"),
                                 rf'<option selected value="{self.eva}">Eva Ruiz</option>')
        self.assertEqual(1, self.count(Auditoria))
        self.assertIsNone(self.load(Auditoria, existing).programa_id)


class FindingScreensTestCase(ScreensBase):
    def test_the_audit_list_links_to_each_audit_page(self) -> None:
        audit_id = self.audit()
        self.login(AUDITOR)
        self.assertIn(f'href="{self.detail(audit_id)}"', self.html(f"{AUD}/"))

    def test_a_finding_asked_under_another_audit_or_a_missing_audit_is_a_404(self) -> None:
        first, second = self.audit(), self.audit(area_auditada="Ventas")
        finding_id, _ = self.finding(first)
        self.login(ADMIN)
        for method, url in (("get", self.finding_url(second, finding_id, "editar")),
                            ("post", self.finding_url(second, finding_id, "editar")),
                            ("post", self.finding_url(second, finding_id, "eliminar")),
                            ("get", self.detail(999)), ("get", self.finding_url(999)),
                            ("post", self.finding_url(999))):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data={
                    "tipo": "observacion", "descripcion": "Otra"})
                self.assertEqual(404, response.status_code)
        self.assertEqual("Registro sin firmar",
                         self.load(HallazgoAuditoria, finding_id).descripcion)

    def test_the_edit_page_shows_the_finding_as_stored(self) -> None:
        # AP-3 review: the stored type is preselected (the last choice, so not
        # the browser's default) and the clause and evidence come back.
        audit_id = self.audit()
        finding_id, _ = self.finding(audit_id, tipo="oportunidad_mejora", clausula="7.1.5",
                                     evidencia="Calibración vencida")
        self.login(AUDITOR)
        page = self.html(self.finding_url(audit_id, finding_id, "editar"))
        tipo = self.select(page, "tipo")
        self.assertIn('<option selected value="oportunidad_mejora">Oportunidad de mejora</option>',
                      tipo)
        self.assertEqual(1, tipo.count("selected"))
        self.assertRegex(page, r'<input[^>]*name="clausula"[^>]*value="7\.1\.5"')
        self.assertRegex(page,
                         r'<textarea[^>]*name="evidencia"[^>]*>\s*Calibración vencida</textarea>')
        self.assertIn("Registro sin firmar", page)

    def test_an_invalid_clause_shows_the_form_again_with_the_input(self) -> None:
        audit_id = self.audit()
        finding_id, _ = self.finding(audit_id)
        self.login(AUDITOR)
        message = audit_findings.BAD_CLAUSE
        for url, clause in ((self.finding_url(audit_id), "11.2"),
                            (self.finding_url(audit_id, finding_id, "editar"), "3")):
            with self.subTest(url=url):
                page = self.rerendered(url, {"tipo": "nc_menor", "clausula": clause,
                                             "descripcion": "Texto conservado",
                                             "evidencia": "Evidencia conservada"})
                self.assertIn(message, page)
                self.assertIn("Texto conservado", page)
                self.assertIn("Evidencia conservada", page)
        self.assertEqual(1, self.count(HallazgoAuditoria))
        self.assertEqual(0, self.count(NoConformidad))

    def test_a_cancelled_audit_shows_its_findings_read_only(self) -> None:
        audit_id = self.audit()
        finding_id, nc_id = self.finding(audit_id, tipo="nc_menor")
        with self.app.app_context():
            audits.update(db.session, SYSTEM, audit_id, {"estado": "CANCELADA"})
            db.session.commit()
        self.login(ADMIN)
        page = self.html(self.detail(audit_id))
        self.assertIn("Registro sin firmar", page)
        self.assertIn(f'href="/no_conformidades/{nc_id}"', page)
        self.assertIn("Una auditoría cancelada no admite hallazgos nuevos ni cambios.", page)
        for marker in (f'href="{self.finding_url(audit_id)}"',
                       f'href="{self.finding_url(audit_id, finding_id, "editar")}"',
                       f'action="{self.finding_url(audit_id, finding_id, "eliminar")}"'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, page)
        cancelled = audit_findings.AUDIT_CANCELLED
        rows = self.audit_rows()
        for method, url in (("get", self.finding_url(audit_id)),
                            ("post", self.finding_url(audit_id)),
                            ("get", self.finding_url(audit_id, finding_id, "editar")),
                            ("post", self.finding_url(audit_id, finding_id, "editar")),
                            ("post", self.finding_url(audit_id, finding_id, "eliminar"))):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, data={
                    "tipo": "observacion", "descripcion": "Nueva"})
                self.assertEqual(self.detail(audit_id), response.headers["Location"])
                self.assertIn(("danger", cancelled), self.flashes())
        self.assertEqual(rows, self.audit_rows())
        self.assertEqual(1, self.count(HallazgoAuditoria))

    def test_a_refused_audit_delete_returns_to_its_page_with_the_reason(self) -> None:
        audit_id = self.audit()
        self.finding(audit_id)
        self.login(ADMIN)
        self.post(f"{AUD}/eliminar/{audit_id}", expect=self.detail(audit_id))
        self.assertIn(("danger", audits.HAS_FINDINGS), self.flashes())
        self.assertIsNotNone(self.load(Auditoria, audit_id))


class PdfTestCase(ScreensBase):
    def test_the_audit_pdf_holds_its_planning_fields_and_findings(self) -> None:
        pid = self.programme(2026, approved=True)
        audit_id = self.audit(programa_id=pid, auditor_id=self.luis,
                              responsable_area_id=self.ana, equipo_ids=[self.ines, self.eva],
                              alcance="Proceso de compras", criterios="ISO 9001:2026",
                              estado="EN_PROCESO", accion_correctiva="Revisar el registro")
        _, nc_id = self.finding(audit_id, tipo="nc_mayor", clausula="8.5.1",
                                descripcion="Pedido sin aprobar", evidencia="Pedido 431")
        self.finding(audit_id)  # an observation without clause or evidence
        self.login(AUDITOR)
        html = self.pdf_html(f"{AUD}/exportar_pdf/{audit_id}", AUDIT_ROUTES)
        self.assertEqual({
            "Programa": "Programa 2026 · Aprobado",
            "Área auditada": "Compras",
            "Responsable del área": "Ana Pérez",
            "Auditor jefe": "Luis · Persona: Luis Gil",
            "Equipo auditor": "Eva Ruiz, Inés Mora",
            "Fecha": "01/10/2026",
            "Estado": "En Proceso",
        }, self.pdf_fields(html))
        for text in ("Proceso de compras", "ISO 9001:2026", "Correcta", "Revisar el registro"):
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertIn("<tr><td>No conformidad mayor</td><td>8.5.1</td><td>Pedido sin aprobar</td>"
                      f"<td>Pedido 431</td><td>N.º {nc_id}</td></tr>", html)
        self.assertIn("<tr><td>Observación</td><td>—</td><td>Registro sin firmar</td>"
                      "<td>—</td><td>—</td></tr>", html)

    def test_an_audit_pdf_without_planning_or_findings_says_so(self) -> None:
        audit_id = self.audit()
        self.login(ADMIN)
        html = self.pdf_html(f"{AUD}/exportar_pdf/{audit_id}", AUDIT_ROUTES)
        fields = self.pdf_fields(html)
        for label in ("Programa", "Responsable del área", "Equipo auditor"):
            with self.subTest(label=label):
                self.assertEqual("—", fields[label])
        self.assertEqual("Luis", fields["Auditor jefe"])
        self.assertIn("Sin hallazgos.", html)
        self.assertEqual(2, html.count("No indicado"))  # scope and criteria

    def test_the_programme_pdf_holds_its_approval_and_its_audits_by_date(self) -> None:
        pid = self.programme(2026, approved=True)  # approved by Luis on 15/01/2026
        later = self.audit(programa_id=pid, area_auditada="Ventas", fecha=date(2026, 11, 3),
                           auditor="", auditor_id=self.eva, estado="COMPLETADA")
        earlier = self.audit(programa_id=pid, area_auditada="Almacén", fecha=date(2026, 3, 2))
        for tipo in ("nc_menor", "observacion", "observacion"):
            self.finding(later, tipo=tipo)
        self.finding(earlier, tipo="oportunidad_mejora")
        self.audit(area_auditada="Fuera del programa")
        self.login(AUDITOR)
        html = self.pdf_html(f"{PROG}/{pid}/pdf", PROG_ROUTES)
        self.assertEqual({"Año": "2026", "Estado": "Aprobado", "Aprobado por": "Luis Gil",
                          "Fecha de aprobación": "15/01/2026"}, self.pdf_fields(html))
        self.assertIn("Objetivos 2026", html)
        self.assertIn("Alcance 2026", html)
        headers = re.findall(r'<th class="num">([^<]+)</th>', html)
        self.assertEqual([tipo.value for tipo in TipoHallazgo], headers)
        rows = re.findall(r"<tr><td>.*?</tr>", html)
        self.assertEqual([
            "<tr><td>02/03/2026</td><td>Almacén</td><td>Luis</td><td>Pendiente</td>"
            '<td class="num">0</td><td class="num">0</td><td class="num">0</td>'
            '<td class="num">1</td></tr>',
            "<tr><td>03/11/2026</td><td>Ventas</td><td>Eva Ruiz</td><td>Completada</td>"
            '<td class="num">0</td><td class="num">1</td><td class="num">2</td>'
            '<td class="num">0</td></tr>',
        ], rows)

    def test_a_programme_pdf_that_cannot_be_rendered_returns_to_the_programme(self) -> None:
        pid = self.programme(2026)
        self.login(AUDITOR)
        with patch(f"{PROG_ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")):
            response = self.client.get(f"{PROG}/{pid}/pdf")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{PROG}/{pid}"))

    def test_a_programme_pdf_lists_every_audit_and_a_draft_shows_no_approval(self) -> None:
        pid = self.programme(2026)
        for area in ("Compras", "Ventas", "Almacén"):
            self.audit(programa_id=pid, area_auditada=area)
        self.login(ADMIN)
        with patch("app.services.crud.MAX_PER_PAGE", 1):
            html = self.pdf_html(f"{PROG}/{pid}/pdf", PROG_ROUTES)
        for area in ("Compras", "Ventas", "Almacén"):
            with self.subTest(area=area):
                self.assertIn(f"<td>{area}</td>", html)
        fields = self.pdf_fields(html)
        self.assertEqual(("Borrador", "—", "—"), (
            fields["Estado"], fields["Aprobado por"], fields["Fecha de aprobación"]))
        empty = self.pdf_html(f"{PROG}/{self.programme(2027)}/pdf", PROG_ROUTES)
        self.assertIn("Este programa todavía no tiene auditorías.", empty)

    def test_the_programme_pdf_has_the_access_of_the_programme_page(self) -> None:
        pid = self.programme(2026)
        url = f"{PROG}/{pid}/pdf"
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.login(role)
                response = self.client.get(url)
                self.assertEqual(200, response.status_code)
                self.assertEqual("application/pdf", response.mimetype)
                self.assertIn("programa_auditoria_2026.pdf",
                              response.headers["Content-Disposition"])
                self.assertIn(f'href="{url}"', self.html(f"{PROG}/{pid}"))
        self.login(OPERATIVO)
        self.denied("get", url)
        self.login(ADMIN)
        self.assertEqual(404, self.client.get(f"{PROG}/999/pdf").status_code)


class RoleVisibilityTestCase(ScreensBase):
    def test_operativo_reaches_none_of_the_new_screens(self) -> None:
        pid = self.programme(2026)
        audit_id = self.audit()
        finding_id, _ = self.finding(audit_id)
        rows = self.audit_rows()
        self.login(OPERATIVO)
        for url in (f"{PROG}/", f"{PROG}/{pid}", f"{PROG}/nuevo", f"{PROG}/{pid}/editar",
                    f"{PROG}/{pid}/aprobar", self.detail(audit_id),
                    self.finding_url(audit_id),
                    self.finding_url(audit_id, finding_id, "editar")):
            with self.subTest(get=url):
                self.denied("get", url)
        for url in (f"{PROG}/nuevo", f"{PROG}/{pid}/editar", f"{PROG}/{pid}/aprobar",
                    f"{PROG}/{pid}/eliminar", self.finding_url(audit_id),
                    self.finding_url(audit_id, finding_id, "editar"),
                    self.finding_url(audit_id, finding_id, "eliminar")):
            with self.subTest(post=url):
                self.denied("post", url)
        self.assertEqual(rows, self.audit_rows())

    def test_auditors_write_programmes_and_findings_but_neither_approve_nor_delete(self) -> None:
        pid = self.programme(2026)
        audit_id = self.audit()
        finding_id, _ = self.finding(audit_id)
        self.login(AUDITOR)
        self.denied("post", f"{PROG}/{pid}/aprobar")
        self.denied("post", f"{PROG}/{pid}/eliminar")
        self.denied("post", self.finding_url(audit_id, finding_id, "eliminar"))
        self.post(f"{PROG}/{pid}/editar", {"anio": "2026", "objetivos": "Revisados",
                                           "alcance": "Todo"}, expect=f"{PROG}/{pid}")
        self.post(self.finding_url(audit_id, finding_id, "editar"), {
            "tipo": "oportunidad_mejora", "clausula": "", "descripcion": "Mejorar el registro",
            "evidencia": ""}, expect=self.detail(audit_id))
        self.assertEqual("Revisados", self.load(ProgramaAuditoria, pid).objetivos)
        self.assertIs(TipoHallazgo.oportunidad_mejora,
                      self.load(HallazgoAuditoria, finding_id).tipo)


class CsrfTestCase(ScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def forms(self, url: str) -> list[dict]:
        page = _PostForms()
        page.feed(self.html(url))
        return page.forms

    def test_every_post_form_on_the_new_pages_carries_a_token(self) -> None:
        pid = self.programme(2026)
        audit_id = self.audit()
        finding_id, _ = self.finding(audit_id)
        self.login(ADMIN)
        cases = ((f"{PROG}/{pid}", f"{PROG}/{pid}/eliminar", f"{PROG}/"),
                 (self.detail(audit_id), self.finding_url(audit_id, finding_id, "eliminar"),
                  self.detail(audit_id)))
        for page, action, target in cases:
            with self.subTest(page=page):
                (form,) = self.forms(page)
                self.assertEqual(action, form["action"])
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
                self.post(action, form["fields"], expect=target)
        self.assertEqual(0, self.count(HallazgoAuditoria))

    def test_the_programme_and_finding_forms_need_their_token(self) -> None:
        pid = self.programme(2026)
        audit_id = self.audit()
        self.login(ADMIN)
        cases = (
            (f"{PROG}/nuevo", {"anio": "2027", "objetivos": "O", "alcance": "A"}, None),
            (f"{PROG}/{pid}/editar", {"anio": "2026", "objetivos": "O2", "alcance": "A"},
             f"{PROG}/{pid}"),
            (f"{PROG}/{pid}/aprobar", {"aprobado_por_id": str(self.ana)}, f"{PROG}/{pid}"),
            (self.finding_url(audit_id), {"tipo": "observacion", "descripcion": "D"},
             self.detail(audit_id)),
        )
        for url, data, target in cases:
            with self.subTest(url=url):
                self.assertEqual(400, self.client.post(url, data=data).status_code)
                (form,) = self.forms(url)
                self.post(url, form["fields"] | data, expect=target)
        self.assertEqual(2, self.count(ProgramaAuditoria))
        self.assertIs(EstadoProgramaAuditoria.aprobado,
                      self.load(ProgramaAuditoria, pid).estado)
        self.assertEqual(1, self.count(HallazgoAuditoria))


if __name__ == "__main__":
    unittest.main()
