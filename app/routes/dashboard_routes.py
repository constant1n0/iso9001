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

from flask import Blueprint, render_template, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..models import (
    Auditoria, CambioSGC, Capacitacion, EstadoNoConformidad, NoConformidad, SatisfaccionCliente,
    TareaCambio,
)
from ..services import (
    audits, context_issues, documents, management_reviews, people, providers, qms_changes,
    quality_objectives, risks,
)
from ..services.nonconformities import OPEN_STATES
from ..utils.permissions import can
from ..utils.web_actor import current_actor

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# Document reviews (DC5 of document-control) shown when due within this many days.
REVIEW_WINDOW_DAYS = 30
# Audits still to be held are "upcoming" when dated within this many days.
UPCOMING_AUDIT_DAYS = 7
# What ``risks.attention`` flags (RO-3 of risks-opportunities), in the card's order:
# its key, label and the list filtered to the same records.
RISK_ALERTS = (
    ('riesgos_altos_sin_accion', 'Riesgos altos o críticos sin acción abierta',
     'riesgo.list_records', {'alto_sin_accion': 1}),
    ('riesgos_revision_vencida', 'Riesgos con la revisión vencida', 'riesgo.list_records',
     {'revision_vencida': 1}),
    ('oportunidades_revision_vencida', 'Oportunidades con la revisión vencida',
     'oportunidad.list_records', {'revision_vencida': 1}),
    ('riesgos_sin_valorar', 'Riesgos sin valorar', 'riesgo.list_records',
     {'sin_valorar': 1, 'abierto': 1}),
    ('oportunidades_sin_valorar', 'Oportunidades sin valorar', 'oportunidad.list_records',
     {'sin_valorar': 1, 'abierto': 1}),
)
# What ``providers.attention`` flags (EP6 of external-providers), in the card's order:
# its key, label and the provider list filtered to the same providers.
PROVIDER_ALERTS = (
    ('evaluaciones_vencidas', 'Proveedores con la evaluación vencida',
     {'evaluacion_vencida': 1}),
    ('bloqueados_con_procesos', 'Proveedores bloqueados con procesos',
     {'estado': 'bloqueado', 'con_procesos': 1}),
    ('aprobados_no_aptos', 'Proveedores aprobados con resultado «No apto»',
     {'estado': 'aprobado', 'ultimo_resultado': 'no_apto'}),
)

# What ``context_issues.attention`` flags (OC5 of organization-context), in the card's
# order: its key, label and the list filtered to the same records.
CONTEXT_ALERTS = (
    ('cuestiones_revision_vencida', 'Cuestiones del contexto con la revisión vencida',
     'contexto.list_issues'),
    ('partes_revision_vencida', 'Partes interesadas con la revisión vencida',
     'parte_interesada.listar_partes_interesadas'),
)

# What ``qms_changes.attention`` flags (CP6 of change-planning), in the card's order: its
# key, label and the change list filtered to the same changes. Each alert counts changes,
# as its list shows them: overdue tasks count the changes holding them.
CHANGE_ALERTS = (
    ('pendientes_aprobacion', 'Cambios pendientes de aprobación', {'estado': 'propuesto'}),
    ('fuera_de_plazo', 'Cambios aprobados con la implantación vencida', {'fuera_de_plazo': 1}),
    ('tareas_vencidas', 'Cambios con tareas vencidas', {'tareas_vencidas': 1}),
    ('eficacia_pendiente', 'Cambios implantados sin revisar su eficacia',
     {'estado': 'implantado'}),
)


