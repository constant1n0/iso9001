# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

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