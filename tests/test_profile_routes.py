"""Mi perfil (UM-4): every signed-in user manages their own account.

The routes are not role-gated, so they are characterized here rather than in
``test_access_characterization``: every role can see their data, change their
e-mail and password (the current password is required) and list and revoke
their own API tokens. Another user's token answers 404 and nothing changes. A
refused change re-renders the page with a Spanish message and never fills a
password back in. Every credential change, successful or refused, writes one
security-log line, which never holds a full e-mail address, a password, a token
secret or a hash. The two credential forms share one rate-limit budget per
signed-in account.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import test_auth_bootstrap as bootstrap
from test_user_routes import ADMIN, AUDITOR, OPERATIVO, PASSWORD, _post_forms, _seed
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import ApiToken, AuditLog, RoleEnum, User
from app.services import api_tokens
from app.services import users as users_service
from app.services.actor import Actor

BASE = "/perfil"
CLI_ADMIN = Actor(user_id=None, label="cli:operator", role=ADMIN, channel="cli")
NEW_PASSWORD = "ClaveNueva2026"
WRONG_PASSWORD = "NoEsLaMia2026"
EMAIL_CHANGED = ("success", "Correo electrónico actualizado exitosamente")
PASSWORD_CHANGED = ("success", "Contraseña actualizada exitosamente")
TOKEN_REVOKED = ("success", "Token revocado exitosamente")
ALREADY_REVOKED = ("danger", "El token ya estaba revocado.")


class ProfileBase(unittest.TestCase):
    csrf = False
    rate_limit = False

    def setUp(self) -> None:
        self.app = bootstrap.build_app(
            WTF_CSRF_ENABLED=self.csrf, RATELIMIT_ENABLED=self.rate_limit
        )
        self.ids = _seed(self.app)
        self.addCleanup(self._teardown)
        self.client = self.app.test_client()

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, role: RoleEnum) -> None:
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True

    def flashes(self) -> list:
        with self.client.session_transaction() as session:
            return list(session.pop("_flashes", []))

    def issue(self, role: RoleEnum, name: str, **overrides) -> tuple[str, int, str]:
        """Issue a token for ``role``'s user; returns ``(plaintext, id, hash)``."""
        with self.app.app_context():
            plaintext, token = api_tokens.issue(
                db.session,
                CLI_ADMIN,
                secret_key=self.app.config["SECRET_KEY"],
                user_id=self.ids[role],
                name=name,
                **overrides,
            )
            db.session.commit()
            return plaintext, token.id, token.token_hash

    def token(self, token_id: int) -> ApiToken:
        with self.app.app_context():
            token = db.session.get(ApiToken, token_id)
            db.session.expunge(token)
            return token

    def user(self, role: RoleEnum) -> User:
        with self.app.app_context():
            user = db.session.get(User, self.ids[role])
            db.session.expunge(user)
            return user

    def audit_rows(self) -> list[tuple]:
        with self.app.app_context():
            return [
                (r.action, r.entity_type, r.entity_id, r.channel, r.actor_user_id, r.after)
                for r in AuditLog.query.order_by(AuditLog.id)
            ]

    def assert_back_to_profile(self, response, flash: tuple) -> None:
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"{BASE}/"))
        self.assertIn(flash, self.flashes())

    def assert_rerendered(self, response, message: str, *secrets: str) -> str:
        """The page came back (200) showing ``message`` and none of ``secrets``."""
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn(message, html)
        for secret in secrets:
            self.assertNotIn(secret, html)
        return html

    def assert_refusal_logged(
        self, logs, event: str, reason: str, *secrets: str
    ) -> None:
        """One ``event`` warning for operativo with ``reason`` and no ``secrets``."""
        (line,) = logs.output
        self.assertEqual(
            f"WARNING:security:{event} | user=operativo | reason={reason} "
            "| ip=127.0.0.1",
            line,
        )
        for secret in secrets:
            self.assertNotIn(secret.lower(), line.lower())


