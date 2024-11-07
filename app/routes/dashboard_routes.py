# app/routes/dashboard_routes.py

from flask import Blueprint, render_template
from ..models import Auditoria, NoConformidad, SatisfaccionCliente, Capacitacion
from ..extensions import db

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@bp.route('/', methods=['GET'])
def dashboard():
    """
    Carga el panel de control con gráficos y estadísticas.
    """
    # Ejemplo de estadísticas
    total_auditorias = Auditoria.query.count()
    total_no_conformidades = NoConformidad.query.count()
    promedio_satisfaccion = db.session.query(db.func.avg(SatisfaccionCliente.puntuacion)).scalar()
    total_capacitaciones = Capacitacion.query.count()
    
    # Datos para gráficos (ejemplos)
    no_conformidades_abiertas = NoConformidad.query.filter_by(estado="Abierta").count()
    no_conformidades_cerradas = NoConformidad.query.filter_by(estado="Cerrada").count()
    
    return render_template('dashboard/dashboard.html', 
                           total_auditorias=total_auditorias,
                           total_no_conformidades=total_no_conformidades,
                           promedio_satisfaccion=promedio_satisfaccion or 0,
                           total_capacitaciones=total_capacitaciones,
                           no_conformidades_abiertas=no_conformidades_abiertas,
                           no_conformidades_cerradas=no_conformidades_cerradas)
