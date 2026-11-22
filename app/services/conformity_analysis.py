"""Read-only product/service conformity for ISO 9001 9.1.3 a.

Adapters own authorization and period validation, as for other review inputs.
Dated events use the inclusive period, not the cut-off used to judge states.
NCs are distinct citations in controls/preservation and property incidents by
``date``, or completed post-delivery activities by ``result_date``. An unlinked
citation still counts (process «Sin orden», customer «Sin cliente»). Multiple
orders attribute the NC to each affected group, once per group, once overall.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from ..models import (
    Cliente, PostDeliveryActivity, PostDeliveryStatus, ProcesoOperacion, ProductRelease,
    ProductionControlRecord, ProductionOrder, ProductionPreservationRecord,
    PropertyIncident, ReleaseStatus, ReleaseVerification,
    post_delivery_orders, property_orders,
)

FIGURES = (
    ("liberadas", "Liberadas sin concesión"),
    ("con_concesion", "Liberadas con concesión"),
    ("rechazadas", "Rechazadas"),
    ("verificaciones", "Verificaciones de liberaciones decididas"),
    ("porcentaje_conformes", "Verificaciones conformes (%)"),
    ("porcentaje_no_conformes", "Verificaciones no conformes (%)"),
    ("no_conformidades", "No conformidades de producción"),
)


@dataclass
class _Figures:
    """Independent counters and a deduplicated citation set for one bucket."""

    counts: Counter[str] = field(default_factory=Counter)
    ncs: set[int] = field(default_factory=set)

    def values(self) -> dict[str, Any]:
        """JSON-safe metrics; percentages are undefined without verifications."""
        total = self.counts["conforming"] + self.counts["nonconforming"]
        return {
            **{key: self.counts[key] for key in ("liberadas", "con_concesion", "rechazadas")},
            "verificaciones": total,
            "porcentaje_conformes": (
                round(100 * self.counts["conforming"] / total, 2) if total else None),
            "porcentaje_no_conformes": (
                round(100 * self.counts["nonconforming"] / total, 2) if total else None),
            "no_conformidades": len(self.ncs),
        }


def in_period(session: Session, desde: date, hasta: date) -> dict[str, Any]:
    """Return totals and name-ordered groups by the order's process and customer.

    Only released/rejected decisions dated in the period participate; all their
    verifications count regardless of verification date. NC evidence has its own
    period membership, independent of releases and of NC detection dates. Group
    identifiers and names are current; holding a review freezes them together.
    """
    totals = _Figures()
    processes: dict[tuple, _Figures] = {}
    customers: dict[tuple, _Figures] = {}
    dimensions = (ProductionOrder.process_id, ProcesoOperacion.proceso,
                  ProductionOrder.customer_id, Cliente.nombre)

    def buckets(process_id: int | None, process: str | None,
                customer_id: int | None, customer: str | None) -> tuple[_Figures, ...]:
        """Resolve all three aggregates without attributing by evidence owner."""
        return (totals,
                processes.setdefault((process_id, process or "Sin orden"), _Figures()),
                customers.setdefault((customer_id, customer or "Sin cliente"), _Figures()))

    decided = select(ProductRelease.id).where(
        ProductRelease.status.in_((ReleaseStatus.released, ReleaseStatus.rejected)),
        ProductRelease.decision_date.between(desde, hasta))
    releases = session.execute(select(
        ProductRelease.id, ProductRelease.status, ProductRelease.concession, *dimensions,
    ).join(ProductionOrder, ProductRelease.order_id == ProductionOrder.id)
      .join(ProcesoOperacion, ProductionOrder.process_id == ProcesoOperacion.id_proceso)
      .outerjoin(Cliente, ProductionOrder.customer_id == Cliente.id)
      .where(ProductRelease.id.in_(decided)))
    release_buckets: dict[int, tuple[_Figures, ...]] = {}
    for release_id, status, concession, *group in releases:
        targets = release_buckets[release_id] = buckets(*group)
        key = ("rechazadas" if status is ReleaseStatus.rejected else
               "con_concesion" if concession else "liberadas")
        for target in targets:
            target.counts[key] += 1
    # Count the verifications of exactly the releases read above: re-running
    # ``decided`` would see a release decided meanwhile, without its buckets.
    for release_id, result in session.execute(select(
            ReleaseVerification.release_id, ReleaseVerification.result).where(
                ReleaseVerification.release_id.in_(list(release_buckets)))):
        for target in release_buckets[release_id]:
            target.counts[result.name] += 1

    # UNION removes repeated (NC, order) citations before assigning group sets.
    citations = union(
        *(select(model.nonconformity_id.label("nc"), model.order_id.label("order_id"))
          .where(model.nonconformity_id.is_not(None), model.date.between(desde, hasta))
          for model in (ProductionControlRecord, ProductionPreservationRecord)),
        select(PropertyIncident.nonconformity_id, property_orders.c.order_id)
        .outerjoin(property_orders, PropertyIncident.item_id == property_orders.c.item_id)
        .where(PropertyIncident.nonconformity_id.is_not(None),
               PropertyIncident.date.between(desde, hasta)),
        select(PostDeliveryActivity.nonconformity_id, post_delivery_orders.c.order_id)
        .outerjoin(post_delivery_orders,
                   PostDeliveryActivity.id == post_delivery_orders.c.activity_id)
        .where(PostDeliveryActivity.nonconformity_id.is_not(None),
               PostDeliveryActivity.status == PostDeliveryStatus.completed,
               PostDeliveryActivity.result_date.between(desde, hasta)),
    ).subquery()
    for nc, *group in session.execute(select(citations.c.nc, *dimensions)
            .select_from(citations)
            .outerjoin(ProductionOrder, citations.c.order_id == ProductionOrder.id)
            .outerjoin(ProcesoOperacion,
                       ProductionOrder.process_id == ProcesoOperacion.id_proceso)
            .outerjoin(Cliente, ProductionOrder.customer_id == Cliente.id)):
        for target in buckets(*group):
            target.ncs.add(nc)

    def listed(groups: dict[tuple, _Figures]) -> list[dict[str, Any]]:
        """Serialize stable group identifiers, names and complete metrics."""
        return [{"id": key[0], "nombre": key[1], **figures.values()}
                for key, figures in sorted(groups.items(),
                                           key=lambda item: (item[0][1], item[0][0] or 0))]

    return {"totales": totals.values(), "por_proceso": listed(processes),
            "por_cliente": listed(customers)}
