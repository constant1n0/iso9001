"""Corrective action screens under a nonconformity (task NC-3 of ``nc-capa-loop``).

Decisions N3-N5 through the web: every role adds and edits actions,
administrators delete them, administrators and auditors verify a done action
(never by its owner) and close the nonconformity once every action proved
effective. The forms keep what was typed when a service refuses it. Every
test runs with the audit flush guard installed, so a web write that skips its
audit row fails.
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from test_delete_forms import _PostForms
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    AccionCorrectiva, AuditLog, EstadoNoConformidad, NoConformidad, Person,
    ResultadoVerificacion, RoleEnum, User,
)
from app.services import audit
from app.services.actor import Actor

PASSWORD = "StrongPassword123!"
PASSWORD_HASH = generate_password_hash(PASSWORD)
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
SYSTEM = Actor(1, "seed", ADMIN, "system")
BASE = "/no_conformidades"
DETECTED = date(2026, 10, 1)
TODAY = date(2026, 10, 30)
DENIED = ("danger", "No tienes permiso para acceder a esta página.")
E = EstadoNoConformidad


def ncs():
    from app.services import nonconformities

    return nonconformities


def actions():
    from app.services import corrective_actions

    return corrective_actions


class ScreensBase(unittest.TestCase):
    """Users of every role, people Ana and Eva (active) and Luis (inactive), one NC."""

    overrides: dict = {}

    def setUp(self) -> None:
        self.app = bootstrap.build_app(**self.overrides)
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(),
                                    email=f"{role.name.lower()}@example.com",
                                    password=PASSWORD_HASH, role=role))
            people = [Person(nombre="Ana Pérez"), Person(nombre="Eva Ruiz"),
                      Person(nombre="Luis Gil", activo=False)]
            db.session.add_all(people)
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
            self.ana, self.eva, self.luis = (p.id for p in people)
            self.remove_guard = audit.install_audit_guard(db.session, audit.AUDITED_MODELS)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()
        self.nc = self.seed_nc()

    def _teardown(self) -> None:
        self.remove_guard()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # -- helpers ------------------------------------------------------------

    def login(self, role: RoleEnum) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True

    def flashes(self) -> list:
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def html(self, url: str) -> str:
        return self.client.get(url).get_data(as_text=True)

    def seed_nc(self, **values) -> int:
        with self.app.app_context():
            nc = ncs().create(db.session, SYSTEM, {"descripcion": "Semilla",
                                                   "fecha_detectada": DETECTED} | values)
            db.session.commit()
            return nc.id

    def seed_action(self, nc_id: int | None = None, **values) -> int:
        """An action owned by Ana unless ``values`` say otherwise."""
        data = {"no_conformidad_id": nc_id or self.nc, "descripcion": "Ajustar el molde",
                "responsable_id": self.ana, "fecha_prevista": date(2026, 10, 20)} | values
        with self.app.app_context():
            action = actions().create(db.session, SYSTEM, data)
            db.session.commit()
            return action.id

    def seed_verified(self, action_id: int, resultado: str = "eficaz") -> None:
        with self.app.app_context():
            actions().verify(db.session, SYSTEM, action_id, resultado=resultado,
                             fecha=date(2026, 10, 15), verificador_id=self.eva,
                             evidencia="Muestreo de 50 piezas")
            db.session.commit()

    def state(self, nc_id: int | None = None) -> EstadoNoConformidad:
        with self.app.app_context():
            return db.session.get(NoConformidad, nc_id or self.nc).estado

    def action(self, action_id: int) -> AccionCorrectiva | None:
        with self.app.app_context():
            found = db.session.get(AccionCorrectiva, action_id)
            if found is not None:
                db.session.expunge(found)
            return found

    def audit_actions(self) -> list[tuple[str, str]]:
        with self.app.app_context():
            return [(row.action, row.entity_type) for row in AuditLog.query.order_by(AuditLog.id)]

    def detail(self, nc_id: int | None = None) -> str:
        return f"{BASE}/{nc_id or self.nc}"

    def action_url(self, action_id: int, verb: str, nc_id: int | None = None) -> str:
        return f"{BASE}/{nc_id or self.nc}/acciones/{action_id}/{verb}"

    def new_url(self, nc_id: int | None = None) -> str:
        return f"{BASE}/{nc_id or self.nc}/acciones/nueva"

    def action_form(self, **values) -> dict:
        return {"descripcion": "Cambiar el proveedor de resina",
                "responsable_id": str(self.ana), "fecha_prevista": "2026-10-20",
                "fecha_realizada": ""} | values

    def verify_form(self, **values) -> dict:
        return {"resultado_verificacion": "eficaz", "fecha_verificacion": "2026-10-15",
                "verificador_id": str(self.eva),
                "evidencia_verificacion": "Muestreo de 50 piezas"} | values


class ActionFormsTestCase(ScreensBase):
    def test_every_role_adds_an_action_and_the_nonconformity_follows(self) -> None:
        for role in RoleEnum:
            with self.subTest(role=role.name):
                nc_id = self.seed_nc()
                self.login(role)
                response = self.client.post(self.new_url(nc_id), data=self.action_form())
                self.assertEqual(302, response.status_code)
                self.assertTrue(response.headers["Location"].endswith(self.detail(nc_id)))
                self.assertIn(("success", "Acción correctiva registrada."), self.flashes())
                self.assertIs(E.accion_planificada, self.state(nc_id))
        self.assertIn(("create", "acciones_correctivas"), self.audit_actions())

    def test_the_owner_picker_offers_active_people_and_asks_for_one(self) -> None:
        self.login(OPERATIVO)
        html = self.html(self.new_url())
        self.assertIn("— Elige una persona —", html)
        self.assertIn(f'<option value="{self.ana}">Ana Pérez</option>', html)
        self.assertIn("Eva Ruiz", html)
        self.assertNotIn("Luis Gil", html)
        for name in ("descripcion", "responsable_id", "fecha_prevista", "fecha_realizada"):
            self.assertIn(f'name="{name}"', html)

    def test_editing_keeps_an_inactive_owner_and_marking_it_done_moves_the_state(self) -> None:
        action_id = self.seed_action(responsable_id=self.eva)
        with self.app.app_context():  # Eva leaves after the action was planned
            db.session.execute(Person.__table__.update().where(Person.id == self.eva)
                               .values(activo=False))
            db.session.commit()
        self.login(AUDITOR)
        html = self.html(self.action_url(action_id, "editar"))
        self.assertIn("Eva Ruiz (desactivada)", html)
        self.assertNotIn("Luis Gil", html)
        response = self.client.post(
            self.action_url(action_id, "editar"),
            data=self.action_form(responsable_id=str(self.eva), fecha_realizada="2026-10-12"))
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(self.detail()))
        self.assertIn(("success", "Acción correctiva actualizada."), self.flashes())
        self.assertEqual(date(2026, 10, 12), self.action(action_id).fecha_realizada)
        self.assertIs(E.en_verificacion, self.state())

    def test_a_service_refusal_shows_the_form_again_with_the_input(self) -> None:
        self.login(OPERATIVO)
        response = self.client.post(self.new_url(), data=self.action_form(
            descripcion="Texto conservado", fecha_realizada="2026-09-01"))
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(actions().DONE_BEFORE_DETECTED, html)
        self.assertIn("Texto conservado", html)
        self.assertIn('value="2026-09-01"', html)
        with self.app.app_context():
            self.assertEqual(0, AccionCorrectiva.query.count())

    def test_editing_a_verified_action_is_refused_and_keeps_the_input(self) -> None:
        """The detail page offers no edit link once verified; the URL still opens the form
        with the stored values, and saving is refused with the service's message."""
        action_id = self.seed_action(fecha_realizada=date(2026, 10, 12))
        self.seed_verified(action_id)
        self.login(ADMIN)
        self.assertNotIn(self.action_url(action_id, "editar"), self.html(self.detail()))
        form = self.client.get(self.action_url(action_id, "editar"))
        self.assertEqual(200, form.status_code)
        self.assertIn("Ajustar el molde", form.get_data(as_text=True))
        before = self.audit_actions()
        response = self.client.post(self.action_url(action_id, "editar"), data=self.action_form(
            descripcion="Texto conservado", fecha_realizada="2026-10-12"))
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(actions().VERIFIED_READ_ONLY, html)
        self.assertIn("Texto conservado", html)
        self.assertEqual("Ajustar el molde", self.action(action_id).descripcion)
        self.assertEqual(before, self.audit_actions())
        self.assertIs(E.en_verificacion, self.state())

    def test_missing_fields_are_marked_without_writing(self) -> None:
        self.login(OPERATIVO)
        response = self.client.post(self.new_url(), data=self.action_form(
            descripcion="", responsable_id="", fecha_prevista=""))
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, response.get_data(as_text=True).count("field--invalid"))
        with self.app.app_context():
            self.assertEqual(0, AccionCorrectiva.query.count())

    def test_an_action_is_only_reachable_under_its_own_nonconformity(self) -> None:
        action_id = self.seed_action(fecha_realizada=date(2026, 10, 12))
        other = self.seed_nc(descripcion="Otra")
        self.login(ADMIN)
        for method, verb in (("get", "editar"), ("post", "editar"), ("get", "verificar"),
                             ("post", "verificar"), ("post", "eliminar")):
            with self.subTest(method=method, verb=verb):
                response = getattr(self.client, method)(
                    self.action_url(action_id, verb, nc_id=other),
                    data=self.action_form() | self.verify_form())
                self.assertEqual(404, response.status_code)
        for url in (self.new_url(999), self.detail(999), self.action_url(999, "editar")):
            with self.subTest(url=url):
                self.assertEqual(404, self.client.get(url).status_code)
        self.assertIsNone(self.action(action_id).resultado_verificacion)

    def test_only_administrators_delete_an_action(self) -> None:
        action_id = self.seed_action()
        for role in (OPERATIVO, AUDITOR):
            with self.subTest(role=role.name):
                self.login(role)
                response = self.client.post(self.action_url(action_id, "eliminar"))
                self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                self.assertIn(DENIED, self.flashes())
                self.assertIsNotNone(self.action(action_id))
        self.login(ADMIN)
        response = self.client.post(self.action_url(action_id, "eliminar"))
        self.assertTrue(response.headers["Location"].endswith(self.detail()))
        self.assertIn(("success", "Acción correctiva eliminada."), self.flashes())
        self.assertIsNone(self.action(action_id))
        self.assertIs(E.abierta, self.state())  # no action left

    def test_a_closed_nonconformity_refuses_action_writes_with_a_message(self) -> None:
        action_id = self.seed_action()
        with self.app.app_context():
            ncs().cancel(db.session, SYSTEM, self.nc, "Duplicada", today=TODAY)
            db.session.commit()
        self.login(ADMIN)
        response = self.client.post(self.action_url(action_id, "eliminar"))
        self.assertTrue(response.headers["Location"].endswith(self.detail()))
        self.assertIn(("danger", actions().NC_NOT_OPEN), self.flashes())
        self.assertIsNotNone(self.action(action_id))
        response = self.client.post(self.new_url(), data=self.action_form(
            descripcion="Otra acción"))
        self.assertEqual(200, response.status_code)
        self.assertIn("Otra acción", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(1, AccionCorrectiva.query.count())


@patch("app.routes.corrective_action_routes.local_today", return_value=TODAY)
class VerifyTestCase(ScreensBase):
    def setUp(self) -> None:
        super().setUp()
        self.done = self.seed_action(fecha_realizada=date(2026, 10, 12))  # owner Ana

    def test_the_verifier_picker_excludes_the_owner_and_defaults_to_today(self, _today) -> None:
        self.login(AUDITOR)
        html = self.html(self.action_url(self.done, "verificar"))
        self.assertIn(f'<option value="{self.eva}">Eva Ruiz</option>', html)
        self.assertNotIn(f'<option value="{self.ana}">', html)
        self.assertNotIn("Luis Gil", html)  # inactive
        self.assertIn("Ana Pérez", html)  # named as the owner, never offered
        self.assertIn(f'value="{TODAY.isoformat()}"', html)
        self.assertIn('<option value="no_eficaz">No eficaz</option>', html)

    def test_the_owner_cannot_be_posted_as_verifier(self, _today) -> None:
        self.login(ADMIN)
        response = self.client.post(self.action_url(self.done, "verificar"),
                                    data=self.verify_form(verificador_id=str(self.ana)))
        self.assertEqual(200, response.status_code)
        self.assertIn("field--invalid", response.get_data(as_text=True))
        self.assertIsNone(self.action(self.done).resultado_verificacion)

    def test_only_administrators_and_auditors_verify(self, _today) -> None:
        self.login(OPERATIVO)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    self.action_url(self.done, "verificar"), data=self.verify_form())
                self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                self.assertIn(DENIED, self.flashes())
        self.assertIsNone(self.action(self.done).resultado_verificacion)
        for role, verdict in ((AUDITOR, "no_eficaz"), (ADMIN, "eficaz")):
            with self.subTest(role=role.name):
                action_id = self.seed_action(fecha_realizada=date(2026, 10, 12))
                self.login(role)
                response = self.client.post(self.action_url(action_id, "verificar"),
                                            data=self.verify_form(resultado_verificacion=verdict))
                self.assertTrue(response.headers["Location"].endswith(self.detail()))
                self.assertIn(("success", "Verificación registrada."), self.flashes())
                found = self.action(action_id)
                self.assertEqual((ResultadoVerificacion[verdict], self.eva),
                                 (found.resultado_verificacion, found.verificador_id))

    def test_a_service_refusal_keeps_the_input(self, _today) -> None:
        self.login(AUDITOR)
        response = self.client.post(self.action_url(self.done, "verificar"), data=self.verify_form(
            fecha_verificacion="2026-10-02", evidencia_verificacion="Evidencia conservada"))
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(actions().VERIFIED_BEFORE_DONE, html)
        self.assertIn("Evidencia conservada", html)
        self.assertIn('value="2026-10-02"', html)
        self.assertIsNone(self.action(self.done).resultado_verificacion)

    def test_an_action_not_done_cannot_be_verified(self, _today) -> None:
        planned = self.seed_action()
        self.login(AUDITOR)
        response = self.client.post(self.action_url(planned, "verificar"),
                                    data=self.verify_form())
        self.assertEqual(200, response.status_code)
        self.assertIn(actions().NOT_DONE, response.get_data(as_text=True))


