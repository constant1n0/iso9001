"""The approved-providers PDF (EP-3 of ``external-providers``, ISO 9001 clause 8.4).

``/proveedores/pdf`` is checked on the HTML its view renders: ``render_pdf`` is
replaced so the test keeps that HTML, as in ``test_risk_reports`` (the access
characterization renders the real PDF). The header counts the providers by
state, the overdue evaluations and the average score of every evaluation of
the approved providers. The main table lists the approved and conditioned
providers, the list an auditor asks for, by name: what each supplies, its
processes, its state with the date and reason, its latest evaluation with the
score and result, its average, its next evaluation and its open
nonconformities. The pending and blocked providers follow in their own table.
Every role reads the PDF from the list.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO
from test_provider_screens import BAD, BASE, GOOD, ProviderScreensBase

from app.models import RoleEnum

ROUTES = "app.routes.provider_routes"
ROW = re.compile(r'<tr id="proveedor-(\d+)">(.*?)</tr>', re.S)
CELL = re.compile(r"<td>([^<]*)</td>")
MAIN = '<h2 class="grupo" id="aprobados">'
OTHERS = '<h2 class="grupo" id="otros">'
BEST = {"calidad": 5, "plazo": 5, "servicio": 5, "documentacion": 5}  # 5.00, apto
FAILED = "Error al generar el PDF de los proveedores."


class ProviderPdfBase(ProviderScreensBase):
    def report(self) -> tuple[dict[str, str], dict[int, list[str]], dict[int, list[str]], str]:
        """The PDF's header fields, its main and second tables' rows by id, and its HTML."""
        html = self.pdf_html(f"{BASE}/pdf", ROUTES)
        header, _, tables = html.partition(MAIN)
        main, _, others = tables.partition(OTHERS)

        def rows(part: str) -> dict[int, list[str]]:
            return {int(found): CELL.findall(row) for found, row in ROW.findall(part)}

        return self.pdf_fields(header), rows(main), rows(others), html


class ProviderPdfTestCase(ProviderPdfBase):
    def test_the_pdf_counts_and_lists_the_approved_and_conditioned_providers_first(
            self) -> None:
        compras, almacen = self.process("Compras"), self.process("Almacén")
        aceros = self.provider("Aceros del Norte", procesos=[compras, almacen],
                               descripcion_suministro="Chapa de acero")
        self.evaluate(aceros, BEST, fecha=date(2026, 3, 1))
        self.evaluate(aceros, BAD, fecha_proxima=date(2027, 4, 10))
        self.decide(aceros, "approve", "Homologado")
        self.nonconformity(aceros)
        bombas = self.provider("Bombas Iberia", tipo_suministro="servicio")
        self.evaluate(bombas, GOOD, fecha=date(2026, 9, 1))
        self.decide(bombas, "approve", "")
        calibraciones = self.provider("Calibraciones Sur", tipo_suministro="proceso_externalizado",
                                      fecha_proxima_evaluacion=date(2026, 9, 30))
        self.decide(calibraciones, "condition", "Plazos largos")
        transportes = self.provider("Transportes Rápidos", tipo_suministro="servicio",
                                    fecha_proxima_evaluacion=date(2026, 9, 30))
        self.evaluate(transportes, BAD)
        self.decide(transportes, "block", "Lote rechazado")
        pendiente = self.provider("Envases Pendientes",
                                  fecha_proxima_evaluacion=date(2026, 10, 1))
        self.login(OPERATIVO)

        header, main, others, html = self.report()

        # (20 + 9 + 16) / 12 = 3.75: every evaluation of the approved providers.
        self.assertEqual({
            "Proveedores registrados": "5", "Aprobados": "2", "Condicionados": "1",
            "Pendientes": "1", "Bloqueados": "1", "Evaluaciones vencidas": "2",
            "Puntuación media de los aprobados": "3,75",
        }, header)
        self.assertEqual([aceros, bombas, calibraciones], list(main))
        self.assertEqual([pendiente, transportes], list(others))
        self.assertEqual([
            "Aceros del Norte", "Producto · Chapa de acero", "Almacén, Compras",
            "Aprobado · 18/10/2026 · Homologado", "10/10/2026 · 2,25 · No apto", "3,63 (2)",
            "10/04/2027", "1",
        ], main[aceros])
        self.assertEqual(["Bombas Iberia", "Servicio", "—", "Aprobado · 18/10/2026",
                          "01/09/2026 · 4,00 · Apto", "4,00 (1)", "—", "0"], main[bombas])
        self.assertEqual([
            "Calibraciones Sur", "Proceso externalizado", "—",
            "Condicionado · 18/10/2026 · Plazos largos", "Sin evaluar", "—",
            "30/09/2026 · vencida", "0",
        ], main[calibraciones])
        self.assertEqual(["Envases Pendientes", "Producto", "—", "Pendiente", "Sin evaluar",
                          "—", "01/10/2026 · vencida", "0"], others[pendiente])
        # A blocked provider is never overdue for evaluation.
        self.assertEqual("Bloqueado · 18/10/2026 · Lote rechazado", others[transportes][3])
        self.assertEqual("30/09/2026", others[transportes][6])
        self.assertIn("Proveedores aprobados a 18/10/2026", html)

    def test_an_empty_register_says_so(self) -> None:
        self.login(ADMIN)
        header, main, others, html = self.report()
        self.assertEqual(("0", "0", "—"), (header["Proveedores registrados"],
                                           header["Evaluaciones vencidas"],
                                           header["Puntuación media de los aprobados"]))
        self.assertEqual(({}, {}), (main, others))
        for text in ("No hay proveedores aprobados ni condicionados.",
                     "No hay proveedores pendientes ni bloqueados."):
            self.assertIn(text, html)


class ProviderPdfAccessTestCase(ProviderPdfBase):
    def test_every_role_reads_the_pdf_from_the_list(self) -> None:
        self.provider()
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                self.assertIn(f'href="{BASE}/pdf"', self.html(f"{BASE}/"))
                with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                    response = self.client.get(f"{BASE}/pdf")
                self.assertEqual(200, response.status_code)
                self.assertEqual("application/pdf", response.mimetype)
                self.assertIn("proveedores_2026-10-18.pdf",
                              response.headers["Content-Disposition"])

    def test_a_pdf_that_cannot_be_rendered_returns_to_the_list(self) -> None:
        self.login(AUDITOR)
        with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                self.assertLogs(self.app.logger, "ERROR") as logged:
            response = self.client.get(f"{BASE}/pdf")
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/", response.headers["Location"])
        self.assertIn(("danger", FAILED), self.flashes())
        self.assertIn("providers PDF", logged.output[0])
