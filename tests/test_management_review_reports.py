"""Management review minutes (task MR-3 of ``management-review``).

The minutes PDF (``/revisiones-direccion/<id>/pdf``) is checked on the HTML its
view renders: ``render_pdf`` is replaced so the test keeps that HTML, as in
``test_quality_objective_reports`` (the access characterization renders the
real PDF). The minutes carry the meeting, the inputs of 9.3.2 with their
comments (frozen once held; live and marked provisional in a draft), the
conclusions and the actions of 9.3.3; a PDF that cannot be rendered returns to
the review's page. Administrators and auditors read them; other roles are
refused.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_audit_screens import TODAY as GENERATED  # the date ``pdf_html`` renders with
from test_management_review_screens import BASE, OLD_FROZEN, ROUTES, TODAY, ReviewScreensBase

from app.extensions import db
from app.forms import REVIEW_INPUTS
from app.services import management_reviews as mr
from app.services import qms_changes

DRAFT_MARK = "BORRADOR — datos provisionales"
FROZEN_NOTE = "Datos congelados al celebrar la revisión"
NO_REGISTER = "Sin datos de los registros: se valora en el comentario."
SECTION = re.compile(r'<section class="entrada">(.*?)</section>', re.S)
TITLE = re.compile(r"<h3>([^<]+)</h3>")
ACTION_ROW = re.compile(r'<tr class="accion">(.*?)</tr>', re.S)
CONTEXT, PERFORMANCE, RISKS = (REVIEW_INPUTS[name] for name in (
    "comentario_contexto", "comentario_desempeno", "comentario_riesgos"))
CONCLUSIONS = "El SGC es conveniente, adecuado y eficaz."


class MinutesPdfTestCase(ReviewScreensBase):
    def minutes(self, review_id: int) -> tuple[dict[str, str], dict[str, str], str]:
        """The minutes' header fields, its input sections by title and the whole HTML."""
        html = self.pdf_html(f"{BASE}/{review_id}/pdf", ROUTES)
        header, _, _ = html.partition('<section class="entrada">')
        sections = {TITLE.search(section).group(1): section for section in SECTION.findall(html)}
        return self.pdf_fields(header), sections, html

    def test_the_minutes_of_a_closed_review_hold_its_frozen_inputs_and_its_actions(self) -> None:
        self.nonconformity(date(2026, 5, 10))
        rid = self.review([self.luis, self.ana], comentario_contexto="Nuevo competidor")
        self.login(ADMIN)
        self.post(f"{BASE}/{rid}/celebrar", expect=f"{BASE}/{rid}")
        self.nonconformity(date(2026, 6, 15))  # after holding: not in the minutes
        self.action(rid, tipo="recursos", descripcion="Contratar un técnico de calidad",
                    fecha_limite=date(2026, 9, 30))
        self.action(rid, descripcion="Automatizar pedidos", responsable_id=self.luis,
                    estado="completada", fecha_completada=date(2026, 8, 1),
                    resultado="Pedidos automatizados")
        with self.app.app_context():
            mr.reviews.update(db.session, SYSTEM, rid, {"conclusiones": CONCLUSIONS})
            db.session.commit()
        self.post(f"{BASE}/{rid}/cerrar", expect=f"{BASE}/{rid}")
        self.login(AUDITOR)

        header, sections, html = self.minutes(rid)

        self.assertIn("<h1>Acta de revisión por la dirección</h1>", html)
        self.assertEqual({
            "Fecha de la reunión": "10/07/2026", "Periodo revisado": "01/04/2026 – 30/06/2026",
            "Estado": "Cerrada", "Asistentes": "Ana Pérez, Luis Gil",
            "Celebrada por": "Ana Pérez", "Fecha de cierre": "12/10/2026",
            "Fecha de corte de los datos": "30/06/2026",
        }, header)
        self.assertEqual(list(REVIEW_INPUTS.values()), list(sections))
        self.assertEqual("1", self.pdf_fields(sections[PERFORMANCE])["Detectadas en el periodo"])
        self.assertIn("Nuevo competidor", sections[CONTEXT])
        self.assertIn(NO_REGISTER, sections[RISKS])
        self.assertEqual(5, html.count("Sin comentario."))
        self.assertIn(FROZEN_NOTE, html)
        self.assertNotIn(DRAFT_MARK, html)
        self.assertIn(CONCLUSIONS, html)
        self.assertEqual([
            "<td>Necesidad de recursos</td><td>Contratar un técnico de calidad</td>"
            "<td>Eva Ruiz</td><td>30/09/2026 · vencida</td><td>Pendiente</td><td>—</td>"
            "<td>—</td>",
            "<td>Oportunidad de mejora</td><td>Automatizar pedidos</td><td>Luis Gil</td>"
            "<td>31/12/2026</td><td>Completada</td><td>01/08/2026</td>"
            "<td>Pedidos automatizados</td>",
        ], ACTION_ROW.findall(html))
        self.assertIn(f"Acta generada el {GENERATED:%d/%m/%Y}.", html)

    def test_the_minutes_of_a_draft_show_the_live_inputs_marked_provisional(self) -> None:
        rid = self.review()
        self.nonconformity(date(2026, 5, 10))
        self.login(ADMIN)

        header, sections, html = self.minutes(rid)

        self.assertEqual({
            "Fecha de la reunión": "10/07/2026", "Periodo revisado": "01/04/2026 – 30/06/2026",
            "Estado": "Borrador", "Asistentes": "—", "Celebrada por": "—",
            "Fecha de cierre": "—", "Fecha de corte de los datos": "30/06/2026",
        }, header)
        self.assertIn(DRAFT_MARK, html)
        self.assertNotIn(FROZEN_NOTE, html)
        self.assertEqual("1", self.pdf_fields(sections[PERFORMANCE])["Detectadas en el periodo"])
        for text in ("Sin conclusiones todavía.", "Sin acciones."):
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertEqual(6, html.count("Sin comentario."))
        self.assertEqual([], ACTION_ROW.findall(html))
        self.nonconformity(date(2026, 6, 1))
        _, sections, _ = self.minutes(rid)
        self.assertEqual("2", self.pdf_fields(sections[PERFORMANCE])["Detectadas en el periodo"])

    def test_the_minutes_carry_the_risk_registers_or_the_old_frozen_risks(self) -> None:
        self.registers()
        draft = self.review([self.ana])
        old = self.held_with(OLD_FROZEN)
        self.login(AUDITOR)
        _, sections, _ = self.minutes(draft)
        figures = self.pdf_fields(sections[CONTEXT])
        self.assertEqual(("2", "1", "1", "1", "1"), tuple(figures[label] for label in (
            "Riesgos registrados", "Nivel: Crítico", "Nivel: Sin valorar",
            "Abiertos con nivel alto o crítico", "Prioridad: Alta")))
        self.assertNotIn("Registrados: Riesgo", figures)
        self.assertIn("<td>Fallo de proveedor</td><td>Crítico</td><td>Eva Ruiz</td>"
                      "<td>Identificado</td>", sections[CONTEXT])
        header, sections, html = self.minutes(old)
        figures = self.pdf_fields(sections[CONTEXT])
        self.assertEqual(("2", "1", "1", "1"), tuple(figures[label] for label in (
            "Registrados: Riesgo", "Registrados: Oportunidad", "Nuevos en el periodo",
            "Modificados en el periodo")))
        self.assertIn("<td>Riesgo</td><td>Fallo de proveedor</td>", sections[CONTEXT])
        self.assertNotIn("Nivel:", html)
        self.assertEqual("30/06/2026", header["Fecha de corte de los datos"])
        self.assertIn(FROZEN_NOTE, html)

    def test_the_minutes_carry_the_providers_unless_held_before_them(self) -> None:
        self.suppliers()
        draft = self.review([self.ana])
        old = self.held_with(self.before_providers())
        self.login(AUDITOR)
        _, sections, _ = self.minutes(draft)
        figures = self.pdf_fields(sections[PERFORMANCE])
        self.assertEqual(("1", "2", "3,13", "1", "2", "1"), tuple(figures[label] for label in (
            "Estado actual: Aprobado", "Evaluaciones en el periodo",
            "Puntuación media de las evaluaciones", "Proveedores no aptos en el periodo",
            "Detectadas en el periodo con origen proveedor", "Sin proveedor indicado")))
        self.assertIn("<td>Transportes Rápidos</td><td>20/06/2026</td><td>2,25</td>"
                      "<td>Pendiente</td>", sections[PERFORMANCE])
        self.assertIn("<td>Aceros del Norte</td><td>1</td>", sections[PERFORMANCE])
        _, sections, html = self.minutes(old)
        self.assertEqual("2", self.pdf_fields(sections[PERFORMANCE])["Detectadas en el periodo"])
        for text in ("Proveedores externos", "Estado actual:", "Sin proveedor indicado"):
            self.assertNotIn(text, html)

    def test_the_minutes_carry_the_context_issues_and_scope_unless_held_before_them(
            self) -> None:
        self.context()
        draft = self.review([self.ana])
        old = self.held_with(self.before_context())
        self.login(AUDITOR)
        _, sections, _ = self.minutes(draft)
        figures = self.pdf_fields(sections[CONTEXT])
        self.assertEqual(("1", "1", "1", "1", "2", "04/05/2026"), tuple(
            figures[label] for label in (
                "Cuestiones nuevas en el periodo", "Cuestiones superadas en el periodo",
                "Vigentes sobre el cambio climático", "Pertinentes sobre el cambio climático",
                "Versión vigente del alcance", "Fecha de aprobación del alcance")))
        self.assertIn("<td>Normativa derogada</td><td>Modificada</td><td>Externa</td>"
                      "<td>Legal</td><td>Sí</td><td>No</td><td>Superada</td>",
                      sections[CONTEXT])
        _, sections, html = self.minutes(old)
        self.assertIn("Riesgos registrados", self.pdf_fields(sections[CONTEXT]))
        for text in ("Cuestiones vigentes", "Versión vigente del alcance", "Normativa derogada"):
            self.assertNotIn(text, html)

    def test_the_minutes_carry_the_changes_unless_held_before_them(self) -> None:
        self.qms_changes()
        draft = self.review([self.ana])
        old = self.held_with(self.before_changes())
        self.login(AUDITOR)
        _, sections, _ = self.minutes(draft)
        figures = self.pdf_fields(sections[PERFORMANCE])
        self.assertEqual(("15", "13", "12", "1", "1", "1", "12", "1"), tuple(
            figures[label] for label in (
                "Cambios propuestos en el periodo", "Cambios aprobados en el periodo",
                "Cambios implantados en el periodo", "Con la eficacia revisada en el periodo",
                "Revisados como no eficaces", "Cambios cancelados en el periodo",
                "Implantados o revisados en el periodo", "Aprobados fuera de plazo al cierre")))
        self.assertIn("<td>Portal de clientes</td><td>Eficacia revisada</td><td>30/04/2026</td>"
                      "<td>20/06/2026</td><td>No eficaz</td>", sections[PERFORMANCE])
        self.assertIn("<td>ERP con retraso</td><td>Eva Ruiz</td><td>03/04/2026</td>"
                      "<td>15/06/2026</td><td>Aprobado</td>", sections[PERFORMANCE])
        self.assertIn(f"Se muestran los {mr.LIST_LIMIT} más recientes de 12.",
                      sections[PERFORMANCE])
        _, sections, html = self.minutes(old)
        self.assertIn("Detectadas en el periodo", self.pdf_fields(sections[PERFORMANCE]))
        for text in ("Cambios en el SGC", "Cambios propuestos en el periodo", "ERP con retraso"):
            self.assertNotIn(text, html)

    def test_the_minutes_name_the_changes_each_action_gives_rise_to(self) -> None:
        rid = self.review([self.ana])
        action = self.action(rid, tipo="cambio_sgc", descripcion="Implantar un ERP")
        self.action(rid, descripcion="Automatizar pedidos")
        with self.app.app_context():
            qms_changes.changes.create(db.session, SYSTEM, {
                "titulo": "Nuevo ERP", "descripcion": "d", "origen": "revision_direccion",
                "accion_revision_id": action, "proposito": "p", "responsable_id": self.eva,
                "fecha_propuesta": TODAY}, today=TODAY)
            db.session.commit()
        self.login(ADMIN)
        _, _, html = self.minutes(rid)
        rows = ACTION_ROW.findall(html)
        self.assertIn("<td>Implantar un ERP · Cambios: Nuevo ERP (Propuesto)</td>",
                      next(row for row in rows if "Implantar un ERP" in row))
        self.assertIn("<td>Automatizar pedidos</td>",
                      next(row for row in rows if "Automatizar pedidos" in row))

    def test_a_minutes_pdf_that_cannot_be_rendered_returns_to_the_review(self) -> None:
        rid = self.review([self.ana])
        self.login(AUDITOR)
        with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                self.assertLogs(self.app.logger, "ERROR") as logged:
            response = self.client.get(f"{BASE}/{rid}/pdf")
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/{rid}", response.headers["Location"])
        self.assertIn(("danger", "Error al generar el acta de la revisión por la dirección."),
                      self.flashes())
        self.assertIn(f"management review {rid}", logged.output[0])

    def test_administrators_and_auditors_read_the_minutes_from_the_review_page(self) -> None:
        rid = self.review([self.ana])
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.login(role)
                self.assertIn(f'href="{BASE}/{rid}/pdf"', self.html(f"{BASE}/{rid}"))
                with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                    response = self.client.get(f"{BASE}/{rid}/pdf")
                self.assertEqual(200, response.status_code)
                self.assertEqual("application/pdf", response.mimetype)
                self.assertIn("acta_revision_direccion_2026-07-10.pdf",
                              response.headers["Content-Disposition"])
                self.assertEqual(404, self.client.get(f"{BASE}/999/pdf").status_code)
        self.login(OPERATIVO)
        self.denied("get", f"{BASE}/{rid}/pdf")
