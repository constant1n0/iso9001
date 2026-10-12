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

from datetime import date, timedelta
from enum import Enum
from typing import Any, TypeVar

from flask import render_template
from sqlalchemy import select

from ..extensions import db
from ..models import (
    AccionCorrectiva, Auditoria, Capacitacion, EstadoAuditoriaEnum, EstadoNoConformidad,
    EstadoObjetivo, HallazgoAuditoria, MedicionObjetivo, NoConformidad, ObjetivoCalidad,
    ResultadoVerificacion, SatisfaccionCliente, TipoHallazgo,
)
from ..services import quality_objectives
from .pdf import render_pdf

E = TypeVar("E", bound=Enum)


def _month_bounds(fecha: date) -> tuple[date, date]:
    """Return the first day of ``fecha``'s month and of the next month."""
    start = fecha.replace(day=1)
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


def _count_by(
    column: Any, enum_cls: type[E], condition: Any, join: tuple[Any, Any] | None = None
) -> dict[E, int]:
    """Rows matching ``condition``, counted by ``column``, for every member of ``enum_cls``.

    ``join`` is an optional ``(target, onclause)`` the condition needs. Members
    come in the enum's order, with zero when no row has them.
    """
    query = db.session.query(column, db.func.count())
    if join is not None:
        query = query.join(*join)
    found = dict(query.filter(condition).group_by(column).all())
    return {member: found.get(member, 0) for member in enum_cls}


def _local_today() -> date:
    """Today in ``APP_TIMEZONE``, imported late: ``audit_notifications`` imports this module."""
    from ..audit_notifications import local_today

    return local_today()


def _objective_figures(cutoff: date) -> dict[str, Any]:
    """The active quality objectives on ``cutoff``, and how many meet or are overdue.

    Active means in state ``activo`` and started by ``cutoff``. Each one is
    judged (``quality_objectives.summary``) on its measurements up to
    ``cutoff`` and on that day, so it is shown as it stood then.
    """
    # The state is the current one: objectives keep no date of a state change.
    # So the objectives that became ``alcanzado`` or ``no_alcanzado`` cannot be
    # counted by month and are left out, and one closed after ``cutoff`` no
    # longer counts as active.
    active = list(db.session.scalars(select(ObjetivoCalidad).where(
        ObjetivoCalidad.estado == EstadoObjetivo.activo, ObjetivoCalidad.fecha_inicio <= cutoff)))
    measured: dict[int, list[MedicionObjetivo]] = {objective.id: [] for objective in active}
    if measured:
        for measurement in db.session.scalars(select(MedicionObjetivo).where(
                MedicionObjetivo.objetivo_id.in_(list(measured)),
                MedicionObjetivo.fecha <= cutoff).order_by(MedicionObjetivo.fecha)):
            measured[measurement.objetivo_id].append(measurement)
    summaries = [quality_objectives.summary(objective, measured[objective.id], today=cutoff)
                 for objective in active]
    return dict(
        objetivos_activos=len(active),
        objetivos_en_meta=sum(summary["cumple"] is True for summary in summaries),
        objetivos_medicion_vencida=sum(summary["medicion_vencida"] for summary in summaries),
        objetivos_fecha_corte=cutoff,
    )


def _monthly_report_context(fecha: date, *, today: date | None = None) -> dict:
    """Count only the records dated within the calendar month of ``fecha``.

    Nonconformities count by their detection date (split by their current
    state) and corrective actions by their verification date (split by result).
    Audits completed are those of the month in state ``COMPLETADA``; audit
    findings count by their audit's date (the detection date of the
    nonconformity a nonconformity finding opens), split by type.

    Quality objective measurements count by their date. The active objectives,
    and those meeting their target or with an overdue measurement, are judged
    on the month's last day, or on ``today`` when earlier (``_objective_figures``).
    ``today`` defaults to the local date in ``APP_TIMEZONE``.
    """
    start, end = _month_bounds(fecha)
    today = _local_today() if today is None else today

    def in_month(column):
        return (column >= start) & (column < end)

    promedio_satisfaccion = (
        db.session.query(db.func.avg(SatisfaccionCliente.puntuacion))
        .filter(in_month(SatisfaccionCliente.fecha_encuesta))
        .scalar()
        or 0
    )
    por_estado = _count_by(NoConformidad.estado, EstadoNoConformidad,
                           in_month(NoConformidad.fecha_detectada))
    verificadas = _count_by(AccionCorrectiva.resultado_verificacion, ResultadoVerificacion,
                            in_month(AccionCorrectiva.fecha_verificacion))
    hallazgos = _count_by(HallazgoAuditoria.tipo, TipoHallazgo, in_month(Auditoria.fecha),
                          join=(Auditoria, HallazgoAuditoria.auditoria_id == Auditoria.id))
    return dict(
        fecha=fecha,
        total_auditorias=Auditoria.query.filter(in_month(Auditoria.fecha)).count(),
        auditorias_completadas=Auditoria.query.filter(
            in_month(Auditoria.fecha), Auditoria.estado == EstadoAuditoriaEnum.COMPLETADA
        ).count(),
        hallazgos_por_tipo=hallazgos,
        total_no_conformidades=sum(por_estado.values()),
        no_conformidades_por_estado=por_estado,
        acciones_verificadas=sum(verificadas.values()),
        acciones_eficaces=verificadas[ResultadoVerificacion.eficaz],
        acciones_no_eficaces=verificadas[ResultadoVerificacion.no_eficaz],
        promedio_satisfaccion=promedio_satisfaccion,
        total_capacitaciones=Capacitacion.query.filter(
            in_month(Capacitacion.fecha)
        ).count(),
        mediciones_objetivos=MedicionObjetivo.query.filter(
            in_month(MedicionObjetivo.fecha)
        ).count(),
        **_objective_figures(min(end - timedelta(days=1), today)),
    )


def render_monthly_report_html(fecha: date, *, today: date | None = None) -> str:
    """Render the HTML summary of the key quality indicators."""
    return render_template('reportes/reporte_mensual.html',
                           **_monthly_report_context(fecha, today=today), generado=fecha)


def generar_reporte_pdf(fecha: date, *, today: date | None = None) -> bytes:
    """Build the monthly summary report as PDF bytes."""
    return render_pdf('reportes/reporte_mensual.html',
                      **_monthly_report_context(fecha, today=today), generado=fecha)
