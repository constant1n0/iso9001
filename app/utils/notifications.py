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