from flask import render_template
from weasyprint import HTML
from ..models import Auditoria, NoConformidad, SatisfaccionCliente, Capacitacion
from ..extensions import db
from datetime import datetime
import io

def generar_reporte_pdf():
    """
    Genera un reporte PDF con un resumen de las estadísticas clave.
    """
    hoy = datetime.utcnow().date()

    # Consultas de datos para el reporte
    total_auditorias = Auditoria.query.count()
    total_no_conformidades = NoConformidad.query.count()
    promedio_satisfaccion = db.session.query(db.func.avg(SatisfaccionCliente.puntuacion)).scalar() or 0
    total_capacitaciones = Capacitacion.query.count()
    
    # Preparar el HTML para el PDF
    rendered_html = render_template('reportes/reporte_mensual.html',
                                    fecha=hoy,
                                    total_auditorias=total_auditorias,
                                    total_no_conformidades=total_no_conformidades,
                                    promedio_satisfaccion=promedio_satisfaccion,
                                    total_capacitaciones=total_capacitaciones)
    
    # Generar el PDF
    pdf_file = HTML(string=rendered_html).write_pdf()
    
    return pdf_file
