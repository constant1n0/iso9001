# app/routes/dashboard_routes.py

from flask import Blueprint, render_template
from ..models import Auditoria, NoConformidad, SatisfaccionCliente, Capacitacion
from ..extensions import db
from ..utils.notifications import enviar_notificacion  # Importar la función de notificaciones
from datetime import datetime, timedelta

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@bp.route('/', methods=['GET'])
def dashboard():
    """
    Carga el panel de control con gráficos, estadísticas y notificaciones.
    """
    # Estadísticas existentes
    total_auditorias = Auditoria.query.count()
    total_no_conformidades = NoConformidad.query.count()
    promedio_satisfaccion = db.session.query(db.func.avg(SatisfaccionCliente.puntuacion)).scalar()
    total_capacitaciones = Capacitacion.query.count()
    
    # Datos para gráficos
    no_conformidades_abiertas = NoConformidad.query.filter_by(estado="Abierta").count()
    no_conformidades_cerradas = NoConformidad.query.filter_by(estado="Cerrada").count()

    # Configuración de notificaciones
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

    # Notificación por correo para auditorías próximas
    for auditoria in proximas_auditorias:
        asunto = "Recordatorio: Auditoría Programada"
        cuerpo = f"Recuerda que tienes una auditoría programada en {auditoria.area_auditada} para la fecha {auditoria.fecha.strftime('%Y-%m-%d')}."
        enviar_notificacion("correo_destinatario@example.com", asunto, cuerpo)

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
