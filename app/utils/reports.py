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
