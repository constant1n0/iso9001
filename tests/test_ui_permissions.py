"""Navigation and list controls follow the permission matrix for every role."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    AccionCorrectiva,
    Auditoria,
    Capacitacion,
    CompetenceRequirement,
    CompetenceType,
    Document,
    DocumentCategory,
    EstadoNoConformidad,
    Mejora,
    NoConformidad,
    ParteInteresada,
    Person,
    RoleEnum,
    ResultadoVerificacion,
    RolResponsabilidad,
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
    ("/usuarios/", "nuevo", "/editar", "/eliminar", {ADMIN, AUDITOR}, {ADMIN}, set()),
    ("/personas/", "nueva", "/editar", "/eliminar", EVERYONE, {ADMIN, AUDITOR}, {ADMIN}),
    ("/competencias/requisitos/", "nuevo", "/editar", "/eliminar", EVERYONE, {ADMIN, AUDITOR},
     {ADMIN}),
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
                RolResponsabilidad(rol="Rol"),
            ])
            owner = Person(nombre="Persona")
            db.session.add(owner)
            db.session.flush()
            db.session.add(CompetenceRequirement(rol_id=1, tipo=CompetenceType.formacion,
                                                 descripcion="Requisito"))
            db.session.commit()
            self.ids = {u.role: u.id for u in User.query.all()}
            self.owner_id = owner.id
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

    def _nonconformity(
        self, estado: EstadoNoConformidad, *actions: dict
    ) -> tuple[int, list[int]]:
        """A nonconformity in ``estado`` with ``actions`` owned by the seeded person.

        Returns the nonconformity's id and its actions' ids, in the order given.
        """
        with self.app.app_context():
            nc = NoConformidad(descripcion="Con acciones", fecha_detectada=DAY, estado=estado)
            db.session.add(nc)
            db.session.flush()
            rows = [AccionCorrectiva(no_conformidad_id=nc.id, descripcion="Acción",
                                     responsable_id=self.owner_id, fecha_prevista=DAY, **values)
                    for values in actions]
            db.session.add_all(rows)
            db.session.commit()
            return nc.id, [row.id for row in rows]

    def test_nonconformity_page_controls_follow_role_and_state(self) -> None:
        """Every role adds and edits actions; administrators delete them; administrators
        and auditors verify done actions, close and cancel; administrators reopen."""
        with self.app.app_context():
            verifier = Person(nombre="Verificadora")
            db.session.add(verifier)
            db.session.commit()
            verifier_id = verifier.id
        done = {"fecha_realizada": DAY}
        effective = done | {"resultado_verificacion": ResultadoVerificacion.eficaz,
                            "fecha_verificacion": DAY, "verificador_id": verifier_id,
                            "evidencia_verificacion": "Evidencia"}
        planned, (first_planned, done_planned) = self._nonconformity(
            EstadoNoConformidad.accion_planificada, {}, done)
        ready, (first_ready,) = self._nonconformity(EstadoNoConformidad.en_verificacion,
                                                     effective)
        closed, (first_closed,) = self._nonconformity(EstadoNoConformidad.cerrada, effective)

        def controls(nc_id: int, action_id: int) -> dict:
            base = f"/no_conformidades/{nc_id}"
            return {
                "add": f'href="{base}/acciones/nueva"',
                "edit": f'href="{base}/acciones/{action_id}/editar"',
                "delete": f'action="{base}/acciones/{action_id}/eliminar"',
                "verify": "/verificar",
                "close": f'action="/no_conformidades/cerrar/{nc_id}"',
                "blockers": "No se puede cerrar todavía",
                "cancel": f'action="/no_conformidades/cancelar/{nc_id}"',
                "reopen": f'action="/no_conformidades/reabrir/{nc_id}"',
            }

        verify_done = f'href="/no_conformidades/{planned}/acciones/{done_planned}/verificar"'
        everyone, deciders = set(RoleEnum), {ADMIN, AUDITOR}
        expected = {
            (planned, first_planned): {"add": everyone, "edit": everyone, "delete": {ADMIN},
                                       "verify": deciders, "close": set(),
                                       "blockers": deciders, "cancel": deciders,
                                       "reopen": set()},
            (ready, first_ready): {"add": everyone, "edit": set(), "delete": {ADMIN},
                                   "verify": set(), "close": deciders, "blockers": set(),
                                   "cancel": deciders, "reopen": set()},
            (closed, first_closed): {"add": set(), "edit": set(), "delete": set(),
                                     "verify": set(), "close": set(), "blockers": set(),
                                     "cancel": set(), "reopen": {ADMIN}},
        }
        for (nc_id, action_id), allowed in expected.items():
            for role in RoleEnum:
                html = self._page(role, f"/no_conformidades/{nc_id}").get_data(as_text=True)
                for name, marker in controls(nc_id, action_id).items():
                    with self.subTest(nc=nc_id, role=role.name, control=name):
                        self.assertEqual(role in allowed[name], marker in html)
                if nc_id == planned:  # only the done action offers verification
                    with self.subTest(role=role.name, control="verify done action"):
                        self.assertEqual(role in deciders, verify_done in html)

    def test_navigation_links_follow_the_read_permission(self) -> None:
        expected = {"/auditorias/": {ADMIN, AUDITOR}, "/documents/": {ADMIN}, "/no_conformidades/": set(RoleEnum),
                    "/usuarios/": {ADMIN, AUDITOR}, "/perfil/": set(RoleEnum),
                    "/personas/": set(RoleEnum), "/competencias/requisitos/": set(RoleEnum),
                    "/competencias/matriz": set(RoleEnum)}
        for role in RoleEnum:
            html = self._page(role, "/dashboard/").get_data(as_text=True)
            self.assertIn('class="nav__group">Personas y competencia<', html)
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
