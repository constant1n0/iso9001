"""The QMS scope PDF (OC-4 of ``organization-context``, ISO 9001 clause 4.3).

``/alcance/pdf`` exports the version in force and ``/alcance/<id>/pdf`` any
version, a draft or an obsolete one included. They are checked on the HTML
their view renders: ``render_pdf`` is replaced so the test keeps that HTML, as
in ``test_provider_reports`` (the access characterization renders the real
PDF). The header gives the version, its state, its approval (who and when) and
its obsolescence date; then the statement, the products and services and the
sites; then the applicability table (code, title, whether it applies and the
justification), with the requirements that do not apply listed again at the
end, the list an auditor asks for. Every role reads the PDF from the current
version's page and from each version's page; without a version in force,
``/alcance/pdf`` returns to the scope page with the reason.
"""

from __future__ import annotations

import re
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_scope_screens import CODES, DESIGN, NO_DESIGN, SCOPE, SCOPE_FORM, ScopeScreensBase

from app.extensions import db
from app.models import RoleEnum
from app.services import qms_scope

ROUTES = "app.routes.scope_routes"
ROW = re.compile(r'<tr id="apartado-([\d.]+)">(.*?)</tr>', re.S)
EXCLUDED = re.compile(r'<tr id="no-aplica-([\d.]+)">(.*?)</tr>', re.S)
CELL = re.compile(r"<td>([^<]*)</td>")
TABLE = '<h2 class="grupo" id="aplicabilidad">'
END = '<h2 class="grupo" id="no-aplican">'
FAILED = "Error al generar el PDF del alcance."
NO_PURCHASES = "No compramos productos ni servicios que intervengan en el producto."


class ScopePdfBase(ScopeScreensBase):
    def approved(self, changes: dict[str, str | None] | None = None, **texts: str) -> int:
        """The next version, approved by Ana today.

        It copies the current version (``texts`` change its texts); ``changes`` maps a
        requirement to the reason it does not apply, or to ``None`` when it applies.
        """
        draft = self.scope_draft(SCOPE_FORM | texts)
        if changes:
            with self.app.app_context():
                qms_scope.update_draft(db.session, SYSTEM, draft, {"aplicabilidad": {
                    code: {"aplica": why is None, "justificacion": why}
                    for code, why in changes.items()}})
                db.session.commit()
        self.login(ADMIN)
        self.post(f"{SCOPE}/{draft}/aprobar", expect=f"{SCOPE}/")
        return draft

    def report(self, url: str) -> tuple[dict[str, str], dict[str, list[str]],
                                        dict[str, list[str]], str]:
        """The PDF's header fields, the applicability rows, the closing list and its HTML."""
        html = self.pdf_html(url, ROUTES)
        header, _, tables = html.partition(TABLE)
        table, _, closing = tables.partition(END)

        def rows(pattern: re.Pattern, part: str) -> dict[str, list[str]]:
            return {code: CELL.findall(row) for code, row in pattern.findall(part)}

        return self.pdf_fields(header), rows(ROW, table), rows(EXCLUDED, closing), html


