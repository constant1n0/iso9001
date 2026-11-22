"""Period conformity through the review's public, read-only input API."""
from datetime import date

from app.extensions import db
from app.models import (
    Cliente, CustomerProperty, NoConformidad, Person, PostDeliveryActivity,
    ProcesoOperacion, ProductRelease, ProductionControlRecord, ProductionOrder,
    ProductionPreservationRecord, PropertyIncident, ReleaseVerification, RoleEnum,
    post_delivery_orders, property_orders,
)
from app.services import management_reviews as mr
from app.services.actor import Actor
from app.services.errors import PermissionDenied
from test_communication_independent import hashes
from test_nonconformity_service import ServiceBase

START, END = date(2026, 4, 1), date(2026, 6, 30)
TODAY = date(2026, 10, 12)
ADMIN = Actor(None, "test", RoleEnum.ADMINISTRADOR, "web")


class ConformityFixtures:
    """Core-insert source evidence, independent of the commands under test."""

    def source(self, model: type, **values: object) -> int:
        """Insert a source row and return its primary key."""
        result = db.session.execute(model.__table__.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def order(self, process: int | None = None, customer: int | None = None) -> int:
        """Create an order with its required process, optionally naming a customer."""
        process = process or self.source(ProcesoOperacion, proceso="Manufacturing")
        count = db.session.query(ProductionOrder).count()
        return self.source(ProductionOrder, code=f"O-{count}", kind="production",
                           title="Container", process_id=process, customer_id=customer)

    def decision(self, day: date | None = START, status: str = "released",
                 concession: bool = False, order: int | None = None) -> int:
        """Create a valid decided release or draft with deliberately unrelated stamps."""
        if order is None:
            count = db.session.query(ProcesoOperacion).count()
            process = self.source(ProcesoOperacion, proceso=f"Process {count}")
            order = self.order(process)
        values = {} if status == "draft" else {
            "decision_date": day, "decided_by_id": 1, "decided_by_user_id": 1,
            "concession": concession, "decision_note": "Decision",
        }
        if concession:
            values |= {"concession_reason": "Accepted", "concession_approver_id": 1}
        return self.source(ProductRelease, order_id=order, status=status, **values)

    def check(self, release: int, result: str = "conforming") -> int:
        """Verification dates need not belong to the release decision period."""
        return self.source(ReleaseVerification, release_id=release, criterion="Fit",
                           result=result, evidence="Report", person_id=1,
                           date=date(2020, 1, 1))

    def analysis(self, **kwargs: object) -> dict:
        """Read through the existing review permission and period validation."""
        return mr.inputs(db.session, ADMIN, START, END, today=TODAY, **kwargs).get(
            "conformidad", {})

    def nc(self) -> int:
        """The NC's detection date deliberately falls outside the period."""
        return self.source(NoConformidad, descripcion="Scratch",
                           fecha_detectada=date(2020, 1, 1))

    def control(self, order: int, nc: int, day: date = START) -> int:
        """Cite an NC in dated control evidence."""
        return self.source(ProductionControlRecord, order_id=order, kind="inspection",
                           date=day, responsible_id=1, description="Scratch",
                           result="nonconforming", evidence="Report", nonconformity_id=nc)


class ConformityRules(ConformityFixtures, ServiceBase):
    def test_inclusive_decision_period_excludes_drafts_and_outside_decisions(self) -> None:
        for day, status in ((START, "released"), (END, "released"),
                            (date(2026, 3, 31), "released"),
                            (date(2026, 7, 1), "released"), (None, "draft")):
            self.decision(day, status)
        before = hashes()
        found = self.analysis()
        self.assertEqual(2, found.get("totales", {}).get("liberadas"))
        self.assertEqual(before, hashes())
        # Like providers, dated events use the full period, not the state cut-off.
        early = mr.inputs(db.session, ADMIN, START, END, today=START)
        self.assertEqual(START.isoformat(), early["fecha_corte"])
        self.assertEqual(found, early["conformidad"])

    def test_release_concession_and_rejection_are_disjoint_counts(self) -> None:
        for status, concession in (("released", False), ("released", True),
                                   ("rejected", False)):
            self.decision(START, status, concession)
        counts = self.analysis().get("totales", {})
        self.assertEqual([1, 1, 1], [counts.get(key) for key in
                         ("liberadas", "con_concesion", "rechazadas")])

    def test_verifications_follow_decided_releases_and_zero_has_no_percentage(self) -> None:
        first, last = self.decision(), self.decision(END, "rejected")
        for result in ("conforming", "conforming", "nonconforming"):
            self.check(first, result)
        self.check(last)
        self.check(self.decision(None, "draft"), "nonconforming")
        self.check(self.decision(date(2026, 3, 31)), "nonconforming")
        counts = self.analysis().get("totales", {})
        self.assertEqual((4, 75.0, 25.0), tuple(counts.get(key) for key in
                         ("verificaciones", "porcentaje_conformes",
                          "porcentaje_no_conformes")))
        empty = mr.inputs(db.session, ADMIN, date(2025, 1, 1), date(2025, 1, 31),
                          today=TODAY)["conformidad"]["totales"]
        self.assertEqual((0, None, None), (empty["verificaciones"],
                         empty["porcentaje_conformes"], empty["porcentaje_no_conformes"]))

    def test_production_ncs_are_distinct_dated_citations_not_detection_dates(self) -> None:
        order = self.order()
        first, second, third, fourth, outside = [self.nc() for _ in range(5)]
        self.control(order, first)
        self.control(order, first, END)
        self.control(order, outside, date(2026, 3, 31))
        self.control(order, outside, date(2026, 7, 1))
        self.source(ProductionPreservationRecord, order_id=order, aspect="storage",
                    date=END, responsible_id=1, description="Scratch",
                    result="nonconforming", nonconformity_id=second)
        customer = self.source(Cliente, nombre="Owner")
        item = self.source(CustomerProperty, customer_id=customer, identification="Gauge",
                           description="Gauge", received_date=START, responsible_id=1,
                           location="Store", condition="Intact")
        self.source(PropertyIncident, item_id=item, date=START, kind="damage",
                    description="Scratch", owner_notified=True, notification_date=END,
                    notification_means="Email", nonconformity_id=third)
        for state, day, nc in (("completed", END, fourth), ("pending", None, outside),
                               ("cancelled", START, outside),
                               ("completed", date(2026, 7, 1), outside)):
            self.source(PostDeliveryActivity, description="Visit", kind="warranty",
                        responsible_id=1, due_date=START, status=state, result_date=day,
                        result="Done" if state == "completed" else None,
                        evidence="Report" if state == "completed" else None,
                        reason="Cancelled" if state == "cancelled" else None,
                        nonconformity_id=nc)
        found = self.analysis()
        self.assertEqual(4, found.get("totales", {}).get("no_conformidades"))
        unlinked = next(row for row in found["por_proceso"] if row["id"] is None)
        self.assertEqual(("Sin orden", 2, 0, None), (unlinked["nombre"],
                         unlinked["no_conformidades"], unlinked["verificaciones"],
                         unlinked["porcentaje_conformes"]))
        # Multi-order property and post-delivery citations use order dimensions,
        # not the property owner's or activity's customer. Cross-source repeats
        # must not multiply counts in a process or in the total.
        p2 = self.source(ProcesoOperacion, proceso="Testing")
        o2 = self.order(p2, customer)
        db.session.execute(property_orders.insert(), [
            {"item_id": item, "order_id": order}, {"item_id": item, "order_id": o2}])
        activity = db.session.query(PostDeliveryActivity).filter_by(
            nonconformity_id=fourth).one()
        db.session.execute(post_delivery_orders.insert(), [
            {"activity_id": activity.id, "order_id": order},
            {"activity_id": activity.id, "order_id": o2}])
        db.session.commit()
        self.control(order, third)
        linked = self.analysis()
        self.assertEqual(4, linked["totales"]["no_conformidades"])
        self.assertEqual({"Manufacturing": 4, "Testing": 2}, {
            row["nombre"]: row["no_conformidades"] for row in linked["por_proceso"]})
        self.assertEqual({"Owner": 2, "Sin cliente": 4}, {
            row["nombre"]: row["no_conformidades"] for row in linked["por_cliente"]})

    def test_process_and_order_customer_breakdowns_include_missing_customer(self) -> None:
        p1 = self.source(ProcesoOperacion, proceso="Assembly")
        p2 = self.source(ProcesoOperacion, proceso="Testing")
        customer = self.source(Cliente, nombre="Acme")
        o1, o2 = self.order(p1, customer), self.order(p2)
        r1 = self.decision(order=o1)
        r2 = self.decision(END, concession=True, order=o2)
        self.check(r1)
        self.check(r2, "nonconforming")
        nc = self.nc()
        self.control(o1, nc)
        self.control(o2, nc)
        found = self.analysis()
        self.assertEqual(2, len(found.get("por_proceso", [])))
        processes = {row["nombre"]: row for row in found["por_proceso"]}
        customers = {row["nombre"]: row for row in found["por_cliente"]}
        self.assertEqual({"Assembly", "Testing"}, processes.keys())
        self.assertEqual({"Acme", "Sin cliente"}, customers.keys())
        self.assertEqual(1, found["totales"]["no_conformidades"])
        for row in (*processes.values(), *customers.values()):
            self.assertEqual(1, row["no_conformidades"])
            self.assertEqual(1, row["verificaciones"])
        self.assertEqual((1, 0, 0, 100.0, 0.0), tuple(processes["Assembly"][key]
                         for key in ("liberadas", "con_concesion", "rechazadas",
                                     "porcentaje_conformes", "porcentaje_no_conformes")))
        self.assertEqual((0, 1, 0, 0.0, 100.0), tuple(customers["Sin cliente"][key]
                         for key in ("liberadas", "con_concesion", "rechazadas",
                                     "porcentaje_conformes", "porcentaje_no_conformes")))

    def test_permissions_match_providers_and_reads_do_not_write(self) -> None:
        before = hashes()
        for role in (RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR):
            for channel in ("web", "mcp"):
                who = Actor(None, "reader", role, channel, scopes=frozenset({"read"}))
                inputs = mr.inputs(db.session, who, START, END, today=TODAY)
                self.assertIn("conformidad", inputs)
                self.assertIn("proveedores", inputs)
        for who in (Actor(None, "worker", RoleEnum.OPERATIVO, "web"),
                    Actor(None, "writer", RoleEnum.ADMINISTRADOR, "mcp",
                          scopes=frozenset({"write"}))):
            with self.assertRaises(PermissionDenied):
                mr.inputs(db.session, who, START, END, today=TODAY)
        self.assertEqual(before, hashes())

    def test_review_lists_keep_existing_size_and_short_text_conventions(self) -> None:
        for number in range(11):
            customer = self.source(Cliente, nombre=f"Customer {number:02d}" + "x" * 130)
            process = self.source(ProcesoOperacion, proceso=f"Process {number:02d}")
            self.decision(order=self.order(process, customer))
        found = self.analysis()
        self.assertEqual((11, 11, 11), (found["procesos"], found["clientes"],
                         found["totales"]["liberadas"]))
        for dimension in ("por_proceso", "por_cliente"):
            self.assertEqual(10, len(found[dimension]))
            self.assertEqual(sorted(row["nombre"] for row in found[dimension]),
                             [row["nombre"] for row in found[dimension]])
        self.assertEqual(120, len(found["por_cliente"][0]["nombre"]))
        self.assertTrue(found["por_cliente"][0]["nombre"].endswith("…"))
        from app.routes.management_review_routes import input_sections

        blocks = [block for section in input_sections({"conformidad": found})
                  for block in section["bloques"]]
        for title in ("Por proceso de la orden", "Por cliente de la orden"):
            block = next(block for block in blocks if block["titulo"] == title)
            self.assertEqual("Se muestran los 10 primeros por nombre de 11.", block["nota"])

    def test_holding_freezes_conformity_exactly_like_providers(self) -> None:
        attendee = self.source(Person, nombre="Attendee")
        review = mr.reviews.create(db.session, ADMIN, {
            "fecha": END, "periodo_desde": START, "periodo_hasta": END,
            "asistente_ids": [attendee],
        })
        db.session.commit()
        live = mr.preview(db.session, ADMIN, review, today=TODAY)
        self.assertIn("conformidad", live)
        mr.hold(db.session, ADMIN, review.id, today=TODAY)
        db.session.commit()
        self.decision()
        frozen = mr.preview(db.session, ADMIN, review, today=TODAY)
        self.assertEqual(live, frozen)
        self.assertNotEqual(frozen["conformidad"], self.analysis())
        self.assertEqual(live["proveedores"], frozen["proveedores"])


class ConcurrentDecision(ConformityFixtures, ServiceBase):
    """A release decided between the analysis queries must not break it."""

    def test_release_decided_between_queries_is_left_out_consistently(self) -> None:
        from unittest.mock import patch
        from app.services import conformity_analysis
        counted = self.decision()
        self.check(counted)
        draft = self.decision(status="draft")
        self.check(draft, "nonconforming")
        db.session.commit()
        original, calls = db.session.execute, []

        def decide_after_releases(statement, *args, **kwargs):
            result = original(statement, *args, **kwargs)
            calls.append(statement)
            if len(calls) == 1:
                original(ProductRelease.__table__.update()
                         .where(ProductRelease.id == draft)
                         .values(status="released", decision_date=START, decided_by_id=1,
                                 decided_by_user_id=1, concession=False,
                                 decision_note="Concurrent"))
            return result

        with patch.object(db.session, "execute", side_effect=decide_after_releases):
            found = conformity_analysis.in_period(db.session, START, END)
        self.assertEqual((1, 1, 100.0), (found["totales"]["liberadas"],
                                         found["totales"]["verificaciones"],
                                         found["totales"]["porcentaje_conformes"]))
