# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Celery application for scheduled audit notifications.

Run from the project root with the same environment as the web app:

    celery -A celery_worker.celery worker --loglevel=info
    celery -A celery_worker.celery beat --loglevel=info

The worker and beat need ``CELERY_BROKER_URL`` (for example a Redis URL).
"""

from celery import Celery
from celery.schedules import crontab
from flask import Flask

from app import create_app
from app import audit_notifications


def create_celery(flask_app: Flask) -> Celery:
    """Build a Celery app that runs every task inside ``flask_app``'s context.

    Only Celery settings are passed on: copying the whole Flask config would
    expose secrets such as ``SECRET_KEY`` through ``celery inspect conf``.
    """
    broker_url = flask_app.config.get("CELERY_BROKER_URL")
    if not broker_url:
        raise RuntimeError(
            "CELERY_BROKER_URL no está configurada. Configure la variable de "
            "entorno CELERY_BROKER_URL para ejecutar Celery."
        )

    celery_app = Celery(flask_app.import_name)
    celery_app.conf.update(
        broker_url=broker_url,
        result_backend=flask_app.config.get("CELERY_RESULT_BACKEND") or None,
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
        timezone=flask_app.config["APP_TIMEZONE"],
        enable_utc=True,
    )

    class FlaskContextTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = FlaskContextTask
    return celery_app


app = create_app()
celery = create_celery(app)


@celery.task(name="iso9001.send_pending_audits_report")
def send_pending_audits_report() -> int:
    """Weekly report of pending audits for administrators."""
    return audit_notifications.send_pending_audits_report()


@celery.task(name="iso9001.send_upcoming_audits_alert")
def send_upcoming_audits_alert() -> int:
    """Daily reminder to auditors of active audits in the next seven days."""
    return audit_notifications.send_upcoming_audits_alert()


# Times are local to APP_TIMEZONE.
celery.conf.beat_schedule = {
    "upcoming-audits-alert-daily": {
        "task": send_upcoming_audits_alert.name,
        "schedule": crontab(hour=7, minute=0),
    },
    "pending-audits-report-weekly": {
        "task": send_pending_audits_report.name,
        "schedule": crontab(day_of_week="monday", hour=8, minute=0),
    },
}
