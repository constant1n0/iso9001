"""The monthly quality report is dated the day it is generated, not its month's end."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db, mail
from app.models import RoleEnum, User
from app.utils import reports

SEPT = date(2026, 9, 30)


class MonthlyReportGenerationDateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app(MAIL_DEFAULT_SENDER="qms@example.invalid")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pdf_html(self, build) -> str:
        """Run ``build`` with WeasyPrint stubbed out; return the HTML it was given."""
        with patch("app.utils.pdf.HTML") as html:
            html.return_value.write_pdf.return_value = b"%PDF-x"
            build()
        return html.call_args.kwargs["string"]

    def test_html_of_a_past_month_is_dated_the_day_it_is_generated(self) -> None:
        html = reports.render_monthly_report_html(SEPT, today=date(2026, 10, 12))

        self.assertIn("Generado el 12/10/2026", html)
        self.assertNotIn("30/09/2026", html.split("</header>")[0])
        self.assertIn("Periodo: septiembre de 2026", html)

    def test_pdf_of_a_past_month_is_dated_the_day_it_is_generated(self) -> None:
        html = self._pdf_html(
            lambda: reports.generar_reporte_pdf(SEPT, today=date(2026, 10, 12)))

        self.assertIn("Generado el 12/10/2026", html)
        self.assertIn("Periodo: septiembre de 2026", html)

    def test_scheduled_e_mail_reports_the_previous_month_dated_the_day_it_runs(self) -> None:
        from app import audit_notifications as notifications

        db.session.add(User(username="admin1", email="a@example.com", role=RoleEnum.ADMINISTRADOR,
                            password=generate_password_hash("StrongPassword123!")))
        db.session.commit()

        with mail.record_messages() as outbox:
            html = self._pdf_html(
                lambda: notifications.send_monthly_quality_report(today=date(2026, 10, 1)))

        self.assertIn("Generado el 01/10/2026", html)
        self.assertIn("Periodo: septiembre de 2026", html)
        self.assertEqual("reporte-calidad-2026-09.pdf", outbox[0].attachments[0].filename)


if __name__ == "__main__":
    unittest.main()
