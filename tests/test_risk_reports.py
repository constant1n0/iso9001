"""Risk and opportunity register PDFs (RO-4 of ``risks-opportunities``).

``/riesgos/pdf`` and ``/oportunidades/pdf`` (ISO 9001 clause 6.1) are checked on
the HTML their view renders: ``render_pdf`` is replaced so the test keeps that
HTML, as in ``test_quality_objective_reports`` (the access characterization
renders the real PDF). The header counts the open records by level or priority,
the unassessed ones and those overdue for review; the risks' header also holds
the 5 × 5 heat map as a table. The open records come first, the closed ones in
their own section, each ordered by level (or priority) from the highest, then
by title, with its actions. Every role reads both PDFs.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, SYSTEM
from test_risk_screens import OPPORTUNITIES, RISKS, ROUTES, TODAY, RiskScreensBase

from app.extensions import db
from app.models import RoleEnum
from app.services import risks

BLOCK = re.compile(r'<section class="registro">(.*?)</section>', re.S)
TITLE = re.compile(r"<h3>([^<]+)</h3>")
ACTION_ROW = re.compile(r"<tr><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>"
                        r"<td>([^<]*)</td><td>([^<]*)</td></tr>")
CELL = re.compile(r'<td id="mapa-(\d)-(\d)" class="mapa__celda mapa__celda--(\w+)">(\d+)</td>')
CLOSED = '<h2 class="grupo" id="cerrados">'


class RegisterPdfBase(RiskScreensBase):
    def register_pdf(self, base: str) -> tuple[dict[str, str], dict[str, str],
                                               dict[str, str], str]:
        """The PDF's header fields, its open and closed blocks by title, and its HTML."""
        html = self.pdf_html(f"{base}/pdf", ROUTES)
        header, _, records = html.partition('<h2 class="grupo" id="abiertos">')
        opened, _, closed = records.partition(CLOSED)

        def blocks(part: str) -> dict[str, str]:
            return {TITLE.search(block).group(1): block for block in BLOCK.findall(part)}

        return self.pdf_fields(header), blocks(opened), blocks(closed), html


class RiskRegisterPdfTestCase(RegisterPdfBase):
    def test_the_risk_pdf_counts_maps_and_lists_each_risk_by_level_then_title(self) -> None:
        process_id = self.process("Compras")
        critical = self.risk(titulo="Proveedor único", origen="externo", proceso_id=process_id,
                             probabilidad=5, impacto=5, tratamiento="mitigar",
                             probabilidad_residual=2, impacto_residual=3,
                             estado="en_tratamiento", fecha_revision=date(2026, 12, 31))
        done = self.action(riesgo_id=critical, descripcion="Segundo proveedor",
                           fecha_limite=date(2026, 9, 30), estado="completada",
                           fecha_completada=date(2026, 9, 15))
        self.action(riesgo_id=critical, descripcion="Auditar al proveedor",
                    responsable_id=self.ines, fecha_limite=date(2026, 10, 1))
        with self.app.app_context():
            risks.actions.update(db.session, SYSTEM, done, {
                "eficacia": "eficaz", "fecha_evaluacion": date(2026, 10, 1),
                "comentario_eficacia": "Sin roturas de stock"}, today=TODAY)
            db.session.commit()
        self.risk(titulo="Rotación de personal", probabilidad=4, impacto=4)  # alto, 16
        self.risk(titulo="Avería de la prensa", probabilidad=3, impacto=4)  # alto, 12
        self.risk(titulo="Bajada de ventas", probabilidad=1, impacto=2,
                  fecha_identificacion=date(2026, 1, 1), fecha_revision=date(2026, 9, 30))
        self.risk(titulo="Sin valorar todavía")
        closed = self.risk(titulo="Incendio del almacén", probabilidad=5, impacto=4,
                           fecha_identificacion=date(2026, 1, 1),
                           fecha_revision=date(2026, 9, 30))
        self.update("risks", closed, {"estado": "cerrado"})
        self.login(AUDITOR)

        header, opened, closed_blocks, html = self.register_pdf(RISKS)

        self.assertEqual({
            "Riesgos abiertos": "5", "Crítico": "1", "Alto": "2", "Medio": "0", "Bajo": "1",
            "Sin valorar": "1", "Revisión vencida": "1", "Riesgos cerrados": "1",
        }, header)
        cells = {(int(p), int(i)): (band, int(count))
                 for p, i, band, count in CELL.findall(html)}
        self.assertEqual(25, len(cells))
        counted = {cell: count for cell, (_, count) in cells.items() if count}
        self.assertEqual({(5, 5): 1, (4, 4): 1, (3, 4): 1, (1, 2): 1}, counted)
        self.assertEqual(("critico", "bajo", "medio"),
                         (cells[5, 5][0], cells[1, 1][0], cells[3, 3][0]))
        self.assertEqual(["Proveedor único", "Avería de la prensa", "Rotación de personal",
                          "Bajada de ventas", "Sin valorar todavía"], list(opened))
        self.assertEqual(["Incendio del almacén"], list(closed_blocks))
        self.assertEqual({
            "Proceso": "Compras", "Responsable": "Eva Ruiz", "Origen": "Externo",
            "Nivel": "Crítico (5 × 5 = 25)", "Tratamiento": "Mitigar",
            "Nivel residual": "Medio (2 × 3 = 6)", "Estado": "En tratamiento",
            "Próxima revisión": "31/12/2026",
        }, self.pdf_fields(opened["Proveedor único"]))
        self.assertEqual([
            ("Segundo proveedor", "Luis Gil", "30/09/2026", "Completada",
             "Eficaz · 01/10/2026 · Sin roturas de stock"),
            ("Auditar al proveedor", "Inés Mora", "01/10/2026 · vencida", "Pendiente", "—"),
        ], ACTION_ROW.findall(opened["Proveedor único"]))
        pending = self.pdf_fields(opened["Sin valorar todavía"])
        self.assertEqual(("—", "Sin valorar", "—", "Sin valorar", "Identificado", "—"), (
            pending["Proceso"], pending["Nivel"], pending["Tratamiento"],
            pending["Nivel residual"], pending["Estado"], pending["Próxima revisión"]))
        self.assertIn("Sin acciones.", opened["Sin valorar todavía"])
        self.assertEqual("30/09/2026 · vencida",
                         self.pdf_fields(opened["Bajada de ventas"])["Próxima revisión"])
        # A closed risk is never overdue for review.
        shut = self.pdf_fields(closed_blocks["Incendio del almacén"])
        self.assertEqual(("Cerrado", "Crítico (5 × 4 = 20)", "30/09/2026"),
                         (shut["Estado"], shut["Nivel"], shut["Próxima revisión"]))

    def test_an_empty_register_says_so(self) -> None:
        self.login(ADMIN)
        header, opened, closed, html = self.register_pdf(RISKS)
        self.assertEqual("0", header["Riesgos abiertos"])
        self.assertEqual(({}, {}), (opened, closed))
        for text in ("No hay riesgos abiertos.", "No hay riesgos cerrados."):
            self.assertIn(text, html)
        self.assertEqual(25, len(CELL.findall(html)))


