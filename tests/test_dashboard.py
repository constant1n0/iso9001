"""Management review action register and dashboard cards (MR-2 and RO-3).

Kept apart from ``test_management_review_screens`` and ``test_risk_screens`` so
the register and the cards can ship in their own commits. The register
(``/revisiones-direccion/acciones``) lists every review's actions, filters them
by state, owner, type and overdue, links each to its review and flags the
overdue ones. Its dashboard card lists the overdue actions to administrators and
auditors only, and empties once they are followed up. Other roles are refused.

The risks and opportunities card (clause 6.1, ``risks-opportunities``) counts
what ``risks.attention`` flags, each linked to the list filtered to the same
records, for every role.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO
from test_management_review_screens import BASE, OVERDUE, ReviewScreensBase
from test_risk_screens import OPPORTUNITIES, RISKS, RiskScreensBase

from app.models import RoleEnum

CARD_ITEM = re.compile(r'<a href="([^"]+)">([^<]+)</a></span>\s*<span class="list__secondary">'
                       r'<span class="badge badge--danger">(\d+)</span>')


class ActionRegisterTestCase(ReviewScreensBase):
    def test_the_register_filters_by_state_owner_type_and_overdue(self) -> None:
        rid = self.review([self.ana])
        late = self.action(rid, fecha_limite=date(2026, 9, 30))
        going = self.action(rid, tipo="cambio_sgc", responsable_id=self.luis, estado="en_curso")
        done = self.action(rid, tipo="recursos", estado="completada",
                           fecha_completada=date(2026, 8, 1), fecha_limite=date(2026, 9, 1))
        older = self.review(fecha=date(2025, 11, 5), periodo_desde=date(2025, 1, 1),
                            periodo_hasta=date(2025, 10, 31))
        cancelled = self.action(older, estado="cancelada")
        self.login(AUDITOR)
        url = f"{BASE}/acciones"
        cases = {"": {late, going, done, cancelled}, "?estado=en_curso": {going},
                 f"?responsable_id={self.luis}": {going}, "?tipo=recursos": {done},
                 "?vencida=1": {late}, "?vencida=0": {going, done, cancelled},
                 "?estado=pendiente&vencida=1": {late},
                 "?estado=nada&tipo=nada&vencida=quizas": {late, going, done, cancelled},
                 "?responsable_id=99999": {late, going, done, cancelled}}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, self.listed(f"{url}{query}", "accion"))
        html = self.html(url)
        for text in (f'href="{BASE}/{rid}"', OVERDUE, "Eva Ruiz", "Oportunidad de mejora"):
            self.assertIn(text, self.row(html, "accion", late))
        self.assertIn(f'href="{BASE}/{older}"', self.row(html, "accion", cancelled))
        self.assertNotIn(OVERDUE, self.row(html, "accion", going))
        self.assertIn(f'<option selected value="{self.luis}">Luis Gil</option>',
                      self.html(f"{url}?responsable_id={self.luis}"))
        self.assertIn(f'href="{url}"', self.html(f"{BASE}/"))
        self.login(OPERATIVO)
        self.denied("get", url)


class DashboardCardTestCase(ReviewScreensBase):
    def card(self) -> str | None:
        match = re.search(r'<article class="card" id="acciones-revision">(.*?)</article>',
                          self.html("/dashboard/"), re.S)
        return match.group(1) if match else None

    def test_the_card_lists_overdue_actions_to_administrators_and_auditors(self) -> None:
        rid = self.review([self.ana])
        late = self.action(rid, descripcion="Revisar el plan de formación",
                           responsable_id=self.luis, fecha_limite=date(2026, 9, 30))
        self.action(rid, descripcion="Contratar un técnico", fecha_limite=date(2026, 10, 31))
        self.action(rid, descripcion="Cerrar el expediente", estado="completada",
                    fecha_completada=date(2026, 8, 1), fecha_limite=date(2026, 9, 1))
        self.advance(rid, "hold", "close")
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.login(role)
                card = self.card()
                for text in (f'href="{BASE}/{rid}">Revisar el plan de formación</a>', "Luis Gil",
                             "1 vencida", f'href="{BASE}/acciones?vencida=1"'):
                    self.assertIn(text, card)
                self.assertNotIn("Contratar un técnico", card)
                self.assertNotIn("Cerrar el expediente", card)

        # Following the late action up on the closed review empties the card.
        self.post(f"{BASE}/{rid}/acciones/{late}/editar", {
            "estado": "completada", "fecha_completada": "2026-10-11",
            "resultado": "Plan revisado"}, expect=f"{BASE}/{rid}")
        self.assertIn("Ninguna acción de la revisión por la dirección está vencida.",
                      self.card())
        self.login(OPERATIVO)
        self.assertIsNone(self.card())


class RiskCardTestCase(RiskScreensBase):
    def card(self) -> str:
        match = re.search(r'<article class="card" id="riesgos-oportunidades">(.*?)</article>',
                          self.html("/dashboard/"), re.S)
        self.assertIsNotNone(match, "the dashboard card is missing")
        return match.group(1)

    def test_the_card_links_what_needs_attention_to_the_filtered_lists(self) -> None:
        high = self.risk(titulo="Crítico sin acción", probabilidad=5, impacto=5)
        late = self.risk(titulo="Revisión pasada", probabilidad=1, impacto=1,
                         fecha_identificacion=date(2026, 1, 1), fecha_revision=date(2026, 9, 30))
        pending = self.risk(titulo="Sin valorar")
        closed = self.risk(titulo="Cerrado sin valorar")
        self.update("risks", closed, {"estado": "cerrado"})
        waiting = self.opportunity(titulo="Oportunidad sin valorar")
        overdue = self.opportunity(titulo="Oportunidad por revisar", beneficio=3, viabilidad=3,
                                   fecha_identificacion=date(2026, 1, 1),
                                   fecha_revision=date(2026, 10, 16))
        expected = [
            (f"{RISKS}/?alto_sin_accion=1", "Riesgos altos o críticos sin acción abierta", {high}),
            (f"{RISKS}/?revision_vencida=1", "Riesgos con la revisión vencida", {late}),
            (f"{OPPORTUNITIES}/?revision_vencida=1", "Oportunidades con la revisión vencida",
             {overdue}),
            (f"{RISKS}/?sin_valorar=1&abierto=1", "Riesgos sin valorar", {pending}),
            (f"{OPPORTUNITIES}/?sin_valorar=1&abierto=1", "Oportunidades sin valorar", {waiting}),
        ]
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                items = [(unescape(href), label, int(count))
                         for href, label, count in CARD_ITEM.findall(self.card())]
                self.assertEqual([(href, label, len(ids)) for href, label, ids in expected],
                                 items)
        for href, _, ids in expected:
            with self.subTest(link=href):
                self.assertEqual(ids, set(self.rows(href)))

        self.action(riesgo_id=high)
        self.assertNotIn("sin acción abierta", self.card())
        for rid in (late, pending):
            self.update("risks", rid, {"estado": "cerrado"})
        for oid in (waiting, overdue):
            self.update("opportunities", oid, {"estado": "descartada"})
        self.assertIn("Ningún riesgo ni oportunidad requiere atención.", self.card())
