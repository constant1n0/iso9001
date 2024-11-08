from celery import Celery
from app import create_app, db
from flask_mail import Message
from app.extensions import mail

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

# Ejemplo de una tarea de prueba
@celery.task
def prueba_tarea():
    print("Tarea de prueba ejecutada correctamente.")

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