class ScopePdfTestCase(ScopePdfBase):
    def test_the_current_version_with_its_approval_texts_and_applicability(self) -> None:
        self.approved({"8.3": NO_DESIGN, "8.4": NO_PURCHASES})
        self.login(OPERATIVO)

        header, rows, closing, html = self.report(f"{SCOPE}/pdf")

        self.assertEqual({"Versión": "1", "Estado": "Vigente", "Aprobada el": "23/10/2026",
                          "Aprobada por": "Ana Pérez", "Obsoleta desde": "—",
                          "Requisitos que aplican": str(len(CODES) - 2),
                          "Requisitos que no aplican": "2"}, header)
        for text in (SCOPE_FORM["declaracion"], SCOPE_FORM["productos_servicios"],
                     SCOPE_FORM["centros"], "Cláusula 4.3", "Versión 1 · Vigente"):
            self.assertIn(text, html)
        self.assertEqual(CODES, list(rows))
        self.assertEqual(["8.3", DESIGN, "No aplica", NO_DESIGN], rows["8.3"])
        self.assertEqual(["8.2", "Requisitos para los productos y servicios", "Aplica", "—"],
                         rows["8.2"])
        self.assertEqual(["8.3", "8.4"], list(closing))
        self.assertEqual(["8.3", DESIGN, NO_DESIGN], closing["8.3"])
        self.assertNotIn("Todos los requisitos aplican.", html)

    def test_any_version_obsolete_or_draft_and_one_where_everything_applies(self) -> None:
        first = self.approved({"8.3": NO_DESIGN})
        second = self.approved({"8.3": None}, centros="Planta de Miranda")
        self.login(AUDITOR)
        third = self.created_at(f"{SCOPE}/nuevo", SCOPE_FORM | {"centros": "Soria"}, SCOPE)

        header, rows, closing, html = self.report(f"{SCOPE}/{first}/pdf")
        self.assertEqual(("1", "Obsoleto", "23/10/2026", "Ana Pérez", "23/10/2026"),
                         tuple(header[k] for k in ("Versión", "Estado", "Aprobada el",
                                                   "Aprobada por", "Obsoleta desde")))
        self.assertIn("Planta de Burgos", html)
        self.assertEqual(["8.3"], list(closing))

        header, rows, closing, html = self.report(f"{SCOPE}/{second}/pdf")
        self.assertEqual(("2", "Vigente", "—"), (header["Versión"], header["Estado"],
                                                 header["Obsoleta desde"]))
        self.assertEqual("0", header["Requisitos que no aplican"])
        self.assertEqual({}, closing)
        self.assertEqual(["Aplica"], sorted({cells[2] for cells in rows.values()}))
        self.assertIn("Todos los requisitos aplican.", html)
        self.assertIn("Planta de Miranda", html)

        header, _, _, html = self.report(f"{SCOPE}/{third}/pdf")
        self.assertEqual(("3", "Borrador", "—", "—"), tuple(
            header[k] for k in ("Versión", "Estado", "Aprobada el", "Aprobada por")))
        self.assertIn("Soria", html)
        self.assertEqual(404, self.client.get(f"{SCOPE}/999/pdf").status_code)


class ScopePdfAccessTestCase(ScopePdfBase):
    def test_every_role_reads_the_pdf_from_the_current_and_version_pages(self) -> None:
        current = self.approved()
        self.login(AUDITOR)
        draft = self.created_at(f"{SCOPE}/nuevo", SCOPE_FORM, SCOPE)
        for role in RoleEnum:
            self.login(role)
            for page, url, version in ((f"{SCOPE}/", f"{SCOPE}/pdf", 1),
                                       (f"{SCOPE}/{current}", f"{SCOPE}/{current}/pdf", 1),
                                       (f"{SCOPE}/{draft}", f"{SCOPE}/{draft}/pdf", 2)):
                with self.subTest(role=role.name, url=url):
                    self.assertIn(f'href="{url}"', self.html(page))
                    with patch(f"{ROUTES}.render_pdf", return_value=b"%PDF-1.7"):
                        response = self.client.get(url)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("application/pdf", response.mimetype)
                    self.assertIn(f"alcance_sgc_v{version}.pdf",
                                  response.headers["Content-Disposition"])

    def test_without_a_version_in_force_the_pdf_returns_to_the_scope_page(self) -> None:
        draft = self.scope_draft()
        self.login(OPERATIVO)
        self.assertNotIn(f'href="{SCOPE}/pdf"', self.html(f"{SCOPE}/"))
        with patch(f"{ROUTES}.render_pdf") as render:
            response = self.client.get(f"{SCOPE}/pdf")
        render.assert_not_called()
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{SCOPE}/", response.headers["Location"])
        self.assertIn(("warning", "No hay ninguna versión vigente del alcance."), self.flashes())
        self.assertIn(f'href="{SCOPE}/{draft}/pdf"', self.html(f"{SCOPE}/{draft}"))

    def test_a_pdf_that_cannot_be_rendered_returns_to_its_page(self) -> None:
        current = self.approved()
        self.login(AUDITOR)
        for url, back in ((f"{SCOPE}/pdf", f"{SCOPE}/"),
                          (f"{SCOPE}/{current}/pdf", f"{SCOPE}/{current}")):
            with self.subTest(url=url):
                with patch(f"{ROUTES}.render_pdf", side_effect=RuntimeError("weasyprint")), \
                        self.assertLogs(self.app.logger, "ERROR") as logged:
                    response = self.client.get(url)
                self.assertEqual(302, response.status_code)
                self.assertEqual(back, response.headers["Location"])
                self.assertIn(("danger", FAILED), self.flashes())
                self.assertIn("QMS scope PDF", logged.output[0])
