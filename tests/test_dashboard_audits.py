"""Dashboard audit figures follow the audit read grant (task DF-1, decision DD2).

Only roles that may read audits (administrators and auditors) see the audit
count, the upcoming-audits figure and card; an operativo sees none of them and
no empty card in their place. "Upcoming" lists only the audits still to be
held: dated from today to seven days ahead, neither completed nor cancelled,
in date order.
"""

from __future__ import annotations

import re
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app import session_lifetime
from app.extensions import db
from app.models import Auditoria, EstadoAuditoriaEnum, RoleEnum, User
from app.services import audits
from app.services.actor import Actor
from app.services.errors import PermissionDenied

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
TODAY = date(2026, 10, 5)
TODAY_IN_DASHBOARD = "app.routes.dashboard_routes.local_today"
CARD = re.compile(r'<article class="card" id="auditorias-proximas">(.*?)</article>', re.S)
AREA = re.compile(r'<span class="list__primary">([^<]*)</span>')
AUDIT_KPI = re.compile(
    r'kpi__label">Auditorías</span>\s*<span class="kpi__value">(\d+)</span>\s*'
    r'<span class="kpi__foot">(\d+) en los próximos 7 días</span>')

# (area, days from today, state); only the first three are still to be held this week.
AUDITS = (
    ("Laboratorio", 7, EstadoAuditoriaEnum.PENDIENTE),
    ("Ventas", 3, EstadoAuditoriaEnum.PENDIENTE),
    ("Compras", 0, EstadoAuditoriaEnum.EN_PROCESO),
    ("Producción", 1, EstadoAuditoriaEnum.COMPLETADA),
    ("Logística", 2, EstadoAuditoriaEnum.CANCELADA),
    ("Ayer", -1, EstadoAuditoriaEnum.PENDIENTE),
    ("Lejana", 8, EstadoAuditoriaEnum.PENDIENTE),
)
STILL_TO_HOLD = ["Compras", "Ventas", "Laboratorio"]


class DashboardAuditsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app_with_schema(self)
        with self.app.app_context():
            for role in RoleEnum:
                name = role.name.lower()
                db.session.add(User(username=name, email=f"{name}@example.com",
                                    password=generate_password_hash("StrongPassword123!"),
                                    role=role))
            for area, days, estado in AUDITS:
                db.session.add(Auditoria(area_auditada=area, fecha=TODAY + timedelta(days=days),
                                         auditor="Eva", resultado="R", estado=estado))
            db.session.commit()
            self.ids = {user.role: user.id for user in User.query.all()}

    def html(self, role: RoleEnum) -> str:
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session_lifetime.start(session)
            session["_fresh"] = True
        with patch(TODAY_IN_DASHBOARD, return_value=TODAY):
            response = client.get("/dashboard/")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_an_operativo_sees_no_audit_figure_area_date_or_link(self) -> None:
        html = self.html(OPERATIVO)
        for text in ('kpi__label">Auditorías<', "en los próximos 7 días",
                     "Auditorías próximas", "Sin auditorías programadas",
                     'id="auditorias-proximas"', "/auditorias/",
                     *(area for area, _, _ in AUDITS)):
            with self.subTest(text=text):
                self.assertNotIn(text, html)
        for days in range(-1, 9):
            with self.subTest(days=days):
                self.assertNotIn((TODAY + timedelta(days=days)).strftime("%d/%m<"), html)
        # The rest of the dashboard still renders.
        self.assertIn('kpi__label">No conformidades abiertas<', html)
        self.assertIn("Capacitaciones próximas", html)

    def test_administrators_and_auditors_see_the_count_and_the_upcoming_card(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                html = self.html(role)
                # The count is every audit recorded; the foot, those still to hold this week.
                self.assertEqual((str(len(AUDITS)), str(len(STILL_TO_HOLD))),
                                 AUDIT_KPI.search(html).groups())
                card = CARD.search(html).group(1)
                self.assertIn("Auditorías próximas", card)
                self.assertIn(TODAY.strftime("%d/%m"), card)
                self.assertIn((TODAY + timedelta(days=7)).strftime("%d/%m"), card)

    def test_upcoming_lists_only_audits_still_to_be_held_in_date_order(self) -> None:
        card = CARD.search(self.html(AUDITOR)).group(1)
        self.assertEqual(STILL_TO_HOLD, AREA.findall(card))

    def test_the_card_says_so_when_no_audit_is_still_to_be_held(self) -> None:
        with self.app.app_context():
            Auditoria.query.filter(Auditoria.area_auditada.in_(STILL_TO_HOLD)).delete()
            db.session.commit()
        html = self.html(ADMIN)
        card = CARD.search(html).group(1)
        self.assertIn("Sin auditorías programadas.", card)
        self.assertEqual([], AREA.findall(card))
        # The count is shown on its own value, not on the upcoming list's.
        self.assertEqual((str(len(AUDITS) - len(STILL_TO_HOLD)), "0"),
                         AUDIT_KPI.search(html).groups())


class UpcomingAuditsServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app_with_schema(self)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        for area, days, estado in AUDITS:
            db.session.add(Auditoria(area_auditada=area, fecha=TODAY + timedelta(days=days),
                                     auditor="Eva", resultado="R", estado=estado))
        db.session.commit()

    @staticmethod
    def actor(role: RoleEnum) -> Actor:
        return Actor(user_id=None, label=role.name, role=role, channel="web")

    def test_upcoming_follows_the_audit_read_grant(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                found = audits.upcoming(db.session, self.actor(role), today=TODAY,
                                        within_days=7)
                self.assertEqual(STILL_TO_HOLD, [audit.area_auditada for audit in found])
        with self.assertRaises(PermissionDenied):
            audits.upcoming(db.session, self.actor(OPERATIVO), today=TODAY, within_days=7)

    def test_the_window_is_inclusive_and_follows_within_days(self) -> None:
        found = audits.upcoming(db.session, self.actor(ADMIN), today=TODAY, within_days=3)
        self.assertEqual(["Compras", "Ventas"], [audit.area_auditada for audit in found])


if __name__ == "__main__":
    unittest.main()
