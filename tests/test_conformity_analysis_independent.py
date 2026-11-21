"""Independent T1 regressions: S1(a), S2, S3, S4; S5 belongs to T2."""
from datetime import date, timedelta
from unittest.mock import patch
from app.extensions import db
from app.models import (CustomerProperty, PostDeliveryActivity, ProductionPreservationRecord,
                        PropertyIncident, RevisionDireccion, post_delivery_orders)
from app.services import management_reviews as mr
from app.utils import reports
from test_conformity_analysis import ConformityFixtures, START, END, TODAY, ADMIN
from test_nonconformity_service import ServiceBase
from test_communication_independent import hashes
from test_management_review_screens import ReviewScreensBase, AUDITOR, BASE, ROUTES
from mcp_support import McpDbCase, mcp_actor


class IndependentMatrix(ConformityFixtures, ServiceBase):
    def test_s1_s2_s3_s4_hand_computed_matrix(self):
        from app.models import Cliente, ProcesoOperacion
        a = self.source(ProcesoOperacion, proceso="Assembly")
        b = self.source(ProcesoOperacion, proceso="Service")
        c = self.source(Cliente, nombre="Customer")
        oa, ob = self.order(a, c), self.order(b)
        rejected = self.decision(START, "rejected", order=oa)
        released = self.decision(END, order=oa)
        concession = self.decision(END - timedelta(days=15), concession=True, order=ob)
        for release, results in ((rejected, ["nonconforming"]),
                (released, ["conforming"] * 3),
                (concession, ["conforming", "nonconforming", "nonconforming"])):
            for result in results:
                self.check(release, result)
        for day, state in ((START-timedelta(days=1), "released"),
                           (END+timedelta(days=1), "rejected"), (None, "draft")):
            self.check(self.decision(day, state), "nonconforming")
        n1, n2, n3, outside = [self.nc() for _ in range(4)]
        self.control(oa, n1)
        self.control(ob, n1, END)
        self.control(oa, outside, START-timedelta(days=1))
        self.control(ob, outside, END+timedelta(days=1))
        self.source(ProductionPreservationRecord, order_id=oa, aspect="storage", date=END,
                    responsible_id=1, description="Damage", result="nonconforming", nonconformity_id=n1)
        item = self.source(CustomerProperty, customer_id=c, identification="Gauge",
            description="Gauge", received_date=START, responsible_id=1, location="Store", condition="OK")
        self.source(PropertyIncident, item_id=item, date=START, kind="damage", description="Damage",
            owner_notified=True, notification_date=START, notification_means="Email", nonconformity_id=n2)
        activity = self.source(PostDeliveryActivity, description="Visit", kind="warranty",
            responsible_id=1, due_date=START, status="completed", result_date=END,
            result="Done", evidence="Report", nonconformity_id=n3)
        db.session.execute(post_delivery_orders.insert(),
            [{"activity_id": activity, "order_id": order} for order in (oa, ob)])
        db.session.commit()
        keys = ("liberadas", "con_concesion", "rechazadas", "verificaciones",
                "porcentaje_conformes", "porcentaje_no_conformes", "no_conformidades")
        before = hashes()
        found = self.analysis()
        self.assertEqual((1, 1, 1, 7, 57.14, 42.86, 3), tuple(found["totales"][k] for k in keys))
        processes = {r["nombre"]: tuple(r[k] for k in keys) for r in found["por_proceso"]}
        self.assertEqual({"Assembly": (1, 0, 1, 4, 75.0, 25.0, 2),
            "Service": (0, 1, 0, 3, 33.33, 66.67, 2),
            "Sin orden": (0, 0, 0, 0, None, None, 1)}, processes)
        self.assertEqual({"Customer": 2, "Sin cliente": 3},
                         {r["nombre"]: r["no_conformidades"] for r in found["por_cliente"]})
        with patch.object(reports, "render_pdf", return_value=b"%PDF-test") as pdf:
            reports.generar_reporte_pdf(END, today=TODAY)
        monthly = pdf.call_args.kwargs["conformidad"]["totales"]
        self.assertEqual((1, 1, 0, 6, 66.67, 33.33, 2), tuple(monthly[k] for k in keys))
        empty = reports.render_monthly_report_html(date(2025, 1, 1), today=TODAY)
        self.assertIn("<tr><th>Verificaciones conformes (%)</th><td>—</td></tr>", empty)
        self.assertEqual(before, hashes())


class IndependentLegacyWeb(ReviewScreensBase):
    def test_s2_legacy_inputs_never_gain_an_empty_section(self):
        with self.app.app_context():
            snapshot = mr.inputs(db.session, ADMIN, START, END, today=TODAY)
        snapshot.pop("conformidad")
        rid = self.held_with(snapshot)
        self.login(AUDITOR)
        for html in (self.inputs(rid), self.pdf_html(f"{BASE}/{rid}/pdf", ROUTES)):
            self.assertNotIn("Conformidad de productos y servicios", html)
            self.assertIn("Proveedores externos", html)
        with self.app.app_context():
            review = db.session.get(RevisionDireccion, rid)
            self.assertEqual(snapshot, mr.preview(db.session, ADMIN, review, today=TODAY))


class IndependentLegacyMcp(McpDbCase):
    async def test_s2_legacy_preview_is_unchanged_and_read_only(self):
        rid = self.seed("revisiones_direccion")
        snapshot = mr.inputs(db.session, ADMIN, START, END, today=TODAY)
        snapshot.pop("conformidad")
        db.session.execute(RevisionDireccion.__table__.update().where(
            RevisionDireccion.id == rid).values(estado="celebrada", datos_entrada=snapshot))
        db.session.commit()
        before = hashes()
        for role in (ADMIN.role, AUDITOR):
            result = await self.call(mcp_actor(role, scopes=("read",)), "qms_get",
                                    {"module": "revisiones_direccion", "id": rid})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(snapshot, result.structured_content["preview"])
        self.assertEqual(before, hashes())
