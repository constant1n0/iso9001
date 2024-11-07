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