from celery import Celery
from app import create_app, db
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
    from app.models import Auditoria, User  # Importa los modelos necesarios

    # Obtener auditorías pendientes
    auditorias_pendientes = Auditoria.query.filter_by(estado="Pendiente").all()
    
    # Si no hay auditorías pendientes, salir de la tarea
    if not auditorias_pendientes:
        print("No hay auditorías pendientes para reportar.")
        return
    
    # Genera el contenido del reporte
    reporte = "\n".join(
        [f"Auditoría ID: {a.id} - Área: {a.area_auditada} - Fecha: {a.fecha.strftime('%Y-%m-%d')}" for a in auditorias_pendientes]
    )
    
    # Configura y envía el correo a los administradores
    administradores = User.query.filter_by(role="Administrador").all()
    for admin in administradores:
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
    from app.models import Auditoria, User  # Importa los modelos necesarios
    
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
        
        # Obtiene los correos de los usuarios con rol de auditor
        auditores = User.query.filter_by(role="AUDITOR").all()
        for auditor in auditores:
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