class ProfilePageTestCase(ProfileBase):
    def test_every_role_sees_their_account_and_only_their_own_tokens(self) -> None:
        issued = {role: self.issue(role, f"token-{role.name.lower()}") for role in RoleEnum}
        for role in RoleEnum:
            self.login(role)
            response = self.client.get(f"{BASE}/")
            with self.subTest(role=role.name):
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn(f">{role.name.lower()}<", html)
                self.assertIn(f"{role.name.lower()}@example.com", html)
                self.assertIn(role.value, html)
                self.assertIn(">Activo<", html)
                for other, (_, token_id, _) in issued.items():
                    mine = other is role
                    prefix = self.token(token_id).prefix
                    self.assertEqual(mine, f"token-{other.name.lower()}" in html)
                    self.assertEqual(mine, prefix in html)
                    self.assertEqual(
                        mine, f'action="{BASE}/tokens/{token_id}/revocar"' in html
                    )

    def test_token_states_and_a_revoke_button_only_for_active_tokens(self) -> None:
        long_ago = datetime.now(timezone.utc) - timedelta(days=30)
        _, active_id, _ = self.issue(OPERATIVO, "portatil", scopes=["read", "write"])
        _, expired_id, _ = self.issue(OPERATIVO, "antiguo", days=1, now=long_ago)
        _, revoked_id, _ = self.issue(OPERATIVO, "retirado")
        with self.app.app_context():
            api_tokens.revoke(db.session, CLI_ADMIN, revoked_id)
            db.session.commit()
        self.login(OPERATIVO)
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        for label in (">Activo<", ">Caducado<", ">Revocado<", "read write"):
            self.assertIn(label, html)
        self.assertIn((long_ago + timedelta(days=1)).strftime("%Y-%m-%d"), html)
        self.assertIn(f'action="{BASE}/tokens/{active_id}/revocar"', html)
        for token_id in (expired_id, revoked_id):
            self.assertNotIn(f"/tokens/{token_id}/revocar", html)

    def test_a_user_without_tokens_sees_an_empty_state(self) -> None:
        self.login(AUDITOR)
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        self.assertIn("No tienes tokens de API.", html)
        self.assertNotIn("/revocar", html)

    def test_no_secret_or_hash_appears_in_the_page(self) -> None:
        plaintext, _, token_hash = self.issue(OPERATIVO, "portatil")
        with self.app.app_context():
            password_hash = db.session.get(User, self.ids[OPERATIVO]).password
        self.login(OPERATIVO)
        html = self.client.get(f"{BASE}/").get_data(as_text=True)
        for secret in (plaintext, plaintext.split("_", 2)[2], token_hash, password_hash):
            self.assertNotIn(secret, html)

    def test_the_user_chip_links_to_the_profile_for_every_role(self) -> None:
        for role in RoleEnum:
            self.login(role)
            html = self.client.get("/dashboard/").get_data(as_text=True)
            with self.subTest(role=role.name):
                self.assertIn(f'class="user-chip" href="{BASE}/"', html)
                self.assertIn("Mi perfil", html)

    def test_anonymous_requests_go_to_login_and_change_nothing(self) -> None:
        _, token_id, _ = self.issue(OPERATIVO, "portatil")
        for method, url, data in (
            ("GET", f"{BASE}/", None),
            ("POST", f"{BASE}/email",
             {"email": "x@example.com", "email_current_password": PASSWORD}),
            ("POST", f"{BASE}/contrasena",
             {"current_password": PASSWORD, "new_password": NEW_PASSWORD,
              "confirm_password": NEW_PASSWORD}),
            ("POST", f"{BASE}/tokens/{token_id}/revocar", None),
        ):
            with self.subTest(method=method, url=url):
                response = self.client.open(url, method=method, data=data)
                self.assertEqual(302, response.status_code)
                self.assertIn("/login", response.headers["Location"])
        user = self.user(OPERATIVO)
        self.assertEqual("operativo@example.com", user.email)
        self.assertTrue(check_password_hash(user.password, PASSWORD))
        self.assertIsNone(self.token(token_id).revoked_at)
        self.assertEqual(["create"], [row[0] for row in self.audit_rows()])


