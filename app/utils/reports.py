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
from enum import Enum
from typing import Any, TypeVar

from flask import render_template

from ..extensions import db
from ..models import (
    AccionCorrectiva, Auditoria, Capacitacion, EstadoNoConformidad, NoConformidad,
    ResultadoVerificacion, SatisfaccionCliente,
)
from .pdf import render_pdf

E = TypeVar("E", bound=Enum)


def _month_bounds(fecha: date) -> tuple[date, date]:
    """Return the first day of ``fecha``'s month and of the next month."""
    start = fecha.replace(day=1)
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


def _count_by(column: Any, enum_cls: type[E], condition: Any) -> dict[E, int]:
    """Rows matching ``condition``, counted by ``column``, for every member of ``enum_cls``.

    Members come in the enum's order, with zero when no row has them.
    """
    found = dict(
        db.session.query(column, db.func.count()).filter(condition).group_by(column).all()
    )
    return {member: found.get(member, 0) for member in enum_cls}


def _monthly_report_context(fecha: date) -> dict:
    """Count only the records dated within the calendar month of ``fecha``.

    Nonconformities count by their detection date (split by their current
    state) and corrective actions by their verification date (split by result).
    """
    start, end = _month_bounds(fecha)

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
    return dict(
        fecha=fecha,
        total_auditorias=Auditoria.query.filter(in_month(Auditoria.fecha)).count(),
        total_no_conformidades=sum(por_estado.values()),
        no_conformidades_por_estado=por_estado,
        acciones_verificadas=sum(verificadas.values()),
        acciones_eficaces=verificadas[ResultadoVerificacion.eficaz],
        acciones_no_eficaces=verificadas[ResultadoVerificacion.no_eficaz],
        promedio_satisfaccion=promedio_satisfaccion,
        total_capacitaciones=Capacitacion.query.filter(
            in_month(Capacitacion.fecha)
        ).count(),
    )


def render_monthly_report_html(fecha: date) -> str:
    """Render the HTML summary of the key quality indicators."""
    return render_template('reportes/reporte_mensual.html', **_monthly_report_context(fecha),
                           generado=fecha)


def generar_reporte_pdf(fecha: date) -> bytes:
    """Build the monthly summary report as PDF bytes."""
    return render_pdf('reportes/reporte_mensual.html', **_monthly_report_context(fecha),
                      generado=fecha)
