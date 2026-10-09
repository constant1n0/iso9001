"""The monthly quality report covers only the calendar month of its date."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Auditoria, Capacitacion, EstadoAuditoriaEnum, EstadoNoConformidad, NoConformidad,
    RoleEnum, SatisfaccionCliente, User,
)
from app.utils import reports

SEPT = date(2026, 9, 30)


class MonthlyReportPeriodTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app(MAIL_DEFAULT_SENDER="qms@example.invalid")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self, day: date, score: int) -> None:
        db.session.add(Auditoria(
            area_auditada="A", fecha=day, auditor="X", resultado="",
            estado=EstadoAuditoriaEnum.PENDIENTE))
        db.session.add(NoConformidad(descripcion="NC", fecha_detectada=day))
        db.session.add(SatisfaccionCliente(fecha_encuesta=day, cliente="C", puntuacion=score))
        db.session.add(Capacitacion(tema="T", fecha=day, personal="P"))

    def test_only_records_of_the_requested_month_are_counted(self) -> None:
        for day, score in ((date(2026, 9, 1), 4), (date(2026, 9, 30), 8),
                           (date(2026, 8, 31), 10), (date(2026, 10, 1), 10),
                           (date(2025, 9, 15), 10)):
            self._seed(day, score)
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        self.assertEqual(2, ctx["total_auditorias"])
        self.assertEqual(2, ctx["total_no_conformidades"])
        self.assertEqual(2, ctx["total_capacitaciones"])
        self.assertEqual(6, ctx["promedio_satisfaccion"])

    def test_nonconformities_of_every_state_are_counted(self) -> None:
        for estado in EstadoNoConformidad:
            db.session.add(NoConformidad(descripcion=estado.value, estado=estado,
                                         fecha_detectada=date(2026, 9, 10)))
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        self.assertEqual(len(EstadoNoConformidad), ctx["total_no_conformidades"])
        html = reports.render_monthly_report_html(SEPT)
        self.assertIn(f'kpi__value">{len(EstadoNoConformidad)}<', html)

    def test_an_empty_month_reports_zeros(self) -> None:
        self._seed(date(2026, 8, 15), 9)
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        self.assertEqual(
            (0, 0, 0, 0),
            (ctx["total_auditorias"], ctx["total_no_conformidades"],
             ctx["total_capacitaciones"], ctx["promedio_satisfaccion"]),
        )
        self.assertIn("0.0", reports.render_monthly_report_html(SEPT))

    def test_the_e_mail_sent_on_the_first_reports_the_previous_month(self) -> None:
        from app import audit_notifications as notifications

        db.session.add(User(username="admin1", email="a@example.com", role=RoleEnum.ADMINISTRADOR,
                            password=generate_password_hash("StrongPassword123!")))
        db.session.commit()
        with patch.object(notifications.reports, "generar_reporte_pdf",
                          return_value=b"%PDF-x") as build:
            from app.extensions import mail
            with mail.record_messages() as outbox:
                notifications.send_monthly_quality_report(today=date(2026, 10, 1))

        build.assert_called_once_with(date(2026, 9, 30))
        self.assertEqual("reporte-calidad-2026-09.pdf", outbox[0].attachments[0].filename)


if __name__ == "__main__":
    unittest.main()
