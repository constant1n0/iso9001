"""People screens (QP-4): list, detail, create, edit and delete under ``/personas``.

The routes are thin adapters over ``app.services.people``. The ``PEOPLE``
grant (decision Q1) lets every role read, administrators and auditors create
and edit, and only administrators delete. A service ``ValidationError`` or
``Conflict`` is flashed on the re-rendered form, which keeps what was typed;
deleting a person that other records cite is refused with a flash.
"""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from register_routes import RegisterRoutesBase
from test_user_routes import PASSWORD, _post_forms, _seed

from app.extensions import db
from app.models import Person, RoleEnum
from app.services import competence, people, roles_responsibilities
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
BASE = "/personas"
SYSTEM = Actor(1, "seed", ADMIN, "system")


class PeopleRoutesBase(RegisterRoutesBase):
    """Seeds through the services, as production code would."""

    def add_role(self, rol: str) -> int:
        return self.seed_with(roles_responsibilities, {"rol": rol})

    def add_person(self, nombre: str = "Ana Pérez", **values) -> int:
        return self.seed_with(people, {"nombre": nombre, **values})

    def add_record(self, person_id: int, **values) -> int:
        data = {"persona_id": person_id, "evidencia": "Certificado",
                "fecha_obtencion": date(2026, 1, 10)}
        return self.seed_with(competence.records, data | values)

    def stored(self, model: type, record_id: int):
        """The row as stored, detached, or ``None``; a person keeps its roles loaded."""
        with self.app.app_context():
            found = db.session.get(model, record_id)
            if found is not None:
                getattr(found, "rol_ids", None)
                db.session.expunge(found)
            return found

    def page(self, url: str, role: RoleEnum = ADMIN) -> str:
        self.login(role)
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, url)
        return response.get_data(as_text=True)



class PeopleListTestCase(PeopleRoutesBase):
    def test_list_shows_name_email_roles_linked_user_and_state(self) -> None:
        calidad = self.add_role("Calidad")
        ana = self.add_person(email="Ana@Example.com", rol_ids=[calidad],
                              user_id=self.ids[OPERATIVO])
        self.add_person("Luis Gil", activo=False)
        html = self.page(f"{BASE}/")
        for text in ("Ana Pérez", "ana@example.com", "Calidad", "operativo", "Luis Gil"):
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertIn(f'href="{BASE}/{ana}"', html)
        self.assertIn('<span class="badge badge--ok">Activo</span>', html)
        self.assertIn('<span class="badge badge--danger">Inactivo</span>', html)

    def test_list_filters_by_name_state_and_role(self) -> None:
        calidad = self.add_role("Calidad")
        compras = self.add_role("Compras")
        self.add_person("Ana Pérez", rol_ids=[calidad])
        self.add_person("Luis Gil", rol_ids=[compras], activo=False)
        self.add_person("Eva Ruiz", rol_ids=[calidad, compras])
        for query, shown in (
            ("nombre=ana", {"Ana Pérez"}),
            ("activo=0", {"Luis Gil"}),
            ("activo=1", {"Ana Pérez", "Eva Ruiz"}),
            (f"rol_id={compras}", {"Luis Gil", "Eva Ruiz"}),
            (f"rol_id={calidad}&activo=1&nombre=eva", {"Eva Ruiz"}),
            ("rol_id=x", {"Ana Pérez", "Luis Gil", "Eva Ruiz"}),
        ):
            with self.subTest(query=query):
                html = self.page(f"{BASE}/?{query}")
                for name in ("Ana Pérez", "Luis Gil", "Eva Ruiz"):
                    self.assertEqual(name in shown, name in html, name)
        html = self.page(f"{BASE}/?rol_id={calidad}&activo=0")
        self.assertIn(f'<option selected value="{calidad}">Calidad</option>', html)
        self.assertIn('<option selected value="0">Inactivas</option>', html)

    def test_the_filter_badge_counts_only_the_filters_applied(self) -> None:
        calidad = self.add_role("Calidad")
        for query, applied in (("nombre=%20%20&rol_id=x&activo=x", 0), ("nombre=ana&rol_id=x", 1),
                               ("activo=0&nombre=%20", 1), (f"rol_id={calidad}", 1),
                               (f"nombre=ana&activo=1&rol_id={calidad}", 3)):
            with self.subTest(query=query):
                html = self.page(f"{BASE}/?{query}")
                badge = re.search(r'<span class="badge badge--warn">(\d+) activos?</span>', html)
                self.assertEqual(applied, int(badge.group(1)) if badge else 0)
                self.assertEqual(applied > 0, '<details class="filters" open>' in html)

    def test_an_unknown_state_value_lists_everyone(self) -> None:
        self.add_person("Ana Pérez")
        self.add_person("Luis Gil", activo=False)
        html = self.page(f"{BASE}/?activo=x")
        self.assertIn("Ana Pérez", html)
        self.assertIn("Luis Gil", html)
        self.assertIn('<option selected value="">Todas</option>', html)

    def test_operativo_sees_a_link_without_the_account_name(self) -> None:
        self.add_person(user_id=self.ids[ADMIN])
        html = self.page(f"{BASE}/", OPERATIVO)
        self.assertIn("Ana Pérez", html)
        self.assertNotIn("administrador", html)  # user accounts follow the USERS grant
        self.assertIn("Vinculado", html)


