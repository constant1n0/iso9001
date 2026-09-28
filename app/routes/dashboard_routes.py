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

from datetime import timedelta

from flask import Blueprint, render_template
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..models import Auditoria, Capacitacion, NoConformidad, SatisfaccionCliente

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@bp.route('/', methods=['GET'])
@login_required
def dashboard():
    """
    Carga el panel de control con gráficos, estadísticas y notificaciones.
    """
    hoy = local_today()

    no_conformidades_abiertas = NoConformidad.query.filter_by(estado="Abierta").count()
    no_conformidades_cerradas = NoConformidad.query.filter_by(estado="Cerrada").count()

    proximas_auditorias = Auditoria.query.filter(
        Auditoria.fecha.between(hoy, hoy + timedelta(days=7))
    ).order_by(Auditoria.fecha).all()
    no_conformidades_pendientes = NoConformidad.query.filter_by(
        estado="Abierta"
    ).order_by(NoConformidad.fecha_detectada).all()
    proximas_capacitaciones = Capacitacion.query.filter(
        Capacitacion.fecha.between(hoy, hoy + timedelta(days=30))
    ).order_by(Capacitacion.fecha).all()

    # Puntuación media de satisfacción por mes
    mes = db.func.extract('month', SatisfaccionCliente.fecha_encuesta).label('mes')
    puntuaciones_meses = db.session.query(
        mes, db.func.avg(SatisfaccionCliente.puntuacion)
    ).group_by(mes).order_by(mes).all()

    chart_data = {
        "no_conformidades": {
            "abiertas": no_conformidades_abiertas,
            "cerradas": no_conformidades_cerradas,
        },
        "satisfaccion": {
            "meses": [int(m) for m, _ in puntuaciones_meses],
            "promedios": [round(float(avg), 2) for _, avg in puntuaciones_meses],
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
        chart_data=chart_data,
    )