class WholeLoopTestCase(ScreensBase):
    def post(self, url: str, data: dict | None = None, expect: str | None = None) -> None:
        response = self.client.post(url, data=data or {})
        self.assertEqual(302, response.status_code, response.get_data(as_text=True)[:300])
        self.assertTrue(response.headers["Location"].endswith(expect or self.detail()))

    def test_the_whole_loop_through_the_web(self) -> None:
        self.login(OPERATIVO)
        response = self.client.post(f"{BASE}/nueva", data={
            "descripcion": "Rebabas en la pieza 12", "fecha_detectada": "2026-10-05",
            "origen": "proceso", "gravedad": "mayor", "responsable": "",
            "responsable_id": str(self.ana), "contencion": "Lote retenido",
            "causa_raiz": "Molde gastado", "accion_correctiva": ""})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            nc_id = NoConformidad.query.filter_by(descripcion="Rebabas en la pieza 12").one().id
        self.assertTrue(response.headers["Location"].endswith(self.detail(nc_id)))
        self.assertIs(E.abierta, self.state(nc_id))

        self.post(self.new_url(nc_id), self.action_form(descripcion="Afilar el molde"),
                  self.detail(nc_id))
        self.assertIs(E.accion_planificada, self.state(nc_id))
        with self.app.app_context():
            first = AccionCorrectiva.query.filter_by(descripcion="Afilar el molde").one().id
        self.post(self.action_url(first, "editar", nc_id), self.action_form(
            descripcion="Afilar el molde", fecha_realizada="2026-10-12"), self.detail(nc_id))
        self.assertIs(E.en_verificacion, self.state(nc_id))

        self.login(AUDITOR)
        self.post(self.action_url(first, "verificar", nc_id), self.verify_form(
            resultado_verificacion="no_eficaz", evidencia_verificacion="Siguen las rebabas"),
            self.detail(nc_id))
        self.assertIs(E.accion_planificada, self.state(nc_id))
        page = self.html(self.detail(nc_id))
        self.assertIn("registra una nueva acción correctiva", page)
        self.assertNotIn(f'action="{BASE}/cerrar/{nc_id}"', page)

        self.login(OPERATIVO)
        self.post(self.new_url(nc_id), self.action_form(descripcion="Sustituir el molde"),
                  self.detail(nc_id))
        with self.app.app_context():
            second = AccionCorrectiva.query.filter_by(descripcion="Sustituir el molde").one().id
        self.post(self.action_url(second, "editar", nc_id), self.action_form(
            descripcion="Sustituir el molde", fecha_realizada="2026-10-20"), self.detail(nc_id))
        self.assertIs(E.en_verificacion, self.state(nc_id))

        self.login(AUDITOR)
        self.post(self.action_url(second, "verificar", nc_id), self.verify_form(
            fecha_verificacion="2026-10-25"), self.detail(nc_id))
        self.assertIs(E.en_verificacion, self.state(nc_id))
        page = self.html(self.detail(nc_id))
        self.assertIn(f'action="{BASE}/cerrar/{nc_id}"', page)
        for text in ("Siguen las rebabas", "No eficaz", "Eficaz", "Eva Ruiz", "25/10/2026"):
            self.assertIn(text, page)

        with patch("app.routes.no_conformidad_routes.local_today", return_value=TODAY):
            self.post(f"{BASE}/cerrar/{nc_id}", expect=self.detail(nc_id))
        self.assertIn(("success", "No conformidad cerrada."), self.flashes())
        with self.app.app_context():
            nc = db.session.get(NoConformidad, nc_id)
            self.assertEqual((E.cerrada, TODAY), (nc.estado, nc.fecha_cierre))
        page = self.html(self.detail(nc_id))
        self.assertIn("Cerrada el 30/10/2026", page)
        self.assertNotIn("/acciones/nueva", page)
        self.assertNotIn("/verificar", page)


