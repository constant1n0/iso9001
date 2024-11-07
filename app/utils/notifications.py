from flask_mail import Message
from ..extensions import mail
from flask import current_app

def enviar_notificacion(destinatario, asunto, cuerpo):
    with current_app.app_context():
        msg = Message(asunto, recipients=[destinatario])
        msg.body = cuerpo
        mail.send(msg)

def enviar_reporte(destinatario, asunto, cuerpo, adjunto, nombre_adjunto):
    """
    Envía un correo con un archivo PDF adjunto.
    """
    with current_app.app_context():
        msg = Message(asunto, recipients=[destinatario])
        msg.body = cuerpo
        msg.attach(nombre_adjunto, "application/pdf", adjunto)
        mail.send(msg)