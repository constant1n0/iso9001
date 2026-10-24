"""The changes to the QMS on the pages of the records behind them (CP-3, decision CP6).

The management review (each ``cambio_sgc`` action), nonconformity, audit, risk,
opportunity and context issue pages list the changes that name the record as
their origin (title, state and link), only with the read grant of the changes. A
``cambio_sgc`` review action, on its review and in the action register, offers
«Planificar cambio» to the roles that create changes: it opens the new-change
form with the origin and the action preselected. A context issue, risk or
opportunity a change names offers no delete. Every test runs with the audit
flush guard.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_change_screens import BASE, TODAY, ChangeScreensBase

from app.extensions import db
from app.models import RoleEnum
from app.services import audits, nonconformities, risks

EMPTY = "Ningún cambio en el SGC nace de este registro."
PLAN_LINK = re.compile(r'href="(/cambios/nuevo\?[^"]+)"[^>]*>Planificar cambio<')


class OriginPagesBase(ChangeScreensBase):
    routes = ("app.routes.change_routes", "app.routes.context_routes",
              "app.routes.risk_routes", "app.routes.management_review_routes")

    def card(self, url: str) -> str | None:
        """The «Cambios» card of a record's page, or ``None`` when the page has none."""
        match = re.search(r'id="cambios">(.*?)</article>', self.html(url), re.S)
        return match.group(1) if match else None

    def row(self, html: str, action_id: int) -> str:
        match = re.search(rf'<tr id="accion-{action_id}">(.*?)</tr>', html, re.S)
        self.assertIsNotNone(match, f"no row accion-{action_id}")
        return match.group(1)

    def records(self) -> dict[str, int]:
        found = self.origin_records()
        with self.app.app_context():
            found["nonconformity"] = nonconformities.create(db.session, SYSTEM, {
                "descripcion": "Pedido perdido", "fecha_detectada": date(2026, 9, 1)}).id
            found["audit"] = audits.create(db.session, SYSTEM, {
                "area_auditada": "Ventas", "fecha": date(2026, 9, 2), "auditor": "Luis",
                "resultado": "Sin trazabilidad"}).id
            found["opportunity"] = risks.opportunities.create(db.session, SYSTEM, {
                "titulo": "Venta en línea", "descripcion": "Pedidos web", "origen": "externo",
                "responsable_id": self.eva}, today=TODAY).id
            db.session.commit()
        return found


