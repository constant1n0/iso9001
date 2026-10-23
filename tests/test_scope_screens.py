"""QMS scope screens (OC-3 of ``organization-context``).

Through the web (ISO 9001 clause 4.3): the scope is drafted (the first draft
from scratch, later ones as a copy of the current version), its applicability
edited row by row (a requirement that does not apply needs a justification,
shown on its row), approved by an administrator and kept in a history; a draft
missing a stored requirement reads it as applying. Every role reads every
version; administrators and auditors write and delete drafts; only
administrators approve. A refused write re-renders the form with the input
kept. Every test runs with the audit flush guard.
"""

from __future__ import annotations

import re

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_context_screens import ROUTES, ContextScreensBase
from test_delete_forms import _PostForms

from app.extensions import db
from app.models import AlcanceSGC, EstadoAlcance, Person, RoleEnum, aplicabilidad_alcance
from app.services import qms_scope

SCOPE = "/alcance"
CODES = [code for code, _ in qms_scope.CATALOGUE]
DESIGN = "Diseño y desarrollo de los productos y servicios"
NO_DESIGN = "Fabricamos según plano del cliente."
SCOPE_FORM = {"declaracion": "Fabricación de envases metálicos para alimentación.",
              "productos_servicios": "Latas y tapas", "centros": "Planta de Burgos",
              "notas": ""}


def applicability(excluded: dict[str, str] | None = None, blank: tuple[str, ...] = ()) -> dict:
    """The rows of the draft form: every requirement applies except ``excluded``.

    ``blank`` names requirements left unchecked without a justification.
    """
    excluded = excluded or {}
    data = {f"justificacion-{code}": excluded.get(code, "") for code in CODES}
    data |= {f"aplica-{code}": "y" for code in CODES if code not in excluded and code not in blank}
    return data