class CsrfTestCase(ScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def setUp(self) -> None:
        super().setUp()
        page = _PostForms()
        page.feed(self.html("/login"))
        fields = dict(page.forms[0]["fields"], username="administrador", password=PASSWORD)
        self.assertEqual(302, self.client.post("/login", data=fields).status_code)

    def token(self, url: str) -> str:
        return re.search(r'name="csrf_token" type="hidden" value="([^"]+)"',
                         self.html(url)).group(1)

    def test_the_action_and_verification_forms_need_their_token(self) -> None:
        data = self.action_form(fecha_realizada="2026-10-12")
        self.assertEqual(400, self.client.post(self.new_url(), data=data).status_code)
        response = self.client.post(self.new_url(),
                                    data=data | {"csrf_token": self.token(self.new_url())})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            action_id = AccionCorrectiva.query.one().id
        url = self.action_url(action_id, "verificar")
        self.assertEqual(400, self.client.post(url, data=self.verify_form()).status_code)
        response = self.client.post(url, data=self.verify_form() | {"csrf_token": self.token(url)})
        self.assertEqual(302, response.status_code)
        self.assertIsNotNone(self.action(action_id).resultado_verificacion)

    def test_every_post_form_on_the_detail_page_carries_a_token(self) -> None:
        verified = self.seed_action(fecha_realizada=date(2026, 10, 12))
        self.seed_verified(verified)
        page = _PostForms()
        page.feed(self.html(self.detail()))
        actions_ = sorted(form["action"] for form in page.forms)
        self.assertEqual(sorted([f"{BASE}/cancelar/{self.nc}", f"{BASE}/cerrar/{self.nc}",
                                 self.action_url(verified, "eliminar")]), actions_)
        for form in page.forms:
            with self.subTest(action=form["action"]):
                self.assertTrue(form["fields"].get("csrf_token"))
        (close,) = (f for f in page.forms if f["action"].endswith(f"/cerrar/{self.nc}"))
        self.assertEqual(302, self.client.post(close["action"], data=close["fields"]).status_code)
        self.assertIs(E.cerrada, self.state())


if __name__ == "__main__":
    unittest.main()