class ChangeEmailTestCase(ProfileBase):
    def post(self, email: str, password: str = PASSWORD):
        return self.client.post(
            f"{BASE}/email", data={"email": email, "email_current_password": password}
        )

    def test_every_role_changes_their_own_email_and_it_is_logged_masked(self) -> None:
        for role in RoleEnum:
            name = role.name.lower()
            self.login(role)
            with self.subTest(role=role.name):
                with self.assertLogs("security", level="INFO") as logs:
                    response = self.post(f" Nuevo.{name}@Example.com ")
                self.assert_back_to_profile(response, EMAIL_CHANGED)
                self.assertEqual(f"nuevo.{name}@example.com", self.user(role).email)
                (line,) = logs.output
                self.assertTrue(
                    line.startswith(f"INFO:security:EMAIL_CHANGE_SUCCESS | user={name} |"),
                    line,
                )
                self.assertIn("email=n***@example.com", line)
                self.assertNotIn(f"nuevo.{name}", line.lower())
                self.assertNotIn(f"{name}@example.com", line)
                self.assertNotIn(PASSWORD, line)
                action, entity, entity_id, channel, actor_id, after = self.audit_rows()[-1]
                self.assertEqual(
                    ("update", "users", self.ids[role], "web", self.ids[role]),
                    (action, entity, entity_id, channel, actor_id),
                )
                self.assertEqual({"email": f"nuevo.{name}@example.com"}, after)

    def test_a_wrong_current_password_changes_nothing_and_is_logged(self) -> None:
        self.login(OPERATIVO)
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post("otra@example.com", password=WRONG_PASSWORD)
        html = self.assert_rerendered(
            response, users_service.WRONG_PASSWORD, WRONG_PASSWORD
        )
        self.assert_refusal_logged(
            logs, "EMAIL_CHANGE_FAILED", "wrong_current_password",
            WRONG_PASSWORD, "otra@example.com", "operativo@example.com",
        )
        self.assertIn('value="otra@example.com"', html)  # the e-mail is kept
        self.assertEqual("operativo@example.com", self.user(OPERATIVO).email)
        self.assertEqual([], self.audit_rows())

    def test_an_e_mail_held_by_another_user_is_a_logged_conflict(self) -> None:
        self.login(OPERATIVO)
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post("Auditor@Example.com")
        self.assert_rerendered(response, users_service.DUPLICATE_EMAIL, PASSWORD)
        self.assert_refusal_logged(
            logs, "EMAIL_CHANGE_FAILED", "duplicate_email",
            PASSWORD, "auditor@example.com",
        )
        self.assertEqual("operativo@example.com", self.user(OPERATIVO).email)
        self.assertEqual([], self.audit_rows())

    def test_both_fields_are_required(self) -> None:
        self.login(OPERATIVO)
        with self.assertLogs("security", level="INFO") as logs:
            response = self.client.post(f"{BASE}/email", data={})
        self.assert_rerendered(response, "Este campo es obligatorio.")
        self.assert_refusal_logged(logs, "EMAIL_CHANGE_FAILED", "invalid_form")
        self.assertEqual("operativo@example.com", self.user(OPERATIVO).email)


class ChangePasswordTestCase(ProfileBase):
    def post(self, current: str = PASSWORD, new: str = NEW_PASSWORD,
             confirm: str | None = None):
        return self.client.post(
            f"{BASE}/contrasena",
            data={
                "current_password": current,
                "new_password": new,
                "confirm_password": new if confirm is None else confirm,
            },
        )

    def assert_unchanged(self) -> None:
        self.assertTrue(check_password_hash(self.user(OPERATIVO).password, PASSWORD))
        self.assertEqual([], self.audit_rows())

    def test_every_role_changes_their_own_password_and_it_is_logged(self) -> None:
        for role in RoleEnum:
            name = role.name.lower()
            self.login(role)
            with self.subTest(role=role.name):
                with self.assertLogs("security", level="INFO") as logs:
                    response = self.post()
                self.assert_back_to_profile(response, PASSWORD_CHANGED)
                self.assertTrue(
                    check_password_hash(self.user(role).password, NEW_PASSWORD)
                )
                (line,) = logs.output
                self.assertTrue(
                    line.startswith(
                        f"INFO:security:PASSWORD_CHANGE_SUCCESS | user={name} |"
                    ),
                    line,
                )
                self.assertNotIn(NEW_PASSWORD, line)
                self.assertNotIn(PASSWORD, line)
                action, entity, entity_id, channel, actor_id, after = self.audit_rows()[-1]
                self.assertEqual(
                    ("update", "users", self.ids[role], "web", self.ids[role]),
                    (action, entity, entity_id, channel, actor_id),
                )
                self.assertEqual({"credential_changed": True}, after)

    def test_a_wrong_current_password_changes_nothing_and_is_logged(self) -> None:
        self.login(OPERATIVO)
        with self.assertLogs("security", level="INFO") as logs:
            response = self.post(current=WRONG_PASSWORD)
        self.assert_rerendered(
            response, users_service.WRONG_PASSWORD, WRONG_PASSWORD, NEW_PASSWORD
        )
        self.assert_refusal_logged(
            logs, "PASSWORD_CHANGE_FAILED", "wrong_current_password",
            WRONG_PASSWORD, NEW_PASSWORD, PASSWORD,
        )
        self.assert_unchanged()

    def test_the_new_password_must_be_confirmed_have_eight_characters_and_differ(
        self,
    ) -> None:
        self.login(OPERATIVO)
        cases = (
            ("Las contraseñas no coinciden.", "invalid_form",
             dict(confirm="OtraClave2026")),
            ("8 caracteres", "invalid_form", dict(new="corta")),
            (users_service.SAME_PASSWORD, "same_password", dict(new=PASSWORD)),
        )
        for message, reason, values in cases:
            with self.subTest(message=message):
                with self.assertLogs("security", level="INFO") as logs:
                    response = self.post(**values)
                self.assert_rerendered(response, message, NEW_PASSWORD)
                self.assert_refusal_logged(
                    logs, "PASSWORD_CHANGE_FAILED", reason,
                    PASSWORD, NEW_PASSWORD, "OtraClave2026", "corta",
                )
        self.assert_unchanged()


