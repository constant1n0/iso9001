"""Read-only risk/opportunity action effectiveness (ISO 9001 9.1.3 e).

Adapters own authorization and period validation, as for conformity analysis.
Membership uses completion dates, not evaluation dates or the state cut-off.
"""
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccionRiesgo, EstadoAccionRiesgo, Oportunidad, Riesgo

FIGURES = (("total", "Completadas en el periodo"), ("eficaz", "Eficaces"),
           ("no_eficaz", "No eficaces"), ("sin_evaluar", "Sin evaluar"))


def in_period(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    """Count completed actions by current effectiveness and parent type.

    Both completion-date edges are included. Completed actions without an
    evaluation are ``sin_evaluar``; pending/in-progress/cancelled actions never
    participate. Evaluations may fall outside the period. One query keeps
    counts, descriptions and parent names consistent within this input. Lists
    are latest completion first, then ascending action id to break ties;
    the review adapter bounds their size and text using its usual helpers.
    """
    totals = {key: 0 for key, _ in FIGURES}
    by_type = {kind: dict(totals) for kind in ("riesgos", "oportunidades")}
    ineffective = []
    rows = session.execute(select(
        AccionRiesgo.id, AccionRiesgo.descripcion, AccionRiesgo.fecha_completada,
        AccionRiesgo.eficacia, AccionRiesgo.riesgo_id, AccionRiesgo.oportunidad_id,
        Riesgo.titulo, Oportunidad.titulo,
    ).outerjoin(Riesgo, AccionRiesgo.riesgo_id == Riesgo.id)
      .outerjoin(Oportunidad, AccionRiesgo.oportunidad_id == Oportunidad.id)
      .where(AccionRiesgo.estado == EstadoAccionRiesgo.completada,
             AccionRiesgo.fecha_completada.between(desde, hasta))
      .order_by(AccionRiesgo.fecha_completada.desc(), AccionRiesgo.id))
    for action_id, description, completed, result, risk_id, opp_id, risk, opp in rows:
        key = result.name if result else "sin_evaluar"
        kind = "riesgos" if risk_id is not None else "oportunidades"
        for bucket in (totals, by_type[kind]):
            bucket["total"] += 1
            bucket[key] += 1
        if key == "no_eficaz":
            ineffective.append({
                "id": action_id, "descripcion": description,
                "tipo": "riesgo" if risk_id is not None else "oportunidad",
                "registro_id": risk_id if risk_id is not None else opp_id,
                "titulo": risk if risk_id is not None else opp,
                "fecha_completada": completed.isoformat(),
            })
    return {"totales": totals, "por_tipo": by_type, "no_eficaces_lista": ineffective}
