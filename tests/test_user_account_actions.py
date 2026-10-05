"""Administrator account actions (UM-3b): deactivate, reactivate, send a reset link.

The actions are POST-only adapters over ``services.users.set_active`` and the
shared reset e-mail helper. The ``USERS`` update grant decides who may use
them (administrators). Guard rails and mail failures come back to the list as
Spanish flash messages, never as a 500. Every action writes one security-log
line that names the target and the acting administrator, and never the link
or its token.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import unquote

import test_auth_bootstrap as bootstrap
from test_user_routes import ADMIN, AUDITOR, OPERATIVO, PASSWORD, _post_forms, _seed

from app.extensions import db
from app.models import ApiToken, AuditLog, User
from app.services import api_tokens
from app.services import users as users_service
from app.services.actor import Actor
from app.services.errors import Conflict
from app.utils import password_reset_mail
from app.utils.security_logger import log_admin_reset_link

BASE = "/usuarios"
CANONICAL_RESET_BASE = "https://qms.example.invalid"
CLI_ADMIN = Actor(user_id=None, label="cli:operator", role=ADMIN, channel="cli")
ACTIONS = ("desactivar", "reactivar", "enviar-enlace")
DENIED = ("danger", "No tienes permiso para acceder a esta página.")
DEACTIVATED = ("success", "Usuario desactivado exitosamente")
REACTIVATED = ("success", "Usuario reactivado exitosamente")
ALREADY_INACTIVE = ("info", "El usuario ya estaba desactivado.")
ALREADY_ACTIVE = ("info", "El usuario ya estaba activo.")
LINK_SENT = ("success", "Se ha enviado un enlace para restablecer la contraseña.")
NO_EMAIL = "El usuario no tiene correo electrónico: añade uno antes de enviar el enlace."
INACTIVE_LINK = "No se puede enviar un enlace a una cuenta desactivada."
MAIL_FAILED = "No se ha podido enviar el correo. Inténtalo de nuevo más tarde."


class AccountActionsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app(PASSWORD_RESET_BASE_URL=CANONICAL_RESET_BASE)
        self.ids = _seed(self.app)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()
        sender = patch.object(password_reset_mail.mail, "send")
        self.mail_send = sender.start()
        self.addCleanup(sender.stop)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, role=ADMIN, client=None) -> None:
        with (client or self.client).session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True

    def flashes(self, client=None) -> list:
        with (client or self.client).session_transaction() as session:
            return list(session.pop("_flashes", []))

    def post(self, action: str, role):
        return self.client.post(f"{BASE}/{self.ids[role]}/{action}")

    def change(self, role, **values) -> None:
        with self.app.app_context():
            user = db.session.get(User, self.ids[role])
            for key, value in values.items():
                setattr(user, key, value)
            db.session.commit()

    def is_active(self, role) -> bool:
        with self.app.app_context():
            return db.session.get(User, self.ids[role]).active

    def audit_count(self) -> int:
        with self.app.app_context():
            return AuditLog.query.count()

    def assert_back_to_list(self, response, flash: tuple) -> None:
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(flash, self.flashes())

    # -- access -------------------------------------------------------------

    def test_auditor_and_operativo_are_refused_and_nothing_changes(self) -> None:
        for role, target in ((AUDITOR, OPERATIVO), (OPERATIVO, AUDITOR)):
            self.login(role)
            for action in ACTIONS:
                with self.subTest(role=role.name, action=action):
                    response = self.post(action, target)
                    self.assertEqual(302, response.status_code)
                    self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                    self.assertIn(DENIED, self.flashes())
        self.assertTrue(self.is_active(OPERATIVO) and self.is_active(AUDITOR))
        self.mail_send.assert_not_called()
        self.assertEqual(0, self.audit_count())

    def test_anonymous_posts_go_to_login_and_get_is_not_allowed(self) -> None:
        for action in ACTIONS:
            with self.subTest(action=action):
                response = self.post(action, OPERATIVO)
                self.assertEqual(302, response.status_code)
                self.assertIn("/login", response.headers["Location"])
        self.login()
        for action in ACTIONS:
            with self.subTest(method="GET", action=action):
                url = f"{BASE}/{self.ids[OPERATIVO]}/{action}"
                # Werkzeug raises 405; the global ``Exception`` handler currently
                # turns HTTP errors without their own handler into a 500, as on
                # every POST-only route. Either way nothing happens.
                self.assertIn(self.client.get(url).status_code, (405, 500))
        self.assertTrue(self.is_active(OPERATIVO))
        self.mail_send.assert_not_called()

    def test_unknown_user_is_not_found(self) -> None:
        self.login()
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertEqual(404, self.client.post(f"{BASE}/999/{action}").status_code)
        self.mail_send.assert_not_called()

    # -- deactivate and reactivate -------------------------------------------

    def test_deactivation_ends_sessions_revokes_tokens_and_refuses_login(self) -> None:
        with self.app.app_context():
            _, token = api_tokens.issue(
                db.session,
                CLI_ADMIN,
                secret_key=self.app.config["SECRET_KEY"],
                user_id=self.ids[OPERATIVO],
                name="portatil",
            )
            db.session.commit()
            token_id = token.id
        victim = self.app.test_client()
        self.login(OPERATIVO, victim)
        self.assertEqual(200, victim.get("/dashboard/").status_code)

        self.login()
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post("desactivar", OPERATIVO)

        self.assert_back_to_list(response, DEACTIVATED)
        self.assertFalse(self.is_active(OPERATIVO))
        with self.app.app_context():
            token = db.session.get(ApiToken, token_id)
            self.assertIsNotNone(token.revoked_at)
            self.assertEqual("administrador", token.revoked_by_label)
        self.assertIn(
            "USER_DEACTIVATED | user=operativo | by=administrador", "\n".join(logs.output)
        )
        dropped = victim.get("/dashboard/")
        self.assertEqual(302, dropped.status_code)
        self.assertIn("/login", dropped.headers["Location"])
        login = self.app.test_client()
        response = login.post("/login", data={"username": "operativo", "password": PASSWORD})
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertIn(("danger", "Credenciales inválidas"), self.flashes(login))

    def test_reactivation_restores_login_and_is_audited(self) -> None:
        self.change(OPERATIVO, active=False)
        self.login()
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post("reactivar", OPERATIVO)

        self.assert_back_to_list(response, REACTIVATED)
        self.assertTrue(self.is_active(OPERATIVO))
        self.assertIn(
            "USER_REACTIVATED | user=operativo | by=administrador", "\n".join(logs.output)
        )
        with self.app.app_context():
            (row,) = AuditLog.query.all()
            self.assertEqual(("update", self.ids[OPERATIVO]), (row.action, row.entity_id))
            self.assertEqual((False, True), (row.before["active"], row.after["active"]))
        login = self.app.test_client()
        response = login.post("/login", data={"username": "operativo", "password": PASSWORD})
        self.assertTrue(response.headers["Location"].endswith("/dashboard/"))

    def test_guard_rails_come_back_as_spanish_flashes(self) -> None:
        self.login()
        with self.assertNoLogs("security", level="INFO"):
            response = self.post("desactivar", ADMIN)
        self.assert_back_to_list(response, ("danger", users_service.OWN_DEACTIVATION))
        self.assertTrue(self.is_active(ADMIN))
        self.assertEqual(0, self.audit_count())

        # Over the web the actor is always another active administrator, so the
        # last-administrator rule only fires under a concurrent change.
        refusal = Conflict(users_service.LAST_ADMINISTRATOR)
        with (
            patch.object(users_service, "set_active", side_effect=refusal),
            patch("app.routes.user_routes.db.session.rollback") as rollback,
        ):
            response = self.post("desactivar", AUDITOR)
        self.assert_back_to_list(response, ("danger", users_service.LAST_ADMINISTRATOR))
        rollback.assert_called_once()

    def test_actions_that_change_nothing_say_so_and_log_nothing(self) -> None:
        self.change(AUDITOR, active=False)
        self.login()
        with self.assertNoLogs("security", level="INFO"):
            self.assert_back_to_list(self.post("reactivar", OPERATIVO), ALREADY_ACTIVE)
            self.assert_back_to_list(self.post("desactivar", AUDITOR), ALREADY_INACTIVE)
        self.assertEqual(0, self.audit_count())

    # -- reset link -----------------------------------------------------------

    def test_reset_link_uses_the_administrator_wording(self) -> None:
        self.login()
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post("enviar-enlace", OPERATIVO)

        self.assert_back_to_list(response, LINK_SENT)
        (call,) = self.mail_send.call_args_list
        message = call.args[0]
        self.assertEqual(["operativo@example.com"], message.recipients)
        self.assertIn("Un administrador", message.body)
        self.assertIn("Si no esperabas este mensaje, puedes ignorarlo", message.body)
        self.assertIn("Este enlace expirará en 1 hora.", message.body)
        self.assertNotIn("Si no solicitaste este cambio", message.body)
        link = next(line for line in message.body.splitlines() if line.startswith("https://"))
        self.assertTrue(link.startswith(f"{CANONICAL_RESET_BASE}/reset_password/"))
        token = unquote(link.rsplit("/", 1)[1])
        with self.app.app_context():
            verification = User.verify_reset_token(token)
            self.assertEqual(self.ids[OPERATIVO], verification.user.id)
        output = "\n".join(logs.output)
        self.assertIn("PASSWORD_RESET_LINK_SENT | user=operativo | by=administrador", output)
        self.assertNotIn(token, output)
        self.assertNotIn("/reset_password/", output)

    def assert_link_refused(self, role, message: str, reason: str) -> str:
        """No 500: back to the list with ``message``; returns the security log."""
        self.login()
        with self.assertLogs("security", level="WARNING") as logs:
            response = self.post("enviar-enlace", role)
        self.assert_back_to_list(response, ("danger", message))
        output = "\n".join(logs.output)
        self.assertIn(
            f"PASSWORD_RESET_LINK_FAILED | user={role.name.lower()} | by=administrador "
            f"| reason={reason}",
            output,
        )
        return output

    def test_reset_link_needs_an_email_address(self) -> None:
        self.change(OPERATIVO, email=None)
        self.assert_link_refused(OPERATIVO, NO_EMAIL, "no_email")
        self.mail_send.assert_not_called()

    def test_reset_link_is_never_sent_to_an_inactive_account(self) -> None:
        self.change(OPERATIVO, active=False)
        self.assert_link_refused(OPERATIVO, INACTIVE_LINK, "inactive")
        self.mail_send.assert_not_called()

    def test_reset_link_fails_cleanly_when_mail_is_misconfigured(self) -> None:
        self.app.config["PASSWORD_RESET_BASE_URL"] = "http://insecure.example.invalid"
        self.assert_link_refused(OPERATIVO, MAIL_FAILED, "configuration")
        self.mail_send.assert_not_called()

    def test_reset_link_fails_cleanly_when_delivery_fails(self) -> None:
        self.mail_send.side_effect = RuntimeError("smtp.example.invalid password=hunter2")
        output = self.assert_link_refused(OPERATIVO, MAIL_FAILED, "delivery")
        self.assertNotIn("hunter2", output)

    def test_mail_failures_name_their_cause_for_operators_only(self) -> None:
        """The application log gets the exception types, never their text."""
        self.mail_send.side_effect = RuntimeError("smtp.example.invalid password=hunter2")
        with self.assertLogs("app.routes.user_routes", level="WARNING") as logs:
            self.assert_link_refused(OPERATIVO, MAIL_FAILED, "delivery")
        output = "\n".join(logs.output)
        self.assertIn("ResetEmailDeliveryError", output)
        self.assertIn("RuntimeError", output)
        for secret in ("hunter2", "smtp.example.invalid", "operativo@example.com",
                       "/reset_password/"):
            self.assertNotIn(secret, output)

        self.app.config["PASSWORD_RESET_BASE_URL"] = "http://insecure.example.invalid"
        with self.assertLogs("app.routes.user_routes", level="WARNING") as logs:
            self.assert_link_refused(OPERATIVO, MAIL_FAILED, "configuration")
        output = "\n".join(logs.output)
        self.assertIn("ResetEmailConfigurationError", output)
        self.assertNotIn("insecure.example.invalid", output)

    # -- screens --------------------------------------------------------------

    def _actions(self, url: str) -> set:
        html = self.client.get(url).get_data(as_text=True)
        return {f["action"] for f in _post_forms(html) if (f["action"] or "").startswith(BASE)}

    def test_list_offers_only_the_actions_that_apply(self) -> None:
        self.change(AUDITOR, active=False)
        self.login()
        actions = self._actions(f"{BASE}/")
        own, inactive, active = (self.ids[r] for r in (ADMIN, AUDITOR, OPERATIVO))
        self.assertIn(f"{BASE}/{active}/desactivar", actions)
        self.assertIn(f"{BASE}/{active}/enviar-enlace", actions)
        self.assertNotIn(f"{BASE}/{active}/reactivar", actions)
        self.assertNotIn(f"{BASE}/{own}/desactivar", actions)  # never on your own row
        self.assertIn(f"{BASE}/{inactive}/reactivar", actions)
        self.assertNotIn(f"{BASE}/{inactive}/desactivar", actions)
        self.assertNotIn(f"{BASE}/{inactive}/enviar-enlace", actions)
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        self.assertIn('data-confirm="¿Desactivar la cuenta de operativo?', html)
        for form in _post_forms(html):
            self.assertIn("csrf_token", form["fields"])

        self.change(AUDITOR, active=True)
        self.login(AUDITOR)
        self.assertEqual(set(), self._actions(f"{BASE}/"))

    def test_edit_screen_offers_the_actions_under_the_form(self) -> None:
        self.login()
        target = self.ids[OPERATIVO]
        self.assertEqual(
            {f"{BASE}/{target}/desactivar", f"{BASE}/{target}/enviar-enlace"},
            self._actions(f"{BASE}/{target}/editar"),
        )
        self.assertNotIn(
            f"{BASE}/{self.ids[ADMIN]}/desactivar",
            self._actions(f"{BASE}/{self.ids[ADMIN]}/editar"),
        )


class ResetLinkRateLimitTestCase(unittest.TestCase):
    def test_reset_link_is_rate_limited_per_client(self) -> None:
        app = bootstrap.build_app(
            RATELIMIT_ENABLED=True, PASSWORD_RESET_BASE_URL=CANONICAL_RESET_BASE
        )
        ids = _seed(app)

        def drop() -> None:
            with app.app_context():
                db.session.remove()
                db.drop_all()

        self.addCleanup(drop)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(ids[ADMIN])
            session["_fresh"] = True
        url = f"{BASE}/{ids[OPERATIVO]}/enviar-enlace"
        with patch.object(password_reset_mail.mail, "send") as mail_send:
            codes = [client.post(url).status_code for _ in range(10)]
            refused = client.post(url)
        self.assertEqual([302] * 10, codes)
        # Flask-Limiter answers 429; the global ``Exception`` handler currently
        # turns any HTTP error without its own handler into a 500. Either way
        # the eleventh request is refused before anything is sent.
        self.assertIn(refused.status_code, (429, 500))
        self.assertEqual(10, mail_send.call_count)

    def test_refused_requests_do_not_spend_the_administrators_budget(self) -> None:
        app = bootstrap.build_app(
            RATELIMIT_ENABLED=True, PASSWORD_RESET_BASE_URL=CANONICAL_RESET_BASE
        )
        ids = _seed(app)

        def drop() -> None:
            with app.app_context():
                db.session.remove()
                db.drop_all()

        self.addCleanup(drop)

        def client_for(role=None):
            client = app.test_client()
            if role is not None:
                with client.session_transaction() as session:
                    session["_user_id"] = str(ids[role])
                    session["_fresh"] = True
            return client

        url = f"{BASE}/{ids[OPERATIVO]}/enviar-enlace"
        anonymous, operativo = client_for(), client_for(OPERATIVO)
        admin = client_for(ADMIN)
        with patch.object(password_reset_mail.mail, "send") as mail_send:
            # All three clients share one address, so they share one budget.
            for client, target in ((anonymous, "/login"), (operativo, "/dashboard/")):
                for _ in range(11):
                    response = client.post(url)
                    self.assertEqual(302, response.status_code)
                    self.assertIn(target, response.headers["Location"])
            mail_send.assert_not_called()
            allowed = admin.post(url)
        self.assertEqual(302, allowed.status_code)
        self.assertTrue(allowed.headers["Location"].endswith(f"{BASE}/"))
        self.assertEqual(1, mail_send.call_count)


class AdminResetLinkLogTestCase(unittest.TestCase):
    """The refusal reason alone decides between the sent and the failed line."""

    def test_outcome_follows_the_reason(self) -> None:
        with self.assertLogs("security", level="INFO") as logs:
            log_admin_reset_link("operativo", "administrador")
            log_admin_reset_link("operativo", "administrador", "delivery")
        sent, failed = logs.output
        self.assertIn("INFO:security:PASSWORD_RESET_LINK_SENT | user=operativo", sent)
        self.assertNotIn("reason=", sent)
        self.assertIn(
            "WARNING:security:PASSWORD_RESET_LINK_FAILED | user=operativo", failed
        )
        self.assertIn("| reason=delivery |", failed)


class AccountActionsCsrfTestCase(unittest.TestCase):
    """With CSRF on, the rendered action forms carry a token the routes accept."""

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

    def _active(self) -> bool:
        with self.app.app_context():
            return db.session.get(User, self.ids[OPERATIVO]).active

    def test_rendered_action_form_posts_and_a_bare_post_is_rejected(self) -> None:
        url = f"{BASE}/{self.ids[OPERATIVO]}/desactivar"
        self.assertEqual(400, self.client.post(url).status_code)
        self.assertTrue(self._active())
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        (form,) = [f for f in _post_forms(html) if f["action"] == url]
        self.assertEqual(302, self.client.post(url, data=form["fields"]).status_code)
        self.assertFalse(self._active())


if __name__ == "__main__":
    unittest.main()