class RevokeOwnTokenTestCase(ProfileBase):
    def revoke(self, token_id: int):
        return self.client.post(f"{BASE}/tokens/{token_id}/revocar")

    def test_every_role_revokes_their_own_token_and_it_is_logged(self) -> None:
        for role in RoleEnum:
            name = role.name.lower()
            _, token_id, _ = self.issue(role, f"token-{name}")
            prefix = self.token(token_id).prefix
            self.login(role)
            with self.subTest(role=role.name):
                with self.assertLogs("security", level="INFO") as logs:
                    response = self.revoke(token_id)
                self.assert_back_to_profile(response, TOKEN_REVOKED)
                token = self.token(token_id)
                self.assertIsNotNone(token.revoked_at)
                self.assertEqual(name, token.revoked_by_label)
                self.assertEqual(
                    [f"INFO:security:API_TOKEN_REVOKED | prefix={prefix} | by={name} "
                     "| ip=127.0.0.1"],
                    logs.output,
                )
                action, entity, entity_id, channel, actor_id, _ = self.audit_rows()[-1]
                self.assertEqual(
                    ("update", "api_tokens", token_id, "web", self.ids[role]),
                    (action, entity, entity_id, channel, actor_id),
                )

    def test_another_users_or_an_unknown_token_answers_404_and_nothing_changes(
        self,
    ) -> None:
        _, token_id, _ = self.issue(ADMIN, "del-admin")
        seen = self.audit_rows()
        self.login(OPERATIVO)
        for target in (token_id, 9999):
            with self.subTest(target=target), self.assertNoLogs("security", "INFO"):
                self.assertEqual(404, self.revoke(target).status_code)
        self.assertIsNone(self.token(token_id).revoked_at)
        self.assertEqual(seen, self.audit_rows())

    def test_revoking_an_already_revoked_token_changes_nothing(self) -> None:
        _, token_id, _ = self.issue(OPERATIVO, "portatil")
        self.login(OPERATIVO)
        self.assert_back_to_profile(self.revoke(token_id), TOKEN_REVOKED)
        revoked_at = self.token(token_id).revoked_at
        seen = self.audit_rows()
        with self.assertNoLogs("security", level="INFO"):
            self.assert_back_to_profile(self.revoke(token_id), ALREADY_REVOKED)
        self.assertEqual(revoked_at, self.token(token_id).revoked_at)
        self.assertEqual(seen, self.audit_rows())


