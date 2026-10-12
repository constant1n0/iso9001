"""Quality objective reports (task QO-3 of ``quality-objectives``).

The yearly objectives PDF (``/objetivos-calidad/pdf?anio=``) is checked on the
HTML its view renders: ``render_pdf`` is replaced so the test keeps that HTML,
as in ``test_audit_screens`` (the access characterization renders the real
PDF). The monthly report's objective figures are checked on its context and
its HTML; they are judged on the month's last day, or on today when earlier.
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from test_audit_screens import ADMIN, AUDITOR
from test_quality_objective_screens import BASE, ROUTES, ObjectiveScreensBase

from app.extensions import db
from app.models import (
    EstadoObjetivo, FrecuenciaMedicion, MedicionObjetivo, ObjetivoCalidad, Person, RoleEnum,
    SentidoObjetivo,
)
from app.utils import reports

PDF = f"{BASE}/pdf"
SEPT = date(2026, 9, 30)
BLOCK = re.compile(r'<section class="objetivo">(.*?)</section>', re.S)
TITLE = re.compile(r"<h2>([^<]+)</h2>")
MEASUREMENT_ROW = re.compile(r"<tr><td>\d\d/.*?</tr>")


class ObjectivesPdfTestCase(ObjectiveScreensBase):
    def objectives_pdf(self, url: str = PDF) -> tuple[dict[str, str], dict[str, str]]:
        """The PDF's header fields and its objective blocks by title, in order."""
        html = self.pdf_html(url, ROUTES)
        header, _, _ = html.partition('<section class="objetivo">')
        blocks = {TITLE.search(block).group(1): block for block in BLOCK.findall(html)}
        return self.pdf_fields(header), blocks

    def test_the_pdf_holds_the_year_figures_and_each_objective_by_deadline(self) -> None:
        process_id = self.process("Logística")
        late = self.objective(titulo="Reclamaciones")  # ≤ 2, monthly, owned by Eva
        self.measure(late, date(2026, 8, 31), 3, comentario="Huelga de transporte")
        self.measure(late, date(2026, 9, 30), "1.5")
        soon = self.objective(
            titulo="Entregas a tiempo", indicador="% de pedidos a tiempo", meta=95,
            sentido="mayor_igual", unidad="%", valor_inicial="88.5", proceso_id=process_id,
            fecha_limite=date(2026, 6, 30), frecuencia="trimestral",
            recursos="Un turno de reparto adicional",
            evaluacion="Revisión trimestral en el comité")
        self.close(self.objective(titulo="Auditorías a tiempo"))  # same deadline, earlier title
        self.objective(titulo="Fuera del año", fecha_inicio=date(2025, 1, 1),
                       fecha_limite=date(2025, 12, 31))
        self.login(AUDITOR)

        header, blocks = self.objectives_pdf(f"{PDF}?anio=2026")

        self.assertEqual({
            "Año": "2026", "Objetivos": "3", "Activo": "2", "Alcanzado": "1",
            "No alcanzado": "0", "Cancelado": "0", "Activos que cumplen la meta": "1",
            "Activos con medición vencida": "1",
        }, header)
        self.assertEqual(["Entregas a tiempo", "Auditorías a tiempo", "Reclamaciones"],
                         list(blocks))
        self.assertEqual({
            "Proceso": "Logística", "Indicador": "% de pedidos a tiempo", "Meta": "≥ 95 %",
            "Valor inicial": "88,5 %", "Responsable": "Eva Ruiz", "Fecha límite": "30/06/2026",
            "Frecuencia de medición": "Trimestral", "Estado": "Activo", "Último valor": "—",
            "Meta cumplida": "Sin medir", "Tendencia": "—",
            "Próxima medición": "01/04/2026 · vencida",
        }, self.pdf_fields(blocks["Entregas a tiempo"]))
        for text in ("Un turno de reparto adicional", "Revisión trimestral en el comité",
                     "Sin mediciones."):
            with self.subTest(text=text):
                self.assertIn(text, blocks["Entregas a tiempo"])
        fields = self.pdf_fields(blocks["Reclamaciones"])
        self.assertEqual(("—", "≤ 2", "—", "1,5 · 30/09/2026", "Cumple", "Baja", "30/10/2026"), (
            fields["Proceso"], fields["Meta"], fields["Valor inicial"], fields["Último valor"],
            fields["Meta cumplida"], fields["Tendencia"], fields["Próxima medición"]))
        self.assertEqual([
            '<tr><td>31/08/2026</td><td class="num">3</td><td>No cumple</td>'
            "<td>Huelga de transporte</td></tr>",
            '<tr><td>30/09/2026</td><td class="num">1,5</td><td>Cumple</td><td>—</td></tr>',
        ], MEASUREMENT_ROW.findall(blocks["Reclamaciones"]))
        self.assertEqual(2, blocks["Reclamaciones"].count("Sin indicar."))  # resources, evaluation
        closed = self.pdf_fields(blocks["Auditorías a tiempo"])
        self.assertEqual(("Alcanzado", "—"), (closed["Estado"], closed["Próxima medición"]))

    def test_the_year_defaults_to_today_s_and_an_invalid_one_is_not_found(self) -> None:
        self.objective(titulo="Del año")
        self.objective(titulo="Del año pasado", fecha_inicio=date(2025, 1, 1),
                       fecha_limite=date(2025, 12, 31))
        self.login(ADMIN)
        with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
            for query, filename in (("", "2026"), ("?anio=", "2026"), ("?anio=2025", "2025")):
                with self.subTest(query=query):
                    response = self.client.get(f"{PDF}{query}")
                    self.assertEqual(200, response.status_code)
                    self.assertIn(f"objetivos_calidad_{filename}.pdf",
                                  response.headers["Content-Disposition"])
            for year in ("abc", "1999", "2101", "20260", "-2026", "２０２６", " 2026"):
                with self.subTest(year=year):
                    self.assertEqual(404, self.client.get(PDF, query_string={
                        "anio": year}).status_code)
        _, blocks = self.objectives_pdf()
        self.assertEqual(["Del año"], list(blocks))
        _, blocks = self.objectives_pdf(f"{PDF}?anio=2025")
        self.assertEqual(["Del año pasado"], list(blocks))
        html = self.pdf_html(f"{PDF}?anio=2027", ROUTES)
        self.assertIn("No hay objetivos de calidad del año 2027.", html)
        self.assertEqual("0", self.pdf_fields(html)["Objetivos"])

    def test_every_role_reads_the_pdf_and_the_list_links_its_year(self) -> None:
        self.objective()
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                self.assertIn(f'href="{PDF}?anio=2026"', self.html(f"{BASE}/"))
                self.assertIn(f'href="{PDF}?anio=2025"', self.html(f"{BASE}/?anio=2025"))
                self.assertIn(f'href="{PDF}?anio=2026"', self.html(f"{BASE}/?anio=1999"))
                with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                    response = self.client.get(PDF)
                self.assertEqual(200, response.status_code)
                self.assertEqual("application/pdf", response.mimetype)

    def test_a_pdf_that_cannot_be_rendered_returns_to_the_list(self) -> None:
        self.objective()
        self.login(AUDITOR)
        with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                self.assertLogs(self.app.logger, "ERROR") as logged:
            response = self.client.get(f"{PDF}?anio=2026")
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/", response.headers["Location"])
        self.assertIn(("danger", "Error al generar el PDF de los objetivos de calidad."),
                      self.flashes())
        self.assertIn("quality objectives 2026", logged.output[0])


