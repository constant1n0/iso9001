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

from datetime import date, timedelta

from flask import Blueprint, render_template
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..models import (
    Auditoria, Capacitacion, EstadoNoConformidad, NoConformidad, SatisfaccionCliente,
)
from ..services import documents
from ..services.nonconformities import OPEN_STATES
from ..utils.permissions import can
from ..utils.web_actor import current_actor

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# Document reviews (DC5 of document-control) shown when due within this many days.
REVIEW_WINDOW_DAYS = 30


@bp.route('/', methods=['GET'])
@login_required
def dashboard():
    """
    Carga el panel de control con gráficos, estadísticas y notificaciones.
    """
    hoy = local_today()

    # Open: not yet closed or cancelled. A cancelled one counts as neither.
    abiertas = NoConformidad.estado.in_(OPEN_STATES)
    no_conformidades_abiertas = NoConformidad.query.filter(abiertas).count()
    no_conformidades_cerradas = NoConformidad.query.filter_by(
        estado=EstadoNoConformidad.cerrada
    ).count()

    proximas_auditorias = Auditoria.query.filter(
        Auditoria.fecha.between(hoy, hoy + timedelta(days=7))
    ).order_by(Auditoria.fecha).all()
    no_conformidades_pendientes = NoConformidad.query.filter(
        abiertas
    ).order_by(NoConformidad.fecha_detectada).all()
    proximas_capacitaciones = Capacitacion.query.filter(
        Capacitacion.fecha.between(hoy, hoy + timedelta(days=30))
    ).order_by(Capacitacion.fecha).all()

    # Reviews overdue or due soon, among the documents the user may read
    # (an operativo only reads documents in force); None hides the card.
    revisiones_documentales = None
    if can('read', 'documents'):
        revisiones_documentales = documents.due_for_review(
            db.session, current_actor(), today=hoy, within_days=REVIEW_WINDOW_DAYS)

    # Puntuación media de satisfacción por mes
    # (year, month) grouping, last 12 calendar months including the current one
    indice_actual = hoy.year * 12 + hoy.month - 1
    primer_indice = indice_actual - 11
    desde = date(primer_indice // 12, primer_indice % 12 + 1, 1)
    anio = db.func.extract('year', SatisfaccionCliente.fecha_encuesta).label('anio')
    mes = db.func.extract('month', SatisfaccionCliente.fecha_encuesta).label('mes')
    puntuaciones_meses = db.session.query(
        anio, mes, db.func.avg(SatisfaccionCliente.puntuacion)
    ).filter(
        SatisfaccionCliente.fecha_encuesta >= desde
    ).group_by(anio, mes).order_by(anio, mes).all()

    chart_data = {
        "no_conformidades": {
            "abiertas": no_conformidades_abiertas,
            "cerradas": no_conformidades_cerradas,
        },
        "satisfaccion": {
            "meses": [f"{int(a):04d}-{int(m):02d}" for a, m, _ in puntuaciones_meses],
            "promedios": [round(float(avg), 2) for _, _, avg in puntuaciones_meses],
        },
    }

    return render_template(
        'dashboard/dashboard.html',
        hoy=hoy,
        total_auditorias=Auditoria.query.count(),
        total_no_conformidades=NoConformidad.query.count(),
        no_conformidades_abiertas=no_conformidades_abiertas,
        no_conformidades_cerradas=no_conformidades_cerradas,
        promedio_satisfaccion=db.session.query(
            db.func.avg(SatisfaccionCliente.puntuacion)
        ).scalar() or 0,
        total_capacitaciones=Capacitacion.query.count(),
        proximas_auditorias=proximas_auditorias,
        no_conformidades_pendientes=no_conformidades_pendientes,
        proximas_capacitaciones=proximas_capacitaciones,
        revisiones_documentales=revisiones_documentales,
        review_window_days=REVIEW_WINDOW_DAYS,
        chart_data=chart_data,
    )