class PeopleAccessTestCase(PeopleRoutesBase):
    """Who may do what is pinned per endpoint in test_access_characterization, and
    the list controls and navigation per role in test_ui_permissions."""

    def test_the_detail_page_controls_follow_the_role(self) -> None:
        ana = self.add_person()
        for role, write, delete in ((ADMIN, True, True), (AUDITOR, True, False),
                                    (OPERATIVO, False, False)):
            with self.subTest(role=role.name):
                html = self.page(f"{BASE}/{ana}", role)
                self.assertIn("Sin competencias acreditadas", html)
                self.assertEqual(write, f'href="{BASE}/{ana}/editar"' in html)
                self.assertEqual(delete, f'action="{BASE}/{ana}/eliminar"' in html)

    def test_an_unknown_person_is_not_found(self) -> None:
        self.login(ADMIN)
        for method, url in (("GET", f"{BASE}/999"), ("GET", f"{BASE}/999/editar"),
                            ("POST", f"{BASE}/999/editar"), ("POST", f"{BASE}/999/eliminar")):
            with self.subTest(method=method, url=url):
                response = self.client.open(url, method=method, data={"nombre": "X"})
                self.assertEqual(404, response.status_code)


class PeopleWriteTestCase(PeopleRoutesBase):
    def form(self, **values) -> dict:
        return {"nombre": "Eva Ruiz", "email": "", "user_id": "", "activo": "y",
                "notas": ""} | values

    def test_the_form_offers_roles_users_and_an_active_default(self) -> None:
        calidad = self.add_role("Calidad")
        html = self.page(f"{BASE}/nueva")
        self.assertRegex(html, r'<select[^>]*multiple[^>]*name="rol_ids"')
        self.assertIn(f'<option value="{calidad}">Calidad</option>', html)
        self.assertRegex(html, r'<select[^>]*name="user_id"[^>]*><option selected value="">')
        self.assertIn(f'<option value="{self.ids[AUDITOR]}">auditor</option>', html)
        self.assertRegex(html, r'<input checked[^>]*name="activo"')

    def test_auditor_creates_a_person_with_roles_and_a_user_link(self) -> None:
        calidad = self.add_role("Calidad")
        compras = self.add_role("Compras")
        self.login(AUDITOR)
        response = self.client.post(f"{BASE}/nueva", data=self.form(
            email="Eva@Example.com", rol_ids=[str(calidad), str(compras)],
            user_id=str(self.ids[OPERATIVO]), notas="Turno de mañana"))
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            created = Person.query.one()
            self.assertEqual(f"{BASE}/{created.id}", response.headers["Location"])
            self.assertEqual(
                ("Eva Ruiz", "eva@example.com", self.ids[OPERATIVO], True, "Turno de mañana",
                 [calidad, compras]),
                (created.nombre, created.email, created.user_id, created.activo,
                 created.notas, created.rol_ids))
        self.assertEqual(["create"] * 3, self.actions())  # two roles, then the person
        self.assertIn(("success", "Persona creada exitosamente"), self.flashes())

    def test_the_user_select_skips_users_linked_to_another_person(self) -> None:
        ana = self.add_person(user_id=self.ids[OPERATIVO])
        luis = self.add_person("Luis Gil")
        operativo = f'value="{self.ids[OPERATIVO]}">operativo</option>'
        self.assertNotIn(operativo, self.page(f"{BASE}/nueva"))
        self.assertNotIn(operativo, self.page(f"{BASE}/{luis}/editar"))
        self.assertIn(f"<option selected {operativo}", self.page(f"{BASE}/{ana}/editar"))

    def test_edit_replaces_roles_unlinks_the_user_and_deactivates(self) -> None:
        calidad = self.add_role("Calidad")
        compras = self.add_role("Compras")
        ana = self.add_person(rol_ids=[calidad], user_id=self.ids[OPERATIVO])
        html = self.page(f"{BASE}/{ana}/editar")
        self.assertIn(f'<option selected value="{calidad}">Calidad</option>', html)
        response = self.client.post(f"{BASE}/{ana}/editar", data={
            "nombre": "Ana P. Gómez", "email": "", "rol_ids": [str(compras)], "user_id": "",
            "notas": ""})  # an unchecked box is not sent
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/{ana}", response.headers["Location"])
        found = self.stored(Person, ana)
        self.assertEqual(("Ana P. Gómez", [compras], None, False),
                         (found.nombre, found.rol_ids, found.user_id, found.activo))
        self.assertEqual(["create", "create", "create", "update"], self.actions())

    def test_a_service_conflict_is_shown_on_the_form_with_the_input_kept(self) -> None:
        calidad = self.add_role("Calidad")
        self.add_person(email="ana@example.com")
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/nueva", data=self.form(
            email="ANA@example.com", rol_ids=[str(calidad)], notas="Notas conservadas"))
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(people.EMAIL_TAKEN, html)
        self.assertIn('value="Eva Ruiz"', html)
        self.assertIn("Notas conservadas", html)
        self.assertIn(f'<option selected value="{calidad}">Calidad</option>', html)
        self.assertEqual(1, self.count(Person))

    def test_a_service_validation_error_on_edit_keeps_the_input(self) -> None:
        ana = self.add_person()
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/{ana}/editar", data=self.form(
            nombre="Nombre conservado", email="no-es-un-correo"))
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Introduce una dirección de correo electrónico válida.", html)
        self.assertIn('value="Nombre conservado"', html)
        self.assertEqual("Ana Pérez", self.stored(Person, ana).nombre)

    def test_form_errors_keep_the_input_and_write_nothing(self) -> None:
        self.login(ADMIN)
        for data in (self.form(nombre=""), self.form(rol_ids=["999"]),
                     self.form(user_id="999")):
            with self.subTest(data=data):
                response = self.client.post(f"{BASE}/nueva", data=data | {"notas": "Se queda"})
                self.assertEqual(200, response.status_code)
                self.assertIn("field--invalid", response.get_data(as_text=True))
                self.assertIn("Se queda", response.get_data(as_text=True))
        self.assertEqual(0, self.count(Person))

    def test_delete_removes_an_unreferenced_person(self) -> None:
        ana = self.add_person()
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/{ana}/eliminar")
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/", response.headers["Location"])
        self.assertIsNone(self.stored(Person, ana))
        self.assertIn(("success", "Persona eliminada exitosamente"), self.flashes())

    def test_deleting_a_referenced_person_is_refused_with_a_flash(self) -> None:
        ana = self.add_person()
        self.add_record(ana)
        self.login(ADMIN)
        response = self.client.post(f"{BASE}/{ana}/eliminar")
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"{BASE}/{ana}", response.headers["Location"])
        self.assertIn(("danger", people.STILL_REFERENCED), self.flashes())
        self.assertIsNotNone(self.stored(Person, ana))


