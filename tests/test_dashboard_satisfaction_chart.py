"""The dashboard satisfaction chart groups by year and month."""

from __future__ import annotations

import json
import re
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import RoleEnum, SatisfaccionCliente, User

PASSWORD = "StrongPassword123!"
DATA_SCRIPT = re.compile(
    r'<script type="application/json" id="dashboard-data">(.*?)</script>', re.S
)
TODAY = date(2026, 10, 5)


class SatisfactionChartTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add(User(username="admin", email="admin@example.com",
                                password=generate_password_hash(PASSWORD),
                                role=RoleEnum.ADMINISTRADOR))
            db.session.commit()
        self.client.post("/login", data={"username": "admin", "password": PASSWORD})

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _chart(self, surveys, today: date = TODAY) -> dict:
        with self.app.app_context():
            for day, score in surveys:
                db.session.add(SatisfaccionCliente(
                    fecha_encuesta=day, cliente="C", puntuacion=score))
            db.session.commit()
        with patch("app.routes.dashboard_routes.local_today", return_value=today):
            html = self.client.get("/dashboard/").get_data(as_text=True)
        return json.loads(DATA_SCRIPT.search(html).group(1))["satisfaccion"]

    def test_the_same_month_of_another_year_is_not_averaged_in(self) -> None:
        chart = self._chart([(date(2025, 1, 5), 2), (date(2026, 1, 5), 8)],
                            today=date(2026, 1, 15))
        self.assertEqual(["2026-01"], chart["meses"])
        self.assertEqual([8.0], chart["promedios"])

    def test_only_the_last_twelve_months_with_the_year_in_the_label(self) -> None:
        chart = self._chart([(date(2025, 10, 5), 1), (date(2025, 11, 5), 4),
                             (date(2025, 11, 20), 6), (date(2026, 10, 2), 8)])
        self.assertEqual(["2025-11", "2026-10"], chart["meses"])
        self.assertEqual([5.0, 8.0], chart["promedios"])


if __name__ == "__main__":
    unittest.main()
