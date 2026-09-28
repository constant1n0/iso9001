"""Tests for scheduled audit notifications and their Celery wiring."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db, mail
from app.models import Auditoria, EstadoAuditoriaEnum, RoleEnum, User


TODAY = date(2026, 10, 5)
SENDER = "qms@example.invalid"


class ScheduledNotificationsTestCase(unittest.TestCase):
    """Exercise notification queries and delivery through real SQLite."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(MAIL_DEFAULT_SENDER=SENDER)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _user(self, username: str, role: RoleEnum, email: str | None) -> None:
        db.session.add(
            User(
                username=username,
                email=email,
                password=generate_password_hash("StrongPassword123!"),
                role=role,
            )
        )

    def _audit(self, area: str, day: date, estado: EstadoAuditoriaEnum) -> None:
        db.session.add(
            Auditoria(
                area_auditada=area,
                fecha=day,
                auditor="Auditor",
                resultado="",
                estado=estado,
            )
        )

    def test_pending_report_goes_only_to_admins_with_email(self) -> None:
        from app import audit_notifications as notifications

        self._user("admin1", RoleEnum.ADMINISTRADOR, "admin1@example.com")
        self._user("admin2", RoleEnum.ADMINISTRADOR, None)
        self._user("auditor", RoleEnum.AUDITOR, "auditor@example.com")
        self._audit("Compras", TODAY, EstadoAuditoriaEnum.PENDIENTE)
        self._audit("Ventas", TODAY, EstadoAuditoriaEnum.COMPLETADA)
        db.session.commit()

        with mail.record_messages() as outbox:
            sent = notifications.send_pending_audits_report()

        self.assertEqual(1, sent)
        self.assertEqual([["admin1@example.com"]], [m.recipients for m in outbox])
        self.assertEqual(SENDER, outbox[0].sender)
        self.assertIn("Compras", outbox[0].body)
        self.assertNotIn("Ventas", outbox[0].body)

    def test_pending_report_sends_nothing_without_pending_audits(self) -> None:
        from app import audit_notifications as notifications

        self._user("admin1", RoleEnum.ADMINISTRADOR, "admin1@example.com")
        self._audit("Ventas", TODAY, EstadoAuditoriaEnum.CANCELADA)
        db.session.commit()

        with mail.record_messages() as outbox:
            sent = notifications.send_pending_audits_report()

        self.assertEqual(0, sent)
        self.assertEqual([], outbox)

    def test_upcoming_alert_covers_next_seven_days_of_active_audits(self) -> None:
        from app import audit_notifications as notifications

        self._user("auditor", RoleEnum.AUDITOR, "auditor@example.com")
        self._user("admin1", RoleEnum.ADMINISTRADOR, "admin1@example.com")
        self._audit("Hoy", TODAY, EstadoAuditoriaEnum.PENDIENTE)
        self._audit("Limite", TODAY + timedelta(days=7), EstadoAuditoriaEnum.EN_PROCESO)
        self._audit("Lejana", TODAY + timedelta(days=8), EstadoAuditoriaEnum.PENDIENTE)
        self._audit("Pasada", TODAY - timedelta(days=1), EstadoAuditoriaEnum.PENDIENTE)
        self._audit("Cancelada", TODAY + timedelta(days=1), EstadoAuditoriaEnum.CANCELADA)
        self._audit("Completada", TODAY + timedelta(days=1), EstadoAuditoriaEnum.COMPLETADA)
        db.session.commit()

        with mail.record_messages() as outbox:
            sent = notifications.send_upcoming_audits_alert(today=TODAY)

        self.assertEqual(1, sent)
        self.assertEqual([["auditor@example.com"]], [m.recipients for m in outbox])
        body = outbox[0].body
        for area in ("Hoy", "Limite"):
            self.assertIn(area, body)
        for area in ("Lejana", "Pasada", "Cancelada", "Completada"):
            self.assertNotIn(area, body)

    def test_upcoming_alert_defaults_to_the_configured_local_date(self) -> None:
        from app import audit_notifications as notifications

        self.assertEqual("Europe/Madrid", self.app.config["APP_TIMEZONE"])
        with patch.object(notifications, "local_today", return_value=TODAY) as today:
            with mail.record_messages():
                notifications.send_upcoming_audits_alert()
        today.assert_called_once_with()

    def test_delivery_failure_is_logged_other_recipients_still_get_mail(self) -> None:
        from app import audit_notifications as notifications

        self._user("admin1", RoleEnum.ADMINISTRADOR, "admin1@example.com")
        self._user("admin2", RoleEnum.ADMINISTRADOR, "admin2@example.com")
        self._audit("Compras", TODAY, EstadoAuditoriaEnum.PENDIENTE)
        db.session.commit()

        delivered = []

        def flaky_send(message):
            if message.recipients == ["admin1@example.com"]:
                raise OSError("smtp down")
            delivered.append(message.recipients)

        with (
            patch.object(notifications.mail, "send", side_effect=flaky_send),
            self.assertLogs(notifications.logger, "ERROR") as logs,
            self.assertRaises(notifications.NotificationDeliveryError),
        ):
            notifications.send_pending_audits_report()

        self.assertEqual([["admin2@example.com"]], delivered)
        self.assertIn("admin1@example.com", "\n".join(logs.output))


