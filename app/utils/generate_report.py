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

from app import create_app
from app.utils.reports import generar_reporte_pdf
from app.utils.notifications import enviar_reporte

app = create_app()

with app.app_context():
    # Generar el reporte en PDF
    pdf_reporte = generar_reporte_pdf()

    # Configurar el correo y enviar
    asunto = "Reporte Mensual del Sistema de Gestión de Calidad"
    cuerpo = "Adjunto encontrarás el reporte mensual del Sistema de Gestión de Calidad."
    destinatario = "destinatario@example.com"
    enviar_reporte(destinatario, asunto, cuerpo, pdf_reporte, "reporte_mensual.pdf")

'''
En el servidor, utiliza cron para programar la ejecución de generate_report.py.

Abre el archivo de tareas de cron:
crontab -e
Añade la siguiente línea para ejecutar el script una vez al mes (por ejemplo, el primer día de cada mes a las 8:00 AM):
0 8 1 * * /path/to/your/venv/bin/python /path/to/your/project/generate_report.py

Reemplaza /path/to/your/venv/bin/python con la ruta de tu intérprete de Python en el entorno virtual.
Reemplaza /path/to/your/project/generate_report.py con la ruta del archivo generate_report.py en tu proyecto.

'''