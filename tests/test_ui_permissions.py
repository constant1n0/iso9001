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
    DocumentRevision,
    EstadoNoConformidad,
    EstadoRevision,
    Mejora,
    NoConformidad,
    ParteInteresada,
    Person,
    ProgramaAuditoria,
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
    ("/programas-auditoria/", "nuevo", "/editar", "/eliminar", {ADMIN, AUDITOR},
     {ADMIN, AUDITOR}, {ADMIN}),
    ("/documents/", "new", "/edit/", "/delete/", EVERYONE, {ADMIN, AUDITOR}, set()),
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
                ProgramaAuditoria(anio=2026, objetivos="O", alcance="A"),
                Document(title="T", code="DOC-1", category=DocumentCategory.OTRO),
                RolResponsabilidad(rol="Rol"),
            ])
            owner = Person(nombre="Persona")
            db.session.add(owner)
            db.session.flush()
            db.session.add(CompetenceRequirement(rol_id=1, tipo=CompetenceType.formacion,
                                                 descripcion="Requisito"))
            db.session.add(DocumentRevision(document_id=1, numero=1, content="c",
                                            estado=EstadoRevision.vigente, effective_from=DAY))
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

    def test_document_page_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors edit, draft, submit, reject and publish;
        administrators approve and withdraw; a withdrawn document offers nothing."""
        from app.services import document_revisions as revisions
        from app.services import documents
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            approver = Person(nombre="Aprobadora")
            db.session.add(approver)
            db.session.flush()

            def document(code: str, steps: tuple[str, ...]) -> tuple[int, int]:
                """A document whose revisions went through ``steps``; its last revision's id."""
                doc_id = documents.create(db.session, system, {
                    "title": code, "code": code, "category": DocumentCategory.OTRO,
                    "owner_id": self.owner_id, "author_id": self.owner_id,
                    "content": "Texto", "change_summary": "Alta"}).id
                (rev,) = (r.id for r in revisions.list_(db.session, system, doc_id))
                for step in steps:
                    if step == "submit":
                        revisions.submit(db.session, system, rev)
                    elif step == "approve":
                        revisions.approve(db.session, system, rev, approver_id=approver.id,
                                          today=DAY)
                    elif step == "publish":
                        revisions.publish(db.session, system, rev, today=DAY)
                    elif step == "redraft":
                        rev = revisions.start_draft(
                            db.session, system, doc_id,
                            {"author_id": self.owner_id, "change_summary": "Cambio"}).id
                    else:
                        revisions.withdraw(db.session, system, doc_id, "Baja", today=DAY)
                return doc_id, rev

            in_force = ("submit", "approve", "publish")
            states = {
                "first draft": document("D-1", ()),
                "draft": document("D-2", (*in_force, "redraft")),
                "in review": document("D-3", (*in_force, "redraft", "submit")),
                "approved": document("D-4", (*in_force, "redraft", "submit", "approve")),
                "in force": document("D-5", in_force),
                "withdrawn": document("D-6", (*in_force, "redraft", "withdraw")),
            }
            db.session.commit()

        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        drafting = {"edit": writers, "withdraw": admins, "edit draft": writers,
                    "upload": writers, "submit": writers, "discard": writers}
        expected = {
            "first draft": drafting | {"discard": nobody},
            "draft": drafting,
            "in review": {"edit": writers, "withdraw": admins, "approve": admins,
                          "reject": writers},
            "approved": {"edit": writers, "withdraw": admins, "publish": writers},
            "in force": {"edit": writers, "withdraw": admins, "new revision": writers},
            "withdrawn": {},
        }
        for state, (doc_id, rev_id) in states.items():
            base, rev = f"/documents/{doc_id}", f"/documents/{doc_id}/revisions/{rev_id}"
            controls = {
                "edit": f'href="/documents/edit/{doc_id}"',
                "new revision": f'href="{base}/revisions/new"',
                "withdraw": f'href="{base}/withdraw"',
                "edit draft": f'href="{rev}/edit"',
                "upload": f'action="{rev}/attachment"',
                "submit": f'action="{rev}/submit"',
                "discard": f'action="{rev}/discard"',
                "approve": f'href="{rev}/approve"',
                "reject": f'href="{rev}/reject"',
                "publish": f'action="{rev}/publish"',
            }
            for role in RoleEnum:
                response = self._page(role, base)
                if response.status_code == 404:  # nothing in force: drafts readers only
                    with self.subTest(state=state, role=role.name):
                        self.assertEqual((OPERATIVO, True),
                                         (role, state in ("first draft", "withdrawn")))
                    continue
                html = response.get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(state=state, role=role.name, control=name):
                        self.assertEqual(role in expected[state].get(name, nobody),
                                         marker in html)

    def test_programme_page_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors edit programmes and add audits to them;
        administrators approve drafts and delete programmes without audits."""
        from app.services import audit_programmes, audits
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            draft = ProgramaAuditoria.query.one().id
            approved = audit_programmes.create(db.session, system, {
                "anio": 2025, "objetivos": "O", "alcance": "A"}).id
            audit_programmes.approve(db.session, system, approved,
                                     aprobado_por_id=self.owner_id, today=DAY)
            audits.create(db.session, system, {
                "area_auditada": "Compras", "fecha": date(2025, 5, 5), "auditor": "x",
                "resultado": "r", "programa_id": approved})
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        expected = {
            draft: {"edit": writers, "approve": admins, "delete": admins, "audit": writers},
            approved: {"edit": writers, "approve": nobody, "delete": nobody, "audit": writers},
        }
        for pid, allowed in expected.items():
            base = f"/programas-auditoria/{pid}"
            controls = {"edit": f'href="{base}/editar"', "approve": f'href="{base}/aprobar"',
                        "delete": f'action="{base}/eliminar"',
                        "audit": f'href="/auditorias/nueva?programa_id={pid}"'}
            for role in RoleEnum:
                html = self._page(role, base).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(programme=pid, role=role.name, control=name):
                        self.assertEqual(role in allowed[name], marker in html)

    def test_audit_page_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors add and edit findings, administrators delete
        them; a cancelled audit shows its findings without any finding control."""
        from app.services import audit_findings, audits
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            open_id = Auditoria.query.one().id
            cancelled_id = audits.create(db.session, system, {
                "area_auditada": "Ventas", "fecha": DAY, "auditor": "x",
                "resultado": "r"}).id
            finding = {"tipo": "observacion", "descripcion": "Hallazgo"}
            findings = {audit_id: audit_findings.create(
                db.session, system, finding | {"auditoria_id": audit_id}).id
                for audit_id in (open_id, cancelled_id)}
            audits.update(db.session, system, cancelled_id, {"estado": "CANCELADA"})
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        expected = {
            open_id: {"edit audit": writers, "add": writers, "edit": writers,
                      "delete": admins},
            cancelled_id: {"edit audit": writers, "add": nobody, "edit": nobody,
                           "delete": nobody},
        }
        for audit_id, allowed in expected.items():
            base = f"/auditorias/{audit_id}"
            finding_url = f"{base}/hallazgos/{findings[audit_id]}"
            controls = {"edit audit": f'href="/auditorias/editar/{audit_id}"',
                        "add": f'href="{base}/hallazgos/nuevo"',
                        "edit": f'href="{finding_url}/editar"',
                        "delete": f'action="{finding_url}/eliminar"'}
            for role in RoleEnum:
                html = self._page(role, base).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(audit=audit_id, role=role.name, control=name):
                        self.assertEqual(role in allowed[name], marker in html)

    def test_quality_objective_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors create, edit and change the state of objectives and
        add and edit measurements while the objective is active; administrators delete
        measurements and objectives without measurements (decision QO4)."""
        from app.services import quality_objectives as qo
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            def objective(titulo):
                return qo.objectives.create(db.session, system, {
                    "titulo": titulo, "indicador": "Indicador", "meta": 1,
                    "sentido": "mayor_igual", "responsable_id": self.owner_id,
                    "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2026, 12, 31),
                    "frecuencia": "mensual"}).id

            measured, empty, closed = (objective(t) for t in ("Medido", "Sin medir", "Cerrado"))
            measurements = {oid: qo.measurements.create(db.session, system, {
                "objetivo_id": oid, "fecha": DAY, "valor": 1}, today=DAY).id
                for oid in (measured, closed)}
            qo.objectives.update(db.session, system, closed, {"estado": "alcanzado"})
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        base = "/objetivos-calidad"
        html = {role: self._page(role, f"{base}/").get_data(as_text=True) for role in RoleEnum}
        for role in RoleEnum:
            with self.subTest(page="list", role=role.name):
                self.assertEqual(role in writers, f'href="{base}/nuevo"' in html[role])
                self.assertEqual(role in writers, f'href="{base}/{empty}/editar"' in html[role])
                # Every role reads the yearly PDF (QO-3).
                self.assertRegex(html[role], rf'href="{base}/pdf\?anio=\d{{4}}"')
        expected = {
            measured: {"edit": writers, "delete": nobody, "close": writers, "reopen": nobody,
                       "add": writers, "edit measurement": writers,
                       "delete measurement": admins},
            empty: {"edit": writers, "delete": admins, "close": writers, "reopen": nobody,
                    "add": writers},
            closed: {"edit": writers, "delete": nobody, "close": nobody, "reopen": writers,
                     "add": nobody, "edit measurement": nobody, "delete measurement": nobody},
        }
        for oid, allowed in expected.items():
            page = f"{base}/{oid}"
            item = f"{page}/mediciones/{measurements.get(oid)}"
            controls = {"edit": f'href="{page}/editar"', "delete": f'action="{page}/eliminar"',
                        "close": f'action="{page}/estado/alcanzado"',
                        "reopen": f'action="{page}/estado/activo"',
                        "add": f'href="{page}/mediciones/nueva"',
                        "edit measurement": f'href="{item}/editar"',
                        "delete measurement": f'action="{item}/eliminar"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(objective=oid, role=role.name, control=name):
                        self.assertEqual(role in allowed.get(name, nobody), marker in body)

    def test_management_review_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors edit drafts, write a held review's comments and
        conclusions, and add and edit actions until the review is closed (then only their
        follow-up); administrators hold drafts, close held reviews, delete drafts without
        actions and delete the actions of a review that is not closed (MR4 to MR6)."""
        from app.services import management_reviews as mr
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            def review(*steps, action=True):
                rid = mr.reviews.create(db.session, system, {
                    "fecha": date(2026, 7, 10), "periodo_desde": date(2026, 4, 1),
                    "periodo_hasta": date(2026, 6, 30), "asistente_ids": [self.owner_id]}).id
                aid = mr.actions.create(db.session, system, {
                    "revision_id": rid, "tipo": "mejora", "descripcion": "Acción",
                    "responsable_id": self.owner_id, "fecha_limite": date(2026, 12, 31)},
                    today=DAY).id if action else None
                for step in steps:
                    if step == "close":
                        mr.reviews.update(db.session, system, rid, {"conclusiones": "Conforme"})
                    getattr(mr, step)(db.session, system, rid, today=DAY)
                return rid, aid

            empty, draft = review(action=False), review()
            held, closed = review("hold"), review("hold", "close")
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        expected = {
            empty: {"edit": writers, "hold": admins, "delete": admins, "add": writers},
            draft: {"edit": writers, "hold": admins, "add": writers, "edit action": writers,
                    "delete action": admins},
            held: {"conclusions": writers, "close": admins, "add": writers,
                   "edit action": writers, "delete action": admins},
            closed: {"edit action": writers},
        }
        for (rid, aid), allowed in expected.items():
            page = f"/revisiones-direccion/{rid}"
            item = f"{page}/acciones/{aid}"
            controls = {"edit": f'href="{page}/editar"', "conclusions": f'href="{page}/conclusiones"',
                        "hold": f'action="{page}/celebrar"', "close": f'action="{page}/cerrar"',
                        "delete": f'action="{page}/eliminar"', "add": f'href="{page}/acciones/nueva"',
                        "edit action": f'href="{item}/editar"',
                        "delete action": f'action="{item}/eliminar"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(review=rid, role=role.name, control=name):
                        self.assertEqual(role in allowed.get(name, nobody), marker in body)
        for role in RoleEnum:
            html = self._page(role, "/revisiones-direccion/").get_data(as_text=True)
            with self.subTest(page="list", role=role.name):
                self.assertEqual(role in writers, 'href="/revisiones-direccion/nueva"' in html)

    def test_navigation_links_follow_the_read_permission(self) -> None:
        expected = {"/auditorias/": {ADMIN, AUDITOR}, "/programas-auditoria/": {ADMIN, AUDITOR},
                    "/documents/": set(RoleEnum), "/no_conformidades/": set(RoleEnum),
                    "/usuarios/": {ADMIN, AUDITOR}, "/perfil/": set(RoleEnum),
                    "/personas/": set(RoleEnum), "/competencias/requisitos/": set(RoleEnum),
                    "/competencias/matriz": set(RoleEnum),
                    "/objetivos-calidad/": set(RoleEnum),
                    "/revisiones-direccion/": {ADMIN, AUDITOR}}
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
