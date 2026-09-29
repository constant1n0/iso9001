# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Monthly quality summary report rendered as PDF."""

from datetime import date

from flask import render_template

from ..extensions import db
from ..models import Auditoria, Capacitacion, NoConformidad, SatisfaccionCliente
from .pdf import render_pdf


def _monthly_report_context(fecha: date) -> dict:
    promedio_satisfaccion = (
        db.session.query(db.func.avg(SatisfaccionCliente.puntuacion)).scalar() or 0
    )
    return dict(
        fecha=fecha,
        total_auditorias=Auditoria.query.count(),
        total_no_conformidades=NoConformidad.query.count(),
        promedio_satisfaccion=promedio_satisfaccion,
        total_capacitaciones=Capacitacion.query.count(),
    )


def render_monthly_report_html(fecha: date) -> str:
    """Render the HTML summary of the key quality indicators."""
    return render_template('reportes/reporte_mensual.html', **_monthly_report_context(fecha),
                           generado=fecha)


def generar_reporte_pdf(fecha: date) -> bytes:
    """Build the monthly summary report as PDF bytes."""
    return render_pdf('reportes/reporte_mensual.html', **_monthly_report_context(fecha),
                      generado=fecha)