class ScopeScreensBase(ContextScreensBase):
    """``ContextScreensBase`` with the scope routes' date pinned and scope helpers."""

    routes = (*ROUTES, "app.routes.scope_routes")

    def scope_draft(self, data: dict | None = None) -> int:
        with self.app.app_context():
            found = qms_scope.new_draft(db.session, SYSTEM, SCOPE_FORM if data is None else data)
            db.session.commit()
            return found.id

    def entries(self, scope_id: int) -> dict:
        with self.app.app_context():
            return qms_scope.applicability_of(db.session, scope_id)

    def requirement_rows(self, html: str) -> dict[str, list[str]]:
        """Each applicability row's cell texts by requirement code."""
        return {code: [self.text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                for code, row in re.findall(r'<tr id="apartado-([\d.]+)"[^>]*>(.*?)</tr>',
                                            html, re.S)}

    @staticmethod
    def requirement_row(html: str, code: str) -> str:
        match = re.search(rf'<tr id="apartado-{re.escape(code)}"[^>]*>(.*?)</tr>', html, re.S)
        assert match is not None, f"no row {code}"
        return match.group(0)


class ScopeFlowTestCase(ScopeScreensBase):
    def test_the_scope_is_drafted_approved_and_kept_in_the_history(self) -> None:
        self.login(OPERATIVO)
        page = self.html(f"{SCOPE}/")
        self.assertIn("Todavía no hay un alcance aprobado.", page)
        self.assertNotIn(f'href="{SCOPE}/nuevo"', page)
        self.login(AUDITOR)
        self.assertIn(f'href="{SCOPE}/nuevo"', self.html(f"{SCOPE}/"))
        html = self.rerendered(f"{SCOPE}/nuevo", SCOPE_FORM | {"declaracion": ""})
        self.assertIn("field--invalid", self.field_html(html, "declaracion"))
        self.assertIn("Planta de Burgos", html)
        first = self.created_at(f"{SCOPE}/nuevo", SCOPE_FORM, SCOPE)
        self.assertIn(("success", "Borrador del alcance creado (versión 1)."), self.flashes())
        self.assertEqual({code: {"aplica": True, "justificacion": None} for code in CODES},
                         self.entries(first))

        edit = self.html(f"{SCOPE}/{first}/editar")
        self.assertEqual(CODES, list(self.requirement_rows(edit)))
        self.assertIn('name="aplica-8.3"', self.requirement_row(edit, "8.3"))
        self.assertIn("checked", self.requirement_row(edit, "8.3"))
        self.assertIn('name="justificacion-8.3"', self.requirement_row(edit, "8.3"))
        html = self.rerendered(f"{SCOPE}/{first}/editar", SCOPE_FORM | {
            "centros": "Burgos y Miranda"} | applicability(blank=("8.3",)))
        row = self.requirement_row(html, "8.3")
        self.assertIn(qms_scope.JUSTIFICATION_REQUIRED.format(code="8.3", title=DESIGN), row)
        self.assertNotIn("checked", row)  # what was typed is kept
        self.assertIn("Burgos y Miranda", html)
        self.assertNotIn("field__error", self.requirement_row(html, "8.2"))
        self.assertTrue(self.entries(first)["8.3"]["aplica"])  # nothing written
        self.post(f"{SCOPE}/{first}/editar", SCOPE_FORM | {"centros": "Burgos y Miranda"}
                  | applicability({"8.3": NO_DESIGN}), expect=f"{SCOPE}/{first}")
        self.assertIn(("success", "Borrador del alcance actualizado."), self.flashes())
        self.assertEqual({"aplica": False, "justificacion": NO_DESIGN}, self.entries(first)["8.3"])

        draft = self.html(f"{SCOPE}/{first}")
        self.assertIn(f'href="{SCOPE}/{first}/editar"', draft)
        self.assertIn(f'action="{SCOPE}/{first}/eliminar"', draft)
        self.assertNotIn(f'action="{SCOPE}/{first}/aprobar"', draft)
        self.denied("post", f"{SCOPE}/{first}/aprobar")

        self.login(ADMIN)
        self.assertIn(f'action="{SCOPE}/{first}/aprobar"', self.html(f"{SCOPE}/{first}"))
        self.post(f"{SCOPE}/{first}/aprobar", expect=f"{SCOPE}/")
        self.assertIn(("success", "Versión 1 del alcance aprobada; está vigente desde hoy."),
                      self.flashes())
        current = self.html(f"{SCOPE}/")
        version = self.text(self.block(current, "version"))
        for marker in ("Versión 1", "Vigente", "23/10/2026", "Ana Pérez"):
            self.assertIn(marker, version)
        for marker in (SCOPE_FORM["declaracion"], "Latas y tapas", "Burgos y Miranda"):
            self.assertIn(marker, current)
        rows = self.requirement_rows(current)
        self.assertEqual(CODES, list(rows))
        self.assertEqual(["8.3", DESIGN, "No aplica", NO_DESIGN], rows["8.3"])
        self.assertEqual(["8.2", "Requisitos para los productos y servicios", "Aplica", "—"],
                         rows["8.2"])

        # A new draft copies the current version; approving it makes version 1 obsolete.
        self.login(AUDITOR)
        self.assertIn("Burgos y Miranda", self.html(f"{SCOPE}/nuevo"))  # prefilled
        second = self.created_at(f"{SCOPE}/nuevo", SCOPE_FORM | {"centros": "Burgos"}, SCOPE)
        self.assertEqual({"aplica": False, "justificacion": NO_DESIGN}, self.entries(second)["8.3"])
        self.assertIn(f'href="{SCOPE}/{second}"', self.html(f"{SCOPE}/"))  # the draft is linked
        self.login(ADMIN)
        self.post(f"{SCOPE}/{second}/aprobar", expect=f"{SCOPE}/")
        self.assertIn("Versión 2", self.text(self.block(self.html(f"{SCOPE}/"), "version")))
        history = self.rows(f"{SCOPE}/historial")
        self.assertEqual([second, first], list(history))
        self.assertEqual(["Versión 2", "Vigente", "23/10/2026", "Ana Pérez", "—"],
                         history[second][:5])
        self.assertEqual(["Versión 1", "Obsoleto", "23/10/2026", "Ana Pérez", "23/10/2026"],
                         history[first][:5])
        old = self.html(f"{SCOPE}/{first}")
        self.assertIn("Obsoleto", self.text(self.block(old, "version")))
        for control in (f"{SCOPE}/{first}/editar", f"{SCOPE}/{first}/eliminar",
                        f"{SCOPE}/{first}/aprobar"):
            self.assertNotIn(control, old)
        self.post(f"{SCOPE}/{first}/editar", SCOPE_FORM, expect=f"{SCOPE}/{first}")
        self.assertIn(("danger", qms_scope.IMMUTABLE), self.flashes())
        self.assertEqual("Burgos y Miranda", self.load(AlcanceSGC, first).centros)

    def test_a_draft_is_deleted_and_a_second_one_is_not_started(self) -> None:
        draft = self.scope_draft()
        self.login(AUDITOR)
        page = self.html(f"{SCOPE}/")
        self.assertIn("Todavía no hay un alcance aprobado.", page)
        self.assertIn(f'href="{SCOPE}/{draft}"', page)
        self.assertNotIn(f'href="{SCOPE}/nuevo"', page)
        self.assertEqual(302, self.client.get(f"{SCOPE}/nuevo").status_code)
        self.assertIn(("warning", qms_scope.ONE_DRAFT), self.flashes())
        self.post(f"{SCOPE}/nuevo", SCOPE_FORM, expect=f"{SCOPE}/{draft}")
        self.assertEqual(1, self.count(AlcanceSGC))
        self.post(f"{SCOPE}/{draft}/eliminar", expect=f"{SCOPE}/")
        self.assertIn(("success", "Borrador del alcance eliminado."), self.flashes())
        self.assertEqual(0, self.count(AlcanceSGC))
        self.login(OPERATIVO)
        for method, url in (("get", f"{SCOPE}/nuevo"), ("post", f"{SCOPE}/1/eliminar"),
                            ("post", f"{SCOPE}/1/editar")):
            with self.subTest(url=url):
                self.denied(method, url)

    def test_a_draft_missing_a_stored_requirement_reads_and_edits_it(self) -> None:
        """OC-2 review: a code absent from a draft's rows reads as applying."""
        draft = self.scope_draft()
        table = aplicabilidad_alcance
        with self.app.app_context():
            db.session.execute(table.delete().where(table.c.alcance_id == draft,
                                                    table.c.apartado == "8.3"))
            db.session.commit()
        self.login(AUDITOR)
        self.assertIn("8.3", self.requirement_rows(self.html(f"{SCOPE}/{draft}")))
        self.assertIn("checked", self.requirement_row(self.html(f"{SCOPE}/{draft}/editar"), "8.3"))
        self.post(f"{SCOPE}/{draft}/editar", SCOPE_FORM | applicability({"8.3": NO_DESIGN}),
                  expect=f"{SCOPE}/{draft}")
        self.assertEqual({"aplica": False, "justificacion": NO_DESIGN}, self.entries(draft)["8.3"])
        self.assertEqual(EstadoAlcance.borrador, self.load(AlcanceSGC, draft).estado)


class AccessTestCase(ScopeScreensBase):
    def test_every_role_reads_writers_write_and_only_administrators_approve(self) -> None:
        draft = self.scope_draft()
        for role in RoleEnum:
            self.login(role)
            for url in (f"{SCOPE}/", f"{SCOPE}/historial", f"{SCOPE}/{draft}"):
                with self.subTest(role=role.name, url=url):
                    self.html(url)
        self.login(OPERATIVO)
        for method, url in (("get", f"{SCOPE}/nuevo"), ("get", f"{SCOPE}/{draft}/editar"),
                            ("post", f"{SCOPE}/{draft}/eliminar"),
                            ("post", f"{SCOPE}/{draft}/aprobar")):
            with self.subTest(denied=url):
                self.denied(method, url)
        self.login(AUDITOR)
        self.denied("post", f"{SCOPE}/{draft}/aprobar")
        self.assertEqual(EstadoAlcance.borrador, self.load(AlcanceSGC, draft).estado)

    def test_an_administrator_without_a_person_is_told_to_link_one(self) -> None:
        draft = self.scope_draft()
        with self.app.app_context():  # core update: the person is not under test
            db.session.execute(Person.__table__.update().where(Person.id == self.ana)
                               .values(user_id=None))
            db.session.commit()
        self.login(ADMIN)
        self.post(f"{SCOPE}/{draft}/aprobar", expect=f"{SCOPE}/{draft}")
        self.assertIn(("danger", qms_scope.APPROVER_REQUIRED), self.flashes())
        self.assertEqual(EstadoAlcance.borrador, self.load(AlcanceSGC, draft).estado)


class CsrfTestCase(ScopeScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        draft = self.scope_draft()
        self.login(ADMIN)
        forms = _PostForms()
        forms.feed(self.html(f"{SCOPE}/{draft}"))
        found = {form["action"]: form for form in forms.forms}
        self.assertEqual({f"{SCOPE}/{draft}/eliminar", f"{SCOPE}/{draft}/aprobar"}, set(found))
        for action, form in found.items():
            with self.subTest(action=action):
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
        for url in (f"{SCOPE}/{draft}/editar",):
            with self.subTest(url=url):
                self.assertIn('name="csrf_token"', self.html(url))
                self.assertEqual(400, self.client.post(url, data=SCOPE_FORM).status_code)
        self.assertEqual(SCOPE_FORM["centros"], self.load(AlcanceSGC, draft).centros)
        action = f"{SCOPE}/{draft}/aprobar"
        self.post(action, found[action]["fields"], expect=f"{SCOPE}/")
        self.assertEqual(EstadoAlcance.vigente, self.load(AlcanceSGC, draft).estado)