class CredentialChangeRateLimitTestCase(ProfileBase):
    """Both credential forms check the current password, so they share one budget.

    The budget is ten requests per hour for each signed-in account, whatever the
    client address; anonymous requests are sent to the login page before it is
    counted.
    """

    rate_limit = True

    def wrong_guesses(self, count: int) -> list[int]:
        """Alternate wrong current-password guesses between both forms."""
        email = {"email": "otra@example.com", "email_current_password": WRONG_PASSWORD}
        password = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD,
                    "confirm_password": NEW_PASSWORD}
        forms = ((f"{BASE}/email", email), (f"{BASE}/contrasena", password))
        return [
            self.client.post(url, data=data).status_code
            for url, data in (forms[attempt % 2] for attempt in range(count))
        ]

    def test_the_eleventh_credential_change_in_an_hour_answers_429(self) -> None:
        self.login(OPERATIVO)
        self.assertEqual([200] * 10, self.wrong_guesses(10))
        for url, data in (
            (f"{BASE}/email",
             {"email": "nueva@example.com", "email_current_password": PASSWORD}),
            (f"{BASE}/contrasena",
             {"current_password": PASSWORD, "new_password": NEW_PASSWORD,
              "confirm_password": NEW_PASSWORD}),
        ):
            with self.subTest(url=url):
                with self.assertLogs("security", level="WARNING") as logs:
                    response = self.client.post(url, data=data)
                self.assertEqual(429, response.status_code)
                self.assertIn("RATE_LIMIT_EXCEEDED", logs.output[-1])
        user = self.user(OPERATIVO)
        self.assertEqual("operativo@example.com", user.email)
        self.assertTrue(check_password_hash(user.password, PASSWORD))
        self.assertEqual(200, self.client.get(f"{BASE}/").status_code)

    def test_anonymous_requests_and_other_accounts_keep_their_own_budget(self) -> None:
        for _ in range(11):
            response = self.client.post(f"{BASE}/email", data={})
            self.assertEqual(302, response.status_code)
        self.login(OPERATIVO)
        self.assertEqual([200] * 10, self.wrong_guesses(10))
        self.assertEqual(429, self.client.post(f"{BASE}/email", data={}).status_code)
        self.login(AUDITOR)
        response = self.client.post(
            f"{BASE}/email",
            data={"email": "nuevo.auditor@example.com",
                  "email_current_password": PASSWORD},
        )
        self.assert_back_to_profile(response, EMAIL_CHANGED)


class ProfileFormsCsrfTestCase(ProfileBase):
    """With CSRF on, every form on the page carries a token the routes accept."""

    csrf = True

    def setUp(self) -> None:
        super().setUp()
        (login,) = _post_forms(self.client.get("/login").get_data(as_text=True))
        fields = dict(login["fields"], username="operativo", password=PASSWORD)
        self.assertEqual(302, self.client.post("/login", data=fields).status_code)

    def forms(self) -> dict:
        page = self.client.get(f"{BASE}/").get_data(as_text=True)
        forms = {form["action"]: form["fields"] for form in _post_forms(page)}
        for action, fields in forms.items():
            self.assertTrue(fields.get("csrf_token"), f"csrf_token missing in {action}")
        return forms

    def test_the_profile_forms_submit_a_valid_csrf_token(self) -> None:
        _, token_id, _ = self.issue(OPERATIVO, "portatil")
        revoke_url = f"{BASE}/tokens/{token_id}/revocar"
        forms = self.forms()
        self.assertEqual({f"{BASE}/email", f"{BASE}/contrasena", revoke_url},
                         set(forms))
        response = self.client.post(
            f"{BASE}/email",
            data=forms[f"{BASE}/email"]
            | {"email": "nueva@example.com", "email_current_password": PASSWORD},
        )
        self.assertEqual(302, response.status_code)
        response = self.client.post(
            f"{BASE}/contrasena",
            data=forms[f"{BASE}/contrasena"]
            | {"current_password": PASSWORD, "new_password": NEW_PASSWORD,
               "confirm_password": NEW_PASSWORD},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual(302, self.client.post(revoke_url, data=forms[revoke_url])
                         .status_code)
        user = self.user(OPERATIVO)
        self.assertEqual("nueva@example.com", user.email)
        self.assertTrue(check_password_hash(user.password, NEW_PASSWORD))
        self.assertIsNotNone(self.token(token_id).revoked_at)

    def test_posts_without_a_token_are_rejected_and_change_nothing(self) -> None:
        _, token_id, _ = self.issue(OPERATIVO, "portatil")
        for url, data in (
            (f"{BASE}/email",
             {"email": "nueva@example.com", "email_current_password": PASSWORD}),
            (f"{BASE}/contrasena",
             {"current_password": PASSWORD, "new_password": NEW_PASSWORD,
              "confirm_password": NEW_PASSWORD}),
            (f"{BASE}/tokens/{token_id}/revocar", {}),
        ):
            with self.subTest(url=url):
                self.assertEqual(400, self.client.post(url, data=data).status_code)
        user = self.user(OPERATIVO)
        self.assertEqual("operativo@example.com", user.email)
        self.assertTrue(check_password_hash(user.password, PASSWORD))
        self.assertIsNone(self.token(token_id).revoked_at)


if __name__ == "__main__":
    unittest.main()
