# celery_beat_schedule.py
from celery.schedules import crontab
from celery_worker import celery

celery.conf.beat_schedule = {
    "enviar-reporte-auditorias-semanal": {
        "task": "enviar_reporte_auditorias",
        "schedule": crontab(day_of_week="monday", hour=8, minute=0),
    },
}

'''
Para ejecutar la tarea programada, inicia Redis, luego inicia el worker de Celery y Celery Beat:
##Bash
# Ejecuta Redis en tu sistema (si no está en ejecución)
redis-server

# Inicia el worker de Celery
celery -A celery_worker.celery worker --loglevel=info

# Inicia Celery Beat
celery -A celery_worker.celery beat --loglevel=info

Prueba de la Tarea Automática
Para verificar que la tarea se ejecuta correctamente, puedes lanzar la tarea manualmente desde el shell de Python:

from celery_worker import enviar_reporte_semanal
enviar_reporte_semanal.apply_async()

Esto te permitirá comprobar que el correo de resumen se envía correctamente y que los administradores reciben el reporte semanal con las auditorías y no conformidades pendientes.
'''