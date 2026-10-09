"""The monthly quality report covers only the calendar month of its date."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    AccionCorrectiva, Auditoria, Capacitacion, EstadoAuditoriaEnum, EstadoNoConformidad,
    NoConformidad, Person, ResultadoVerificacion, RoleEnum, SatisfaccionCliente, User,
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

    def test_nonconformities_detected_in_the_month_are_counted_by_state(self) -> None:
        E = EstadoNoConformidad
        for day, estado in ((date(2026, 9, 1), E.abierta), (date(2026, 9, 30), E.abierta),
                            (date(2026, 9, 10), E.cerrada), (date(2026, 9, 11), E.cancelada),
                            (date(2026, 8, 31), E.cerrada), (date(2026, 10, 1), E.abierta)):
            db.session.add(NoConformidad(descripcion="NC", fecha_detectada=day, estado=estado))
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        by_state = ctx["no_conformidades_por_estado"]
        self.assertEqual(list(E), list(by_state))  # every state, in the enum's order
        self.assertEqual([2, 0, 0, 1, 1], list(by_state.values()))
        self.assertEqual(ctx["total_no_conformidades"], sum(by_state.values()))
        html = reports.render_monthly_report_html(SEPT)
        for row in ("<tr><th>Abierta</th><td>2</td></tr>",
                    "<tr><th>Acción planificada</th><td>0</td></tr>",
                    "<tr><th>Cerrada</th><td>1</td></tr>"):
            self.assertIn(row, html)

    def test_actions_verified_in_the_month_are_counted_by_result(self) -> None:
        owner, verifier = Person(nombre="Ana"), Person(nombre="Eva")
        nc = NoConformidad(descripcion="NC", fecha_detectada=date(2026, 8, 1),
                           estado=EstadoNoConformidad.en_verificacion)
        db.session.add_all([owner, verifier, nc])
        db.session.flush()
        effective, not_effective = ResultadoVerificacion.eficaz, ResultadoVerificacion.no_eficaz
        for verified, result in ((date(2026, 9, 1), effective), (date(2026, 9, 30), effective),
                                 (date(2026, 9, 15), not_effective),
                                 (date(2026, 8, 31), effective),
                                 (date(2026, 10, 1), not_effective), (None, None)):
            db.session.add(AccionCorrectiva(
                no_conformidad_id=nc.id, descripcion="Acción", responsable_id=owner.id,
                fecha_prevista=date(2026, 8, 20), fecha_realizada=date(2026, 8, 25),
                resultado_verificacion=result, fecha_verificacion=verified,
                verificador_id=verifier.id if result else None,
                evidencia_verificacion="Evidencia" if result else None))
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        self.assertEqual((3, 2, 1), (ctx["acciones_verificadas"], ctx["acciones_eficaces"],
                                     ctx["acciones_no_eficaces"]))
        html = reports.render_monthly_report_html(SEPT)
        for row in ("<tr><th>Acciones verificadas</th><td>3</td></tr>",
                    "<tr><th>Eficaces</th><td>2</td></tr>",
                    "<tr><th>No eficaces</th><td>1</td></tr>"):
            self.assertIn(row, html)

    def test_an_empty_month_reports_zeros(self) -> None:
        self._seed(date(2026, 8, 15), 9)
        db.session.commit()

        ctx = reports._monthly_report_context(SEPT)

        self.assertEqual(
            (0, 0, 0, 0, 0, 0, 0),
            (ctx["total_auditorias"], ctx["total_no_conformidades"],
             ctx["total_capacitaciones"], ctx["promedio_satisfaccion"],
             ctx["acciones_verificadas"], ctx["acciones_eficaces"],
             ctx["acciones_no_eficaces"]),
        )
        self.assertEqual({0}, set(ctx["no_conformidades_por_estado"].values()))
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