WIRING_PROBE = textwrap.dedent(
    """
    import json
    import celery_worker

    app = celery_worker.celery
    print(json.dumps({
        "registered": sorted(t for t in app.tasks if not t.startswith("celery.")),
        "schedule": {
            key: {
                "task": entry["task"],
                "crontab": str(entry["schedule"]),
            }
            for key, entry in app.conf.beat_schedule.items()
        },
        "timezone": app.conf.timezone,
        "broker_url": app.conf.broker_url,
        "result_backend": app.conf.result_backend,
        "conf_dump": repr(sorted(app.conf.items(), key=lambda item: item[0])),
    }))
    """
)

SECRET_SENTINELS = {
    "SECRET_KEY": "sentinel-secret-key",
    "MAIL_PASSWORD": "sentinel-mail-password",
    "DATABASE_URI": "sqlite:///sentinel-db-uri.invalid",
}


def _run_worker_probe(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(bootstrap.PROJECT_ROOT),
        "FLASK_DEBUG": "False",
        "SECURITY_LOG_ENABLED": "False",
        "CELERY_BROKER_URL": "memory://",
        **SECRET_SENTINELS,
        **overrides,
    }
    env = {key: value for key, value in env.items() if value is not None}
    if "DYLD_FALLBACK_LIBRARY_PATH" in bootstrap.ISOLATED_IMPORT_ENV:
        env["DYLD_FALLBACK_LIBRARY_PATH"] = bootstrap.ISOLATED_IMPORT_ENV[
            "DYLD_FALLBACK_LIBRARY_PATH"
        ]
    return subprocess.run(
        [sys.executable, "-c", WIRING_PROBE],
        cwd=bootstrap.PROJECT_ROOT / "tests",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class CeleryWiringTestCase(unittest.TestCase):
    """Import the real worker module in a clean process, as the CLI does."""

    @classmethod
    def setUpClass(cls) -> None:
        result = _run_worker_probe()
        if result.returncode != 0:
            raise AssertionError(f"worker import failed:\n{result.stderr}")
        cls.probe = json.loads(result.stdout.strip().splitlines()[-1])

    def test_every_scheduled_task_is_registered(self) -> None:
        scheduled = {entry["task"] for entry in self.probe["schedule"].values()}
        self.assertEqual(2, len(scheduled))
        self.assertLessEqual(scheduled, set(self.probe["registered"]))

    def test_schedule_runs_daily_alert_and_weekly_report_in_local_time(self) -> None:
        self.assertEqual("Europe/Madrid", self.probe["timezone"])
        crontabs = sorted(entry["crontab"] for entry in self.probe["schedule"].values())
        self.assertEqual(
            [
                "<crontab: 0 7 * * * (m/h/dM/MY/d)>",
                "<crontab: 0 8 * * monday (m/h/dM/MY/d)>",
            ],
            crontabs,
        )

    def test_broker_comes_from_environment_and_results_are_not_required(self) -> None:
        self.assertEqual("memory://", self.probe["broker_url"])
        self.assertIsNone(self.probe["result_backend"])

    def test_flask_secrets_are_not_copied_into_celery_configuration(self) -> None:
        for name, value in SECRET_SENTINELS.items():
            with self.subTest(name=name):
                self.assertFalse(
                    value in self.probe["conf_dump"],
                    f"{name} value leaked into the Celery configuration",
                )

    def test_worker_refuses_to_start_without_broker(self) -> None:
        result = _run_worker_probe(CELERY_BROKER_URL="")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("CELERY_BROKER_URL", result.stderr)


if __name__ == "__main__":
    unittest.main()