class MonthlyReportObjectivesTestCase(unittest.TestCase):
    """The monthly report's "Objetivos de calidad" section."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(MAIL_DEFAULT_SENDER="qms@example.invalid")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        owner = Person(nombre="Eva Ruiz")
        db.session.add(owner)
        db.session.flush()
        self.owner = owner.id

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def objective(self, measured: dict[date, int], *, start: date = date(2026, 1, 1),
                  estado: EstadoObjetivo = EstadoObjetivo.activo,
                  sentido: SentidoObjetivo = SentidoObjetivo.mayor_igual) -> None:
        """A monthly objective with target 95 and the ``measured`` values by date."""
        found = ObjetivoCalidad(
            titulo="O", indicador="I", meta=95, sentido=sentido, responsable_id=self.owner,
            anio=start.year, fecha_inicio=start, fecha_limite=date(2026, 12, 31),
            frecuencia=FrecuenciaMedicion.mensual, estado=estado)
        db.session.add(found)
        db.session.flush()
        db.session.add_all(MedicionObjetivo(objetivo_id=found.id, fecha=day, valor=Decimal(value))
                           for day, value in measured.items())

    @staticmethod
    def figures(ctx: dict) -> tuple[int, int, int, int]:
        return (ctx["mediciones_objetivos"], ctx["objetivos_activos"], ctx["objetivos_en_meta"],
                ctx["objetivos_medicion_vencida"])

    def test_figures_are_judged_on_the_month_s_last_day(self) -> None:
        # Measurements after the month are left out, so each objective is judged
        # as it stood on 30/09: they would turn the first to "not met" and the
        # second to "met, not overdue".
        self.objective({date(2026, 9, 15): 96, date(2026, 10, 5): 80})  # met, next on 15/10
        self.objective({date(2026, 8, 10): 90, date(2026, 10, 1): 99})  # missed, due 10/09
        self.objective({})  # not measured, due 01/02: overdue
        self.objective({}, start=date(2026, 10, 1))  # starts after the month: left out
        self.objective({date(2026, 9, 20): 99, date(2026, 8, 31): 99},
                       estado=EstadoObjetivo.alcanzado)
        self.objective({date(2026, 9, 1): 99}, estado=EstadoObjetivo.cancelado)
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT, today=date(2026, 10, 12))

        self.assertEqual((3, 3, 1, 2), self.figures(ctx))
        self.assertEqual(SEPT, ctx["objetivos_fecha_corte"])
        html = reports.render_monthly_report_html(SEPT, today=date(2026, 10, 12))
        for row in ("<tr><th>Mediciones registradas</th><td>3</td></tr>",
                    "<tr><th>Objetivos activos</th><td>3</td></tr>",
                    "<tr><th>Objetivos activos que cumplen la meta</th><td>1</td></tr>",
                    "<tr><th>Objetivos activos con medición vencida</th><td>2</td></tr>"):
            self.assertIn(row, html)
        self.assertIn("Situación de los objetivos activos a 30/09/2026.", html)
        self.assertIn("hallazgos por la fecha de su auditoría", html)  # the scope still says so

    def test_the_latest_measurement_is_the_latest_by_date_whatever_the_stored_order(self) -> None:
        # Stored newest first: judged on the 99 of 20/09, not on the 80 of 20/08.
        self.objective({date(2026, 9, 20): 99, date(2026, 8, 20): 80})
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT, today=date(2026, 10, 12))

        self.assertEqual((1, 1, 1, 0), self.figures(ctx))

    def test_a_month_not_yet_over_is_judged_on_today(self) -> None:
        self.objective({date(2026, 9, 15): 96})  # next on 15/10
        self.objective({date(2026, 8, 25): 96})  # next on 25/09: overdue on 30/09 only
        db.session.commit()

        self.assertEqual((1, 2, 2, 0), self.figures(
            reports._monthly_report_context(SEPT, today=date(2026, 9, 20))))
        with patch("app.audit_notifications.local_today", return_value=date(2026, 9, 20)):
            ctx = reports._monthly_report_context(SEPT)
        self.assertEqual((1, 2, 2, 0), self.figures(ctx))
        self.assertEqual(date(2026, 9, 20), ctx["objetivos_fecha_corte"])
        with patch("app.audit_notifications.local_today", return_value=date(2026, 10, 1)):
            self.assertEqual((1, 2, 2, 1), self.figures(reports._monthly_report_context(SEPT)))

    def test_without_objectives_the_section_reports_zeros_and_older_keys_stay(self) -> None:
        ctx = reports._monthly_report_context(SEPT, today=date(2026, 10, 12))
        self.assertEqual((0, 0, 0, 0), self.figures(ctx))
        self.assertLessEqual({
            "fecha", "total_auditorias", "auditorias_completadas", "hallazgos_por_tipo",
            "total_no_conformidades", "no_conformidades_por_estado", "acciones_verificadas",
            "acciones_eficaces", "acciones_no_eficaces", "promedio_satisfaccion",
            "total_capacitaciones"}, set(ctx))
        self.assertIn("<h2>Objetivos de calidad</h2>",
                      reports.render_monthly_report_html(SEPT, today=date(2026, 10, 12)))


if __name__ == "__main__":
    unittest.main()