def _change_id(found: CambioSGC | TareaCambio) -> int:
    """The change a flagged record is or belongs to."""
    return found.cambio_id if isinstance(found, TareaCambio) else found.id


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

    no_conformidades_pendientes = NoConformidad.query.filter(
        abiertas
    ).order_by(NoConformidad.fecha_detectada).all()
    proximas_capacitaciones = Capacitacion.query.filter(
        Capacitacion.fecha.between(hoy, hoy + timedelta(days=30))
    ).order_by(Capacitacion.fecha).all()

    # Audit figures, shown only to the roles that read audits (DD2 of
    # documented-defects); None hides the count, the figure and the card.
    # total_auditorias counts every audit recorded, whatever its state or date.
    total_auditorias = proximas_auditorias = None
    if can('read', 'audits'):
        total_auditorias = Auditoria.query.count()
        proximas_auditorias = audits.upcoming(db.session, current_actor(), today=hoy,
                                              within_days=UPCOMING_AUDIT_DAYS)

    # Reviews overdue or due soon, among the documents the user may read
    # (an operativo only reads documents in force); None hides the card.
    revisiones_documentales = None
    if can('read', 'documents'):
        revisiones_documentales = documents.due_for_review(
            db.session, current_actor(), today=hoy, within_days=REVIEW_WINDOW_DAYS)

    # Active quality objectives off target or with an overdue measurement (QO3
    # of quality-objectives); every role reads them. None hides the card.
    objetivos_desviados = None
    if can('read', 'quality_objectives'):
        objetivos_desviados = quality_objectives.off_track(db.session, current_actor(), today=hoy)

    # Overdue management review actions (MR3 of management-review), read by
    # administrators and auditors; None hides the card.
    acciones_revision = None
    responsables_acciones = {}
    if can('read', 'management_review'):
        acciones_revision = management_reviews.overdue_actions(db.session, current_actor(),
                                                               today=hoy)
        responsables_acciones = people.names(db.session, current_actor(), (
            accion.responsable_id for accion in acciones_revision))

    # Risks and opportunities needing attention, each with its count and filtered
    # list; every role reads them. None hides the card, and nothing flagged leaves
    # it empty.
    avisos_riesgos = None
    if can('read', 'risks_opportunities'):
        flagged = risks.attention(db.session, current_actor(), today=hoy)
        avisos_riesgos = [(label, url_for(endpoint, **query), len(flagged[key]))
                          for key, label, endpoint, query in RISK_ALERTS if flagged[key]]

    # External providers needing attention, each with its count and filtered list;
    # every role reads them. None hides the card, and nothing flagged leaves it empty.
    avisos_proveedores = None
    if can('read', 'external_providers'):
        flagged = providers.attention(db.session, current_actor(), today=hoy)
        avisos_proveedores = [(label, url_for('proveedor.list_providers', **query),
                               len(flagged[key]))
                              for key, label, query in PROVIDER_ALERTS if flagged[key]]

    # Context issues and interested parties whose review is overdue; every role reads
    # them. None hides the card, and nothing flagged leaves it empty.
    avisos_contexto = None
    if can('read', 'organization_context') and can('read', 'interested_parties'):
        flagged = context_issues.attention(db.session, current_actor(), today=hoy)
        avisos_contexto = [(label, url_for(endpoint, revision_vencida=1), len(flagged[key]))
                           for key, label, endpoint in CONTEXT_ALERTS if flagged[key]]

    # Changes to the QMS needing attention, each with its count and filtered list; every
    # role reads them. None hides the card, and nothing flagged leaves it empty.
    avisos_cambios = None
    if can('read', 'qms_changes'):
        flagged = qms_changes.attention(db.session, current_actor(), today=hoy)
        avisos_cambios = [(label, url_for('changes.list_changes', **query),
                           len({_change_id(found) for found in flagged[key]}))
                          for key, label, query in CHANGE_ALERTS if flagged[key]]

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
        total_auditorias=total_auditorias,
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
        objetivos_desviados=objetivos_desviados,
        acciones_revision=acciones_revision,
        responsables_acciones=responsables_acciones,
        avisos_riesgos=avisos_riesgos,
        avisos_proveedores=avisos_proveedores,
        avisos_contexto=avisos_contexto,
        avisos_cambios=avisos_cambios,
        review_window_days=REVIEW_WINDOW_DAYS,
        upcoming_audit_days=UPCOMING_AUDIT_DAYS,
        chart_data=chart_data,
    )