class PersonDetailTestCase(PeopleRoutesBase):
    def setUp(self) -> None:
        super().setUp()
        calidad = self.add_role("Calidad")
        self.ana = self.add_person(email="ana@example.com", rol_ids=[calidad],
                                   notas="Responsable de turno")
        evaluator = self.add_person("Luis Gil")
        requirement = self.seed_with(competence.requirements, {
            "rol_id": calidad, "tipo": "formacion", "descripcion": "Auditor interno"})
        self.record = self.add_record(
            self.ana, requisito_id=requirement, evidencia="Certificado AI-7",
            fecha_caducidad=date(2027, 1, 10), evaluacion_eficacia="eficaz",
            fecha_evaluacion=date(2026, 3, 1), evaluador_id=evaluator)

    def test_the_page_shows_data_roles_and_competence_records(self) -> None:
        html = self.page(f"{BASE}/{self.ana}", OPERATIVO)
        for text in ("Ana Pérez", "ana@example.com", "Responsable de turno", "Calidad",
                     "Calidad · Auditor interno", "Certificado AI-7", "10/01/2026",
                     "10/01/2027", '<span class="badge badge--ok">Eficaz</span>'):
            with self.subTest(text=text):
                self.assertIn(text, html)

    def test_record_actions_follow_the_competence_grant(self) -> None:
        new = f'href="/competencias/personas/{self.ana}/nueva"'
        edit = f'href="/competencias/{self.record}/editar"'
        delete = f'action="/competencias/{self.record}/eliminar"'
        for role, write, remove in ((ADMIN, True, True), (AUDITOR, True, False),
                                    (OPERATIVO, False, False)):
            with self.subTest(role=role.name):
                html = self.page(f"{BASE}/{self.ana}", role)
                self.assertEqual((write, write, remove),
                                 (new in html, edit in html, delete in html))


class CsrfRoutesBase(unittest.TestCase):
    """CSRF on: an administrator signs in through the real login form."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(WTF_CSRF_ENABLED=True)
        _seed(self.app)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()
        (login,) = _post_forms(self.client.get("/login").get_data(as_text=True))
        fields = dict(login["fields"], username="administrador", password=PASSWORD)
        self.assertEqual(302, self.client.post("/login", data=fields).status_code)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()


class PeopleCsrfTestCase(CsrfRoutesBase):
    def test_the_create_form_needs_its_token(self) -> None:
        data = {"nombre": "Eva Ruiz", "activo": "y"}
        self.assertEqual(400, self.client.post(f"{BASE}/nueva", data=data).status_code)
        html = self.client.get(f"{BASE}/nueva").get_data(as_text=True)
        token = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html).group(1)
        response = self.client.post(f"{BASE}/nueva", data=data | {"csrf_token": token})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertEqual(["Eva Ruiz"], [p.nombre for p in Person.query.all()])


if __name__ == "__main__":
    unittest.main()
