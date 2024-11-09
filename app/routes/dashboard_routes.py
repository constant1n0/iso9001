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

from flask import Blueprint, render_template
from ..models import Auditoria, NoConformidad, SatisfaccionCliente, Capacitacion
from ..extensions import db
from datetime import datetime, timedelta
from flask_login import login_required

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@bp.route('/', methods=['GET'])
@login_required
def dashboard():
    """
    Carga el panel de control con gráficos, estadísticas y notificaciones.
    """
    # Estadísticas principales
    total_auditorias = Auditoria.query.count()
    total_no_conformidades = NoConformidad.query.count()
    promedio_satisfaccion = db.session.query(db.func.avg(SatisfaccionCliente.puntuacion)).scalar()
    total_capacitaciones = Capacitacion.query.count()
    
    # Datos para gráficos de no conformidades
    no_conformidades_abiertas = NoConformidad.query.filter_by(estado="Abierta").count()
    no_conformidades_cerradas = NoConformidad.query.filter_by(estado="Cerrada").count()

    # Configuración de alertas
    hoy = datetime.utcnow().date()
    
    # Auditorías próximas (en los próximos 7 días)
    proximas_auditorias = Auditoria.query.filter(
        db.func.date(Auditoria.fecha) >= hoy,
        db.func.date(Auditoria.fecha) <= hoy + timedelta(days=7)
    ).all()
    
    # No conformidades abiertas
    no_conformidades_pendientes = NoConformidad.query.filter_by(estado="Abierta").all()
    
    # Capacitaciones próximas (en los próximos 30 días)
    proximas_capacitaciones = Capacitacion.query.filter(
        db.func.date(Capacitacion.fecha) >= hoy,
        db.func.date(Capacitacion.fecha) <= hoy + timedelta(days=30)
    ).all()

    # Gráficos de Satisfacción del Cliente (puntuación promedio por mes)
    puntuaciones_meses_labels = []
    puntuaciones_meses_data = []
    meses = db.session.query(
        db.func.extract('month', SatisfaccionCliente.fecha_encuesta).label('mes'),
        db.func.avg(SatisfaccionCliente.puntuacion).label('promedio')
    ).group_by('mes').order_by('mes').all()

    for mes, promedio in meses:
        puntuaciones_meses_labels.append(mes)
        puntuaciones_meses_data.append(promedio)

    return render_template('dashboard/dashboard.html', 
                           total_auditorias=total_auditorias,
                           total_no_conformidades=total_no_conformidades,
                           promedio_satisfaccion=promedio_satisfaccion or 0,
                           total_capacitaciones=total_capacitaciones,
                           no_conformidades_abiertas=no_conformidades_abiertas,
                           no_conformidades_cerradas=no_conformidades_cerradas,
                           proximas_auditorias=proximas_auditorias,
                           no_conformidades_pendientes=no_conformidades_pendientes,
                           proximas_capacitaciones=proximas_capacitaciones,
                           puntuaciones_meses_labels=puntuaciones_meses_labels,
                           puntuaciones_meses_data=puntuaciones_meses_data)
