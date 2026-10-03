"""Navigation and list controls follow the permission matrix for every role."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Auditoria,
    Capacitacion,
    Document,
    DocumentCategory,
    Mejora,
    NoConformidad,
    ParteInteresada,
    RoleEnum,
    SatisfaccionCliente,
    User,
)

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
DAY = date(2026, 10, 5)
EVERYONE = frozenset(RoleEnum)

# (list URL, new-record path, edit marker, delete marker, roles that may read, write, delete)
MODULES = (
    ("/no_conformidades/", "nueva", "/editar/", "/eliminar/", EVERYONE, EVERYONE, {ADMIN}),
    ("/mejoras/", "nueva", "/editar/", "/eliminar/", EVERYONE, EVERYONE, {ADMIN}),
    ("/satisfaccion_cliente/", "nueva", "/editar/", "/eliminar/", EVERYONE, EVERYONE, {ADMIN}),
    ("/capacitaciones/", "nueva", "/editar/", "/eliminar/", EVERYONE, EVERYONE, {ADMIN}),
    ("/partes_interesadas/", "nueva", "/editar/", "/eliminar/", EVERYONE, EVERYONE, {ADMIN}),
    ("/auditorias/", "nueva", "/editar/", "/eliminar/", {ADMIN, AUDITOR}, {ADMIN, AUDITOR}, {ADMIN}),
    ("/documents/", "new", "/edit/", "/delete/", {ADMIN}, {ADMIN}, {ADMIN}),
)


class UiPermissionsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        with self.app.app_context():
            db.create_all()
            for role in RoleEnum:
                db.session.add(User(username=role.name.lower(), email=f"{role.name.lower()}@example.com",
                                    password=generate_password_hash("StrongPassword123!"), role=role))
            db.session.add_all([
                NoConformidad(descripcion="NC", fecha_detectada=DAY),
                Mejora(no_conformidad="Mejora"),
                SatisfaccionCliente(cliente="C", fecha_encuesta=DAY, puntuacion=5),
                Capacitacion(tema="T", fecha=DAY, personal="P"),
                ParteInteresada(nombre="Parte"),
                Auditoria(area_auditada="A", fecha=DAY, auditor="x", resultado="r"),
                Document(title="T", code="DOC-1", category=DocumentCategory.OTRO, content="c"),
            ])
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _page(self, role: RoleEnum, url: str):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.ids[role])
            session["_fresh"] = True
        return client.get(url)

    def test_list_controls_show_exactly_what_the_role_may_do(self) -> None:
        for url, new, edit, delete, read, write, remove in MODULES:
            for role in RoleEnum:
                with self.subTest(url=url, role=role.name):
                    response = self._page(role, url)
                    if role not in read:
                        self.assertEqual(302, response.status_code)
                        continue
                    html = response.get_data(as_text=True)
                    self.assertEqual(role in write, f'href="{url}{new}"' in html, "new button")
                    self.assertEqual(role in write, edit in html, "edit link")
                    self.assertEqual(role in remove, delete in html, "delete form")

    def test_navigation_links_follow_the_read_permission(self) -> None:
        expected = {"/auditorias/": {ADMIN, AUDITOR}, "/documents/": {ADMIN}, "/no_conformidades/": set(RoleEnum)}
        for role in RoleEnum:
            html = self._page(role, "/dashboard/").get_data(as_text=True)
            for link, roles in expected.items():
                with self.subTest(role=role.name, link=link):
                    self.assertEqual(role in roles, f'class="nav__link" href="{link}"' in html)

    def test_dashboard_shortcuts_follow_the_create_permission(self) -> None:
        shortcuts = {"/auditorias/nueva": {ADMIN, AUDITOR}, "/no_conformidades/nueva": set(RoleEnum),
                     "/capacitaciones/nueva": set(RoleEnum), "/satisfaccion_cliente/nueva": set(RoleEnum)}
        for role in RoleEnum:
            html = self._page(role, "/dashboard/").get_data(as_text=True)
            for link, roles in shortcuts.items():
                with self.subTest(role=role.name, link=link):
                    self.assertEqual(role in roles, re.search(rf'href="{link}"', html) is not None)


if __name__ == "__main__":
    unittest.main()
