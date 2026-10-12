"""Management review action register and dashboard card (task MR-2 of ``management-review``).

Kept apart from ``test_management_review_screens`` so the register and the card
can ship in their own commit. The register (``/revisiones-direccion/acciones``)
lists every review's actions, filters them by state, owner, type and overdue,
links each to its review and flags the overdue ones. The dashboard card lists
the overdue actions to administrators and auditors only, and empties once they
are followed up. Other roles are refused.
"""

from __future__ import annotations

import re
from datetime import date

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO
from test_management_review_screens import BASE, OVERDUE, ReviewScreensBase


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
