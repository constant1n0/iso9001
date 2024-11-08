# celery_beat_schedule.py
from celery.schedules import crontab
from celery_worker import celery

celery.conf.beat_schedule = {
    "enviar-reporte-auditorias-semanal": {
        "task": "enviar_reporte_auditorias",
        "schedule": crontab(day_of_week="monday", hour=8, minute=0),
    },
}