class OriginPagesTestCase(OriginPagesBase):
    def test_each_origin_page_lists_the_changes_it_gives_rise_to(self) -> None:
        found = self.records()
        pages = {  # origin, link column, record, its page, the roles reading that page
            "revision_direccion": ("accion_revision_id", found["action"],
                                   f"/revisiones-direccion/{found['review']}", {ADMIN, AUDITOR}),
            "no_conformidad": ("no_conformidad_id", found["nonconformity"],
                               f"/no_conformidades/{found['nonconformity']}", set(RoleEnum)),
            "auditoria": ("auditoria_id", found["audit"], f"/auditorias/{found['audit']}",
                          {ADMIN, AUDITOR}),
            "riesgo": ("riesgo_id", found["risk"], f"/riesgos/{found['risk']}", set(RoleEnum)),
            "oportunidad": ("oportunidad_id", found["opportunity"],
                            f"/oportunidades/{found['opportunity']}", set(RoleEnum)),
            "cuestion_contexto": ("cuestion_id", found["issue"], f"/contexto/{found['issue']}",
                                  set(RoleEnum)),
        }
        self.login(ADMIN)
        for origin, (column, record, url, _) in pages.items():
            with self.subTest(page=url, cited=False):
                if origin == "revision_direccion":
                    row = self.row(self.html(url), found["action"])
                    self.assertNotIn(f"{BASE}/", row.replace(f"{BASE}/nuevo", ""))
                else:
                    self.assertIn(EMPTY, self.card(url))
        made = {origin: self.change(f"Cambio por {origin}", origen=origin, **{column: record})
                for origin, (column, record, _, _) in pages.items()}
        cancelled = self.cancelled("Cancelado sin origen")
        self.approved("Otro cambio del riesgo", origen="riesgo", riesgo_id=found["risk"])
        for role in RoleEnum:
            self.login(role)
            for origin, (_, _, url, readers) in pages.items():
                if role not in readers:
                    continue
                with self.subTest(role=role.name, page=url):
                    html = self.html(url)
                    if origin == "revision_direccion":
                        listed = self.row(html, found["action"])
                    else:
                        listed = self.card(url)
                    self.assertIn(f'href="{BASE}/{made[origin]}">Cambio por {origin}</a>', listed)
                    self.assertIn("Propuesto", listed)
                    self.assertNotIn(f'href="{BASE}/{cancelled}"', html)
        self.assertIn("Aprobado", self.card(pages["riesgo"][2]))
        self.assertEqual(2, self.card(pages["riesgo"][2]).count(f'href="{BASE}/'))

    def test_without_the_read_grant_of_the_changes_no_list_is_shown(self) -> None:
        found = self.records()
        self.change("Por el riesgo", origen="riesgo", riesgo_id=found["risk"])
        self.change("Por la acción", origen="revision_direccion",
                    accion_revision_id=found["action"])
        self.login(ADMIN)
        with patch("app.routes.change_routes.can", return_value=False):
            for url in (f"/riesgos/{found['risk']}", f"/contexto/{found['issue']}",
                        f"/no_conformidades/{found['nonconformity']}",
                        f"/auditorias/{found['audit']}"):
                with self.subTest(url=url):
                    self.assertIsNone(self.card(url))
            self.assertNotIn("Por la acción",
                             self.html(f"/revisiones-direccion/{found['review']}"))

    def test_a_record_a_change_names_offers_no_delete(self) -> None:
        found = self.records()
        self.login(ADMIN)
        for origin, column, record, page in (
                ("cuestion_contexto", "cuestion_id", found["issue"],
                 f"/contexto/{found['issue']}"),
                ("oportunidad", "oportunidad_id", found["opportunity"],
                 f"/oportunidades/{found['opportunity']}")):
            with self.subTest(page=page):
                delete = f'action="{page}/eliminar"'
                self.assertIn(delete, self.html(page))
                self.change(origen=origin, **{column: record})
                self.assertNotIn(delete, self.html(page))


class PlanChangeLinkTestCase(OriginPagesBase):
    def test_a_change_action_offers_planning_a_change_to_its_creators(self) -> None:
        found = self.records()
        review, action, other = found["review"], found["action"], found["improvement"]
        for role in (ADMIN, AUDITOR):
            self.login(role)
            for url in (f"/revisiones-direccion/{review}", "/revisiones-direccion/acciones"):
                with self.subTest(role=role.name, page=url):
                    html = self.html(url)
                    links = PLAN_LINK.findall(self.row(html, action))
                    self.assertEqual([f"{BASE}/nuevo?origen=revision_direccion"
                                      f"&accion_revision_id={action}"],
                                     [unescape(link) for link in links])
                    self.assertNotIn("Planificar cambio", self.row(html, other))
        # The link opens the form with the origin and the action preselected.
        form = self.html(unescape(PLAN_LINK.search(
            self.html(f"/revisiones-direccion/{review}")).group(1)))
        self.assertIn('<option selected value="revision_direccion">', self.select(form, "origen"))
        self.assertIn(f'<option selected value="accion_revision_id:{action}">',
                      self.select(form, "registro_origen"))
        with patch("app.routes.management_review_routes.can", side_effect=lambda action, resource:
                   resource != "qms_changes" or action == "read"):
            self.assertNotIn("Planificar cambio", self.html(f"/revisiones-direccion/{review}"))

    def test_operativo_reads_no_review_so_sees_no_link(self) -> None:
        self.records()
        self.login(OPERATIVO)
        self.denied("get", "/revisiones-direccion/acciones")
