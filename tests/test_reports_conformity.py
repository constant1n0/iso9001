"""Monthly conformity section follows the report's existing metric sections."""
from datetime import date
from unittest.mock import patch

from app.utils import reports
from test_conformity_analysis import ConformityFixtures
from test_nonconformity_service import ServiceBase


class MonthlyConformityTestCase(ConformityFixtures, ServiceBase):
    def test_month_section_uses_decision_dates_and_preserves_existing_sections(self) -> None:
        release = self.decision(date(2026, 6, 1))
        self.check(release)
        self.decision(date(2026, 7, 1))
        html = reports.render_monthly_report_html(date(2026, 6, 30),
                                                  today=date(2026, 7, 1))
        self.assertIn('Conformidad de productos y servicios', html)
        for row in ('<tr><th>Liberadas sin concesión</th><td>1</td></tr>',
                    '<tr><th>Verificaciones conformes (%)</th><td>100</td></tr>'):
            self.assertIn(row, html)
        for title in ('Por proceso de la orden', 'Por cliente de la orden',
                      'Sin cliente', 'Acciones correctivas verificadas',
                      'Objetivos de calidad'):
            self.assertIn(title, html)
        with patch.object(reports, 'render_pdf', return_value=b'%PDF-test') as pdf:
            self.assertEqual(b'%PDF-test', reports.generar_reporte_pdf(
                date(2026, 6, 30), today=date(2026, 7, 1)))
        self.assertEqual(1, pdf.call_args.kwargs['conformidad']['totales']['liberadas'])
        empty = reports.render_monthly_report_html(date(2025, 6, 30))
        self.assertIn('<tr><th>Verificaciones conformes (%)</th><td>—</td></tr>', empty)
        self.assertIn('<tr><th>Liberadas sin concesión</th><td>0</td></tr>', empty)
