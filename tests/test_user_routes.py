"""Administrator screens for user accounts (UM-3a): list, create and edit.

The routes are thin adapters over ``app.services.users``: the ``USERS`` grant
decides who reads (administrators and auditors) and who writes
(administrators). Service ``ValidationError`` and ``Conflict`` messages are
shown on the re-rendered form, which keeps what was typed except the
passwords, and nothing is written.
"""

from __future__ import annotations

import unittest
from html.parser import HTMLParser
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import AuditLog, RoleEnum, User
from app.services import users as users_service
from app.services.errors import Conflict

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
PASSWORD = "StrongPassword123!"
PASSWORD_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")  # fast
NEW_PASSWORD = "ClaveNueva2026"
BASE = "/usuarios"
DENIED = ("danger", "No tienes permiso para acceder a esta página.")
NEW_USER = {
    "username": "nueva.persona",
    "email": "Nueva.Persona@Example.com",
    "role": "OPERATIVO",
    "password": NEW_PASSWORD,
    "confirm_password": NEW_PASSWORD,
}


class _PostForms(HTMLParser):
    """Collect POST forms and the inputs a browser would submit."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("method", "get").lower() == "post":
            self._current = {"action": attrs.get("action"), "fields": {}}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and attrs.get("name"):
            self._current["fields"][attrs["name"]] = attrs.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._current = None


def _post_forms(html: str) -> list[dict]:
    page = _PostForms()
    page.feed(html)
    return page.forms


def _seed(app) -> dict:
    """One account per role, every one active; returns their ids by role."""
    with app.app_context():
        db.create_all()
        for role in RoleEnum:
            db.session.add(
                User(
                    username=role.name.lower(),
                    email=f"{role.name.lower()}@example.com",
                    password=PASSWORD_HASH,
                    role=role,
                )
            )
        db.session.commit()
        return {u.role: u.id for u in User.query.all()}


class UserRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.ids = _seed(self.app)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, role: RoleEnum = ADMIN) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True

    def flashes(self) -> list:
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def audit_rows(self) -> list[tuple]:
        with self.app.app_context():
            return [
                (r.action, r.entity_id, r.channel, r.actor_user_id, r.before, r.after)
                for r in AuditLog.query.order_by(AuditLog.id)
            ]

    def accounts(self) -> dict:
        with self.app.app_context():
            return {u.username: (u.email, u.role) for u in User.query.all()}

    def assert_refused(self, response) -> None:
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
        self.assertIn(DENIED, self.flashes())

    def assert_rerendered(self, response, message: str) -> str:
        """The form came back (200) showing ``message``; returns the page."""
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(message, html)
        self.assertNotIn(NEW_PASSWORD, html)  # passwords are never filled back in
        return html

    # -- list ---------------------------------------------------------------

    def test_list_shows_every_account_with_role_and_state(self) -> None:
        with self.app.app_context():
            db.session.get(User, self.ids[OPERATIVO]).active = False
            db.session.commit()
        self.login()
        response = self.client.get(f"{BASE}/")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.assertIn(f"{role.name.lower()}@example.com", html)
                self.assertIn(role.value, html)
                self.assertIn(f'href="{BASE}/{self.ids[role]}/editar"', html)
        self.assertIn(">Activo<", html)
        self.assertIn(">Inactivo<", html)
        self.assertIn(f'href="{BASE}/nuevo"', html)

    def test_auditor_reads_the_list_without_write_controls(self) -> None:
        self.login(AUDITOR)
        response = self.client.get(f"{BASE}/")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("operativo@example.com", html)
        self.assertNotIn(f'href="{BASE}/nuevo"', html)
        self.assertNotIn("/editar", html)

    def test_operativo_cannot_read_the_list(self) -> None:
        self.login(OPERATIVO)
        self.assert_refused(self.client.get(f"{BASE}/"))

    def test_navigation_shows_administration_only_to_readers(self) -> None:
        for role in RoleEnum:
            self.login(role)
            html = self.client.get("/dashboard/").get_data(as_text=True)
            with self.subTest(role=role.name):
                visible = role in (ADMIN, AUDITOR)
                self.assertEqual(visible, 'class="nav__group">Administración<' in html)
                self.assertEqual(visible, f'class="nav__link" href="{BASE}/"' in html)

    # -- refusals -----------------------------------------------------------

    def test_auditor_and_operativo_are_refused_on_the_forms(self) -> None:
        target = self.ids[OPERATIVO]
        edit = {"email": "otra@example.com", "role": "AUDITOR"}
        for role in (AUDITOR, OPERATIVO):
            self.login(role)
            for method, url, data in (
                ("GET", f"{BASE}/nuevo", None),
                ("POST", f"{BASE}/nuevo", NEW_USER),
                ("GET", f"{BASE}/{target}/editar", None),
                ("POST", f"{BASE}/{target}/editar", edit),
            ):
                with self.subTest(role=role.name, method=method, url=url):
                    self.assert_refused(self.client.open(url, method=method, data=data))
        self.assertNotIn("nueva.persona", self.accounts())
        self.assertEqual(
            ("operativo@example.com", OPERATIVO), self.accounts()["operativo"]
        )
        self.assertEqual([], self.audit_rows())

    def test_unknown_user_is_not_found(self) -> None:
        self.login()
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                response = self.client.open(
                    f"{BASE}/999/editar", method=method, data={"email": "x@example.com"}
                )
                self.assertEqual(404, response.status_code)

    # -- create -------------------------------------------------------------

    def test_admin_creates_an_active_user_and_the_write_is_audited(self) -> None:
        self.login()
        response = self.client.get(f"{BASE}/nuevo")
        self.assertEqual(200, response.status_code)
        response = self.client.post(f"{BASE}/nuevo", data=NEW_USER)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(("success", "Usuario creado exitosamente"), self.flashes())
        with self.app.app_context():
            user = User.query.filter_by(username="nueva.persona").one()
            self.assertEqual("nueva.persona@example.com", user.email)
            self.assertIs(OPERATIVO, user.role)
            self.assertTrue(user.active)
            self.assertTrue(check_password_hash(user.password, NEW_PASSWORD))
            user_id = user.id
        ((action, entity_id, channel, actor_id, _, after),) = self.audit_rows()
        self.assertEqual(("create", user_id, "web", self.ids[ADMIN]),
                         (action, entity_id, channel, actor_id))
        self.assertNotIn("password", after)

    def test_create_form_requires_matching_passwords_of_eight_characters(self) -> None:
        self.login()
        cases = {
            "Las contraseñas no coinciden.": {"confirm_password": "OtraClave2026"},
            "8 caracteres": {"password": "corta", "confirm_password": "corta"},
        }
        for message, override in cases.items():
            with self.subTest(message=message):
                response = self.client.post(f"{BASE}/nuevo", data=NEW_USER | override)
                html = self.assert_rerendered(response, message)
                self.assertIn('value="nueva.persona"', html)
        self.assertNotIn("nueva.persona", self.accounts())

    def test_service_refusals_re_render_the_create_form_and_write_nothing(self) -> None:
        self.login()
        cases = {
            users_service.DUPLICATE_USERNAME: {"username": "operativo"},
            users_service.DUPLICATE_EMAIL: {"email": "AUDITOR@example.com"},
            users_service.USERNAME_LENGTH: {"username": "  ab  "},
            "Introduce una dirección de correo electrónico válida.": {
                "email": "sin-arroba"
            },
        }
        for message, override in cases.items():
            data = NEW_USER | override
            with self.subTest(message=message):
                html = self.assert_rerendered(
                    self.client.post(f"{BASE}/nuevo", data=data), message
                )
                self.assertIn(f'value="{data["username"]}"', html)
                self.assertIn(f'value="{data["email"]}"', html)
        self.assertEqual(3, len(self.accounts()))
        self.assertEqual([], self.audit_rows())

    # -- edit ---------------------------------------------------------------

    def test_edit_form_shows_the_current_values_and_a_read_only_username(self) -> None:
        self.login()
        response = self.client.get(f"{BASE}/{self.ids[AUDITOR]}/editar")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("<strong>auditor</strong>", html)
        self.assertNotIn('name="username"', html)
        self.assertIn('value="auditor@example.com"', html)
        self.assertIn('<option selected value="AUDITOR">', html)

    def test_admin_edits_email_and_role_and_the_write_is_audited(self) -> None:
        self.login()
        target = self.ids[OPERATIVO]
        response = self.client.post(
            f"{BASE}/{target}/editar",
            data={"email": " Op@Example.com ", "role": "AUDITOR", "username": "otro"},
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(("success", "Usuario actualizado exitosamente"), self.flashes())
        self.assertEqual(("op@example.com", AUDITOR), self.accounts()["operativo"])
        ((action, entity_id, channel, actor_id, before, after),) = self.audit_rows()
        self.assertEqual(("update", target, "web", self.ids[ADMIN]),
                         (action, entity_id, channel, actor_id))
        self.assertEqual("operativo@example.com", before["email"])
        self.assertEqual("op@example.com", after["email"])

    def test_service_refusals_re_render_the_edit_form_and_write_nothing(self) -> None:
        self.login()
        cases = (
            (self.ids[ADMIN], {"email": "yo@example.com", "role": "AUDITOR"},
             users_service.OWN_ROLE),
            (self.ids[OPERATIVO], {"email": "Auditor@Example.com", "role": "OPERATIVO"},
             users_service.DUPLICATE_EMAIL),
            (self.ids[OPERATIVO], {"email": "sin-arroba", "role": "OPERATIVO"},
             "Introduce una dirección de correo electrónico válida."),
        )
        before = self.accounts()
        for user_id, data, message in cases:
            with self.subTest(message=message):
                html = self.assert_rerendered(
                    self.client.post(f"{BASE}/{user_id}/editar", data=data), message
                )
                self.assertIn(f'value="{data["email"]}"', html)
        self.assertEqual(before, self.accounts())
        self.assertEqual([], self.audit_rows())

    def test_last_administrator_conflict_re_renders_and_rolls_back(self) -> None:
        # Over the web the actor is always another active administrator, so the
        # service raises this only under a concurrent demotion; the adapter
        # must still show it on the form and roll back.
        self.login()
        refusal = Conflict(users_service.LAST_ADMINISTRATOR)
        with (
            patch.object(users_service, "update", side_effect=refusal),
            patch("app.routes.user_routes.db.session.rollback") as rollback,
        ):
            response = self.client.post(
                f"{BASE}/{self.ids[AUDITOR]}/editar",
                data={"email": "auditor@example.com", "role": "OPERATIVO"},
            )
        self.assert_rerendered(response, users_service.LAST_ADMINISTRATOR)
        rollback.assert_called_once()


class UserFormsCsrfTestCase(unittest.TestCase):
    """With CSRF on, the rendered forms carry a token the routes accept."""

    def setUp(self) -> None:
        self.app = bootstrap.build_app(WTF_CSRF_ENABLED=True)
        self.ids = _seed(self.app)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()
        (login,) = _post_forms(self.client.get("/login").get_data(as_text=True))
        fields = dict(login["fields"], username="administrador", password=PASSWORD)
        self.assertEqual(302, self.client.post("/login", data=fields).status_code)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _form(self, url: str) -> dict:
        (form,) = _post_forms(self.client.get(url).get_data(as_text=True))
        self.assertTrue(form["fields"].get("csrf_token"), "hidden csrf_token missing")
        return form["fields"]

    def test_create_and_edit_forms_submit_a_valid_csrf_token(self) -> None:
        fields = self._form(f"{BASE}/nuevo")
        response = self.client.post(f"{BASE}/nuevo", data=fields | NEW_USER)
        self.assertEqual(302, response.status_code)

        target = self.ids[OPERATIVO]
        fields = self._form(f"{BASE}/{target}/editar")
        response = self.client.post(
            f"{BASE}/{target}/editar", data=fields | {"role": "AUDITOR"}
        )
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            self.assertEqual(1, User.query.filter_by(username="nueva.persona").count())
            self.assertIs(AUDITOR, db.session.get(User, target).role)

    def test_posts_without_a_token_are_rejected_and_write_nothing(self) -> None:
        response = self.client.post(f"{BASE}/nuevo", data=NEW_USER)
        self.assertEqual(400, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, User.query.filter_by(username="nueva.persona").count())


if __name__ == "__main__":
    unittest.main()
