"""The QMS change PDFs (CP-4 of ``change-planning``, decision CP7, ISO 9001 clause 6.3).

``/cambios/pdf`` exports the change register and ``/cambios/<id>/pdf`` one
change's plan. They are checked on the HTML their view renders: ``render_pdf``
is replaced so the test keeps that HTML, as in ``test_scope_reports`` (the
access characterization renders the real PDFs).

- The register's header counts the changes by state and lists the approved
  ones past their planned date; then the changes follow grouped by state, the
  open ones first (proposed, approved, implemented), then the reviewed and the
  cancelled ones, each with its origin and the record behind it, its owner, its
  proposal, planned, approval and implementation dates, its tasks completed of
  the total and, once reviewed, the result.
- The plan gives the change's title, state, origin and record, description,
  the four 6.3 considerations a) to d), the communication, the processes, the
  documents the role may read (an operativo reads only documents in force, as on
  the change's page), the tasks and the approval, implementation, effectiveness
  and cancellation data.

Every role reads both PDFs, from the list and from the change's page; a PDF
that cannot be rendered returns to the page it came from with the reason.
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO
from test_change_screens import BASE, PLAN, TODAY, ChangeScreensBase

from app.models import RoleEnum
from app.services import policy
from app.services.policy import Resource

ROUTES = "app.routes.change_routes"
CELL = re.compile(r"<td>([^<]*)</td>")
GROUP = re.compile(r'<h2 class="grupo" id="estado-(\w+)">([^<]+)</h2>')
STATES = ["propuesto", "aprobado", "implantado", "eficacia_revisada", "cancelado"]
REGISTER_FAILED = "Error al generar el PDF del registro de cambios."
PLAN_FAILED = "Error al generar el PDF del plan del cambio."


class ChangePdfBase(ChangeScreensBase):
    @staticmethod
    def pdf_rows(html: str, kind: str) -> dict[int, list[str]]:
        """The cells of each ``<tr id="kind-<id>">`` row of a PDF, by record id."""
        return {int(found): CELL.findall(row) for found, row in re.findall(
            rf'<tr id="{kind}-(\d+)">(.*?)</tr>', html, re.S)}

    @staticmethod
    def part(html: str, start: str, end: str | None = None) -> str:
        """The HTML from the heading with ``id="start"`` up to the one with ``id="end"``."""
        _, _, rest = html.partition(f'id="{start}"')
        assert rest, f"no part {start}"
        return rest.partition(f'id="{end}"')[0] if end else rest


class ChangeRegisterPdfTestCase(ChangePdfBase):
    def test_the_register_counts_lists_the_late_ones_and_groups_by_state(self) -> None:
        records = self.origin_records()
        proposed = self.change("Firma digital", fecha_propuesta=TODAY - timedelta(days=18))
        late = self.approved("ERP con retraso", origen="riesgo", riesgo_id=records["risk"],
                             fecha_prevista=TODAY - timedelta(days=1))
        self.task(late, descripcion="Formar al equipo", estado="completada",
                  fecha_completada=TODAY)
        on_time = self.approved("ERP al día")
        implemented = self.implemented("Portal de clientes")
        reviewed = self.reviewed("Revisado")
        cancelled = self.cancelled("Cancelado")
        self.login(OPERATIVO)

        html = self.pdf_html(f"{BASE}/pdf", ROUTES)

        self.assertEqual({"Cambios registrados": "6", "Estado: Propuesto": "1",
                          "Estado: Aprobado": "2", "Estado: Implantado": "1",
                          "Estado: Eficacia revisada": "1", "Estado: Cancelado": "1",
                          "Aprobados fuera de plazo": "1"}, self.pdf_fields(html))
        for text in ("Cláusula 6.3", "Registro de cambios en el SGC"):
            self.assertIn(text, html)
        header = self.part(html, "fuera-de-plazo", f"estado-{STATES[0]}")
        self.assertEqual({late: ["ERP con retraso", "Eva Ruiz", "22/10/2026", "23/10/2026"]},
                         self.pdf_rows(header, "fuera"))
        self.assertEqual([(state, f"{label}") for state, label in zip(STATES, (
            "Propuesto (1)", "Aprobado (2)", "Implantado (1)", "Eficacia revisada (1)",
            "Cancelado (1)"))], GROUP.findall(html))
        groups = {state: self.pdf_rows(self.part(html, f"estado-{state}", following), "cambio")
                  for state, following in zip(STATES, [f"estado-{s}" for s in STATES[1:]]
                                              + [None])}
        self.assertEqual({
            "propuesto": [proposed], "aprobado": [on_time, late], "implantado": [implemented],
            "eficacia_revisada": [reviewed], "cancelado": [cancelled],
        }, {state: list(rows) for state, rows in groups.items()})
        self.assertEqual(["ERP con retraso", "Riesgo · Pérdida de pedidos", "Eva Ruiz",
                          "01/10/2026", "22/10/2026 · fuera de plazo", "23/10/2026", "—",
                          "1 de 2", "—"], groups["aprobado"][late])
        self.assertEqual(["Firma digital", "Otro", "Eva Ruiz", "05/10/2026", "—", "—", "—",
                          "0 de 0", "—"], groups["propuesto"][proposed])
        self.assertEqual(["Revisado", "Otro", "Eva Ruiz", "01/10/2026", "31/12/2026",
                          "23/10/2026", "23/10/2026", "1 de 1",
                          "No eficaz · Siguen los pedidos en papel"],
                         groups["eficacia_revisada"][reviewed])
        self.assertEqual("Cancelado el 23/10/2026 · Sin presupuesto",
                         groups["cancelado"][cancelled][-1])

    def test_an_empty_register_says_so_in_every_group(self) -> None:
        self.login(AUDITOR)
        html = self.pdf_html(f"{BASE}/pdf", ROUTES)
        self.assertEqual("0", self.pdf_fields(html)["Cambios registrados"])
        self.assertIn("Ningún cambio aprobado está fuera de plazo.", html)
        self.assertEqual(len(STATES), html.count("Ningún cambio en este estado."))


class ChangePlanPdfTestCase(ChangePdfBase):
    def test_the_plan_carries_the_considerations_lists_tasks_and_stages(self) -> None:
        records = self.origin_records()
        compras = self.process("Compras")
        manual, draft = self.document("PR-01"), self.document("PR-02", in_force=False)
        change = self.approved("ERP", origen="cuestion_contexto", cuestion_id=records["issue"],
                               comunicacion="Reunión con comerciales", procesos=[compras],
                               documentos=[manual, draft])
        late = self.task(change, descripcion="Formar al equipo",
                         fecha_limite=TODAY - timedelta(days=2))
        self.login(OPERATIVO)

        html = self.pdf_html(f"{BASE}/{change}/pdf", ROUTES)

        fields = self.pdf_fields(html)
        self.assertEqual({
            "Título": "ERP", "Estado": "Aprobado", "Origen": "Cuestión del contexto",
            "Registro de origen": "Saber disperso", "Responsable": "Eva Ruiz",
            "Fecha de propuesta": "01/10/2026", "Implantación prevista": "31/12/2026",
            "Aprobado por": "Ana Pérez", "Fecha de aprobación": "23/10/2026",
            "Fecha de implantación": "—", "Resultado de la eficacia": "—", "Revisada por": "—",
            "Fecha de la revisión": "—", "Comentario de la revisión": "—",
        }, fields)
        for text in ("Cláusula 6.3", "Plan del cambio", "Sustituir la hoja de pedidos",
                     "a) Propósito y consecuencias potenciales", PLAN["consecuencias"],
                     "Trazabilidad de los pedidos", "b) Integridad del SGC", PLAN["integridad"],
                     "c) Disponibilidad de recursos", PLAN["recursos"],
                     "d) Responsabilidades y autoridades", PLAN["responsabilidades"],
                     "Comunicación (a quién y cómo)", "Reunión con comerciales"):
            self.assertIn(text, html)
        processes = self.part(html, "procesos", "documentos")
        self.assertIn("Compras", processes)
        documents = self.part(html, "documentos", "tareas")
        self.assertIn("PR-01 · Procedimiento PR-01", documents)
        self.assertNotIn("PR-02", html)  # a draft: OPERATIVO does not read it
        tasks = self.pdf_rows(html, "tarea")
        self.assertEqual(["Formar al equipo", "Luis Gil", "21/10/2026 · vencida", "Pendiente",
                          "—", "—"], tasks[late])
        self.assertEqual(2, len(tasks))
        self.assertNotIn("Cancelado el", html)
        self.login(AUDITOR)
        self.assertIn("PR-02 · Procedimiento PR-02",
                      self.pdf_html(f"{BASE}/{change}/pdf", ROUTES))

    def test_the_plan_of_a_reviewed_and_of_a_cancelled_change(self) -> None:
        reviewed, cancelled = self.reviewed("Revisado"), self.cancelled("Cancelado")
        self.login(AUDITOR)
        fields = self.pdf_fields(self.pdf_html(f"{BASE}/{reviewed}/pdf", ROUTES))
        self.assertEqual(("Eficacia revisada", "Otro", "—", "23/10/2026", "No eficaz",
                          "Ana Pérez", "23/10/2026", "Siguen los pedidos en papel"), tuple(
            fields[label] for label in (
                "Estado", "Origen", "Registro de origen", "Fecha de implantación",
                "Resultado de la eficacia", "Revisada por", "Fecha de la revisión",
                "Comentario de la revisión")))
        html = self.pdf_html(f"{BASE}/{cancelled}/pdf", ROUTES)
        fields = self.pdf_fields(html)
        self.assertEqual(("Cancelado", "—", "23/10/2026", "Sin presupuesto"), tuple(
            fields[label] for label in ("Estado", "Aprobado por", "Cancelado el",
                                        "Motivo de la cancelación")))
        self.assertIn("Sin tareas.", html)
        self.assertIn("No afecta a ningún proceso.", html)
        self.assertEqual(404, self.client.get(f"{BASE}/9999/pdf").status_code)


class ChangePdfAccessTestCase(ChangePdfBase):
    def test_every_role_reads_both_pdfs_from_the_list_and_the_change_page(self) -> None:
        change = self.change()
        for role in RoleEnum:
            self.login(role)
            for page, url, name in ((f"{BASE}/", f"{BASE}/pdf", "cambios_sgc_2026-10-23.pdf"),
                                    (f"{BASE}/{change}", f"{BASE}/{change}/pdf",
                                     f"plan_cambio_{change}.pdf")):
                with self.subTest(role=role.name, url=url):
                    self.assertIn(f'href="{url}"', self.html(page))
                    with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                        response = self.client.get(url)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("application/pdf", response.mimetype)
                    self.assertIn(name, response.headers["Content-Disposition"])

    def test_the_pdfs_need_a_login_and_the_read_grant_of_the_changes(self) -> None:
        change = self.change()
        urls = (f"{BASE}/pdf", f"{BASE}/{change}/pdf")
        for url in urls:
            with self.subTest(url=url, client="anonymous"):
                response = self.client.get(url)
                self.assertEqual(302, response.status_code)
                self.assertIn("/login", response.headers["Location"])
        allowed = policy.can
        self.login(ADMIN)
        with patch("app.services.policy.can", side_effect=lambda actor, action, resource: (
                Resource(resource) is not Resource.QMS_CHANGES
                and allowed(actor, action, resource))), \
                patch(f"{ROUTES}.render_pdf") as render:
            for url in urls:
                with self.subTest(url=url, client="without the read grant"):
                    self.denied("get", url)
        render.assert_not_called()

    def test_a_pdf_that_cannot_be_rendered_returns_to_its_page(self) -> None:
        change = self.change(**PLAN)
        self.login(AUDITOR)
        for url, back, message, logged_text in (
                (f"{BASE}/pdf", f"{BASE}/", REGISTER_FAILED, "QMS changes register PDF"),
                (f"{BASE}/{change}/pdf", f"{BASE}/{change}", PLAN_FAILED,
                 f"plan PDF of QMS change {change}")):
            with self.subTest(url=url):
                with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                        self.assertLogs(self.app.logger, "ERROR") as logged:
                    response = self.client.get(url)
                self.assertEqual(302, response.status_code)
                self.assertEqual(back, response.headers["Location"])
                self.assertIn(("danger", message), self.flashes())
                self.assertIn(logged_text, logged.output[0])
