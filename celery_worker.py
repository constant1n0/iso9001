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

from celery import Celery
from app import create_app
from flask_mail import Message
from app.extensions import mail
from datetime import datetime, timedelta

# Inicializa Flask y Celery
app = create_app()
celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Configuración de contexto de aplicación Flask en tareas de Celery
class ContextTask(celery.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)

celery.Task = ContextTask

# Tarea programada: enviar un correo de reporte de auditorías pendientes
@celery.task
def enviar_reporte_auditorias():
    """
    Tarea periódica para enviar un reporte de auditorías pendientes a los administradores.
    """
    from app.models import Auditoria, User, RoleEnum, EstadoAuditoriaEnum

    # Obtener auditorías pendientes usando el Enum correcto
    auditorias_pendientes = Auditoria.query.filter_by(estado=EstadoAuditoriaEnum.PENDIENTE).all()

    # Si no hay auditorías pendientes, salir de la tarea
    if not auditorias_pendientes:
        print("No hay auditorías pendientes para reportar.")
        return

    # Genera el contenido del reporte
    reporte = "\n".join(
        [f"Auditoría ID: {a.id} - Área: {a.area_auditada} - Fecha: {a.fecha.strftime('%Y-%m-%d')}" for a in auditorias_pendientes]
    )

    # Configura y envía el correo a los administradores (usando el Enum correcto)
    administradores = User.query.filter_by(role=RoleEnum.ADMINISTRADOR).all()
    for admin in administradores:
        # Verificar que el admin tenga email configurado
        if not admin.email:
            print(f"Usuario {admin.username} no tiene email configurado")
            continue

        msg = Message(
            subject="Reporte de Auditorías Pendientes",
            sender=app.config['MAIL_DEFAULT_SENDER'],
            recipients=[admin.email]
        )
        msg.body = f"Hola {admin.username},\n\nAquí está el reporte de auditorías pendientes:\n\n{reporte}"

        try:
            mail.send(msg)
            print(f"Correo enviado a {admin.email}")
        except Exception as e:
            print(f"Error al enviar el correo a {admin.email}: {e}")

# Nueva Tarea programada: enviar alertas para auditorías próximas en los próximos 7 días
@celery.task
def enviar_alerta_auditorias_proximas():
    """
    Tarea periódica para enviar un correo a los auditores recordándoles auditorías programadas en los próximos 7 días.
    """
    from app.models import Auditoria, User, RoleEnum

    # Calcula el rango de fechas para los próximos 7 días
    hoy = datetime.utcnow().date()
    fecha_limite = hoy + timedelta(days=7)

    # Buscar auditorías programadas en los próximos 7 días
    auditorias_proximas = Auditoria.query.filter(
        Auditoria.fecha.between(hoy, fecha_limite)
    ).all()

    if auditorias_proximas:
        # Genera el contenido del mensaje
        mensaje = "\n".join(
            [f"Auditoría en {a.area_auditada} - Fecha: {a.fecha.strftime('%Y-%m-%d')}" for a in auditorias_proximas]
        )

        # Obtiene los correos de los usuarios con rol de auditor (usando el Enum correcto)
        auditores = User.query.filter_by(role=RoleEnum.AUDITOR).all()
        for auditor in auditores:
            # Verificar que el auditor tenga email configurado
            if not auditor.email:
                print(f"Usuario {auditor.username} no tiene email configurado")
                continue

            msg = Message(
                subject="Recordatorio de Auditorías Próximas",
                sender=app.config['MAIL_DEFAULT_SENDER'],
                recipients=[auditor.email]
            )
            msg.body = f"Estimado/a {auditor.username},\n\nEstas son las auditorías programadas para los próximos 7 días:\n\n{mensaje}"

            try:
                mail.send(msg)
                print(f"Correo enviado a {auditor.email}")
            except Exception as e:
                print(f"Error al enviar el correo a {auditor.email}: {e}")