class OpportunityRegisterPdfTestCase(RegisterPdfBase):
    def test_the_opportunity_pdf_counts_and_lists_each_by_priority_then_title(self) -> None:
        best = self.opportunity(titulo="Nuevo mercado", beneficio=5, viabilidad=4,
                                decision="aprovechar", estado="en_curso")
        self.action(oportunidad_id=best, descripcion="Estudio de mercado")
        self.opportunity(titulo="Venta en línea", beneficio=3, viabilidad=4)  # media, 12
        self.opportunity(titulo="Feria local", beneficio=2, viabilidad=3,  # baja, 6
                         fecha_identificacion=date(2026, 1, 1),
                         fecha_revision=date(2026, 9, 30))
        self.opportunity(titulo="Catálogo digital", beneficio=2, viabilidad=4)  # media, 8
        self.opportunity(titulo="Por valorar")
        dropped = self.opportunity(titulo="Franquicia", beneficio=5, viabilidad=5)
        self.update("opportunities", dropped, {"estado": "descartada"})
        self.login(AUDITOR)

        header, opened, closed, html = self.register_pdf(OPPORTUNITIES)

        self.assertEqual({
            "Oportunidades abiertas": "5", "Alta": "1", "Media": "2", "Baja": "1",
            "Sin valorar": "1", "Revisión vencida": "1",
            "Oportunidades aprovechadas o descartadas": "1",
        }, header)
        self.assertEqual([], CELL.findall(html))  # no heat map
        self.assertEqual(["Nuevo mercado", "Catálogo digital", "Venta en línea",
                          "Feria local", "Por valorar"], list(opened))
        self.assertEqual(["Franquicia"], list(closed))
        self.assertEqual({
            "Proceso": "—", "Responsable": "Eva Ruiz", "Origen": "Interno",
            "Prioridad": "Alta (5 × 4 = 20)", "Decisión": "Aprovechar", "Estado": "En curso",
            "Próxima revisión": "—",
        }, self.pdf_fields(opened["Nuevo mercado"]))
        self.assertEqual([("Estudio de mercado", "Luis Gil", "31/12/2026", "Pendiente", "—")],
                         ACTION_ROW.findall(opened["Nuevo mercado"]))
        self.assertEqual("Sin valorar", self.pdf_fields(opened["Por valorar"])["Prioridad"])
        self.assertEqual("Descartada", self.pdf_fields(closed["Franquicia"])["Estado"])


class RegisterPdfAccessTestCase(RegisterPdfBase):
    def test_every_role_reads_both_pdfs_from_their_lists(self) -> None:
        self.risk()
        self.opportunity()
        for role in RoleEnum:
            for base, name in ((RISKS, "riesgos"), (OPPORTUNITIES, "oportunidades")):
                with self.subTest(role=role.name, register=name):
                    self.login(role)
                    self.assertIn(f'href="{base}/pdf"', self.html(f"{base}/"))
                    with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                        response = self.client.get(f"{base}/pdf")
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("application/pdf", response.mimetype)
                    self.assertIn(f"{name}_2026-10-17.pdf",
                                  response.headers["Content-Disposition"])

    def test_a_pdf_that_cannot_be_rendered_returns_to_its_list(self) -> None:
        self.login(AUDITOR)
        for base, name, message in (
                (RISKS, "riesgos", "Error al generar el PDF de los riesgos."),
                (OPPORTUNITIES, "oportunidades", "Error al generar el PDF de las oportunidades.")):
            with self.subTest(register=name):
                with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                        self.assertLogs(self.app.logger, "ERROR") as logged:
                    response = self.client.get(f"{base}/pdf")
                self.assertEqual(302, response.status_code)
                self.assertEqual(f"{base}/", response.headers["Location"])
                self.assertIn(("danger", message), self.flashes())
                self.assertIn(f"register {name}", logged.output[0])
