"""Navigation and list controls follow the permission matrix for every role."""

from __future__ import annotations

import re
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app import session_lifetime
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
            session_lifetime.start(session)
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
        # Every reader links the minutes PDF, whatever the state (MR-3).
        expected = {
            empty: {"edit": writers, "hold": admins, "delete": admins, "add": writers,
                    "minutes": writers},
            draft: {"edit": writers, "hold": admins, "add": writers, "edit action": writers,
                    "delete action": admins, "minutes": writers},
            held: {"conclusions": writers, "close": admins, "add": writers,
                   "edit action": writers, "delete action": admins, "minutes": writers},
            closed: {"edit action": writers, "minutes": writers},
        }
        for (rid, aid), allowed in expected.items():
            page = f"/revisiones-direccion/{rid}"
            item = f"{page}/acciones/{aid}"
            controls = {"edit": f'href="{page}/editar"', "conclusions": f'href="{page}/conclusiones"',
                        "hold": f'action="{page}/celebrar"', "close": f'action="{page}/cerrar"',
                        "delete": f'action="{page}/eliminar"', "add": f'href="{page}/acciones/nueva"',
                        "edit action": f'href="{item}/editar"',
                        "delete action": f'action="{item}/eliminar"',
                        "minutes": f'href="{page}/pdf"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(review=rid, role=role.name, control=name):
                        self.assertEqual(role in allowed.get(name, nobody), marker in body)
        for role in RoleEnum:
            html = self._page(role, "/revisiones-direccion/").get_data(as_text=True)
            with self.subTest(page="list", role=role.name):
                self.assertEqual(role in writers, 'href="/revisiones-direccion/nueva"' in html)

    def test_risk_and_opportunity_controls_follow_role_and_state(self) -> None:
        """Administrators and auditors create and edit records and add, edit and evaluate
        actions (a completed one only), while the record is open; administrators delete
        actions and records without actions (RO6)."""
        from app.services import risks
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            def record(register, titulo, estado=None, action=None):
                found = getattr(risks, register).create(db.session, system, {
                    "titulo": titulo, "descripcion": "d", "origen": "interno",
                    "responsable_id": self.owner_id}, today=DAY).id
                parent = "riesgo_id" if register == "risks" else "oportunidad_id"
                action_id = risks.actions.create(db.session, system, {
                    parent: found, "descripcion": "Acción", "responsable_id": self.owner_id,
                    "fecha_limite": DAY, **action}, today=DAY).id if action is not None else None
                if estado:
                    getattr(risks, register).update(db.session, system, found,
                                                    {"estado": estado}, today=DAY)
                return found, action_id

            done = {"estado": "completada", "fecha_completada": DAY}
            pages = {
                ("/riesgos", "empty"): record("risks", "Sin acciones"),
                ("/riesgos", "pending"): record("risks", "Pendiente", action={}),
                ("/riesgos", "done"): record("risks", "Completada", action=done),
                ("/riesgos", "closed"): record("risks", "Cerrado", "cerrado", action=done),
                ("/oportunidades", "empty"): record("opportunities", "Sin acciones"),
                ("/oportunidades", "done"): record("opportunities", "Completada", action=done),
                ("/oportunidades", "closed"): record("opportunities", "Descartada",
                                                     "descartada", action=done),
            }
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        expected = {
            "empty": {"edit": writers, "delete": admins, "add": writers},
            "pending": {"edit": writers, "add": writers, "edit action": writers,
                        "delete action": admins},
            "done": {"edit": writers, "add": writers, "edit action": writers,
                     "evaluate": writers, "delete action": admins},
            "closed": {"edit": writers, "edit action": writers, "evaluate": writers,
                       "delete action": admins},
        }
        for (base, state), (record_id, action_id) in pages.items():
            page = f"{base}/{record_id}"
            item = f"{page}/acciones/{action_id}"
            controls = {"edit": f'href="{page}/editar"', "delete": f'action="{page}/eliminar"',
                        "add": f'href="{page}/acciones/nueva"',
                        "edit action": f'href="{item}/editar"',
                        "evaluate": f'href="{item}/eficacia"',
                        "delete action": f'action="{item}/eliminar"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(page=page, state=state, role=role.name, control=name):
                        self.assertEqual(role in expected[state].get(name, nobody),
                                         marker in body)
        for base, new in (("/riesgos", "nuevo"), ("/oportunidades", "nueva")):
            empty = pages[base, "empty"][0]
            for role in RoleEnum:
                html = self._page(role, f"{base}/").get_data(as_text=True)
                with self.subTest(page=f"{base}/", role=role.name):
                    self.assertEqual(role in writers, f'href="{base}/{new}"' in html)
                    self.assertEqual(role in writers, f'href="{base}/{empty}/editar"' in html)
                    # Every role reads the register PDF (RO-4).
                    self.assertIn(f'href="{base}/pdf"', html)

    def test_process_and_role_controls_follow_role_and_state(self) -> None:
        """Every role reads processes and roles; administrators and auditors create and
        edit them; administrators delete a process nothing relates to or cites and a role
        without competence requirements or subordinates (PR5 of process-role-registers)."""
        from app.services import process_operations as po
        from app.services import risks
        from app.services import roles_responsibilities as rr
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            def process(nombre, **values):
                return po.create(db.session, system, {"proceso": nombre, **values}).id_proceso

            free, first = process("Libre"), process("Compras")
            related, cited = process("Ventas", relacionados=[first]), process("Almacén")
            risks.risks.create(db.session, system, {
                "titulo": "Rotura de stock", "descripcion": "d", "origen": "interno",
                "responsable_id": self.owner_id, "proceso_id": cited}, today=DAY)
            top = rr.create(db.session, system, {"rol": "Dirección"}).id_rol
            below = rr.create(db.session, system, {"rol": "Calidad", "reporta_a_id": top}).id_rol
            db.session.commit()
            held = CompetenceRequirement.query.one().rol_id  # the seeded requirement's role
            self.assertEqual("Rol", db.session.get(RolResponsabilidad, held).rol)
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        expected = {f"/procesos/{free}": {"edit": writers, "delete": admins},
                    f"/procesos/{related}": {"edit": writers},
                    f"/procesos/{cited}": {"edit": writers},
                    f"/roles/{below}": {"edit": writers, "delete": admins},
                    f"/roles/{top}": {"edit": writers}, f"/roles/{held}": {"edit": writers}}
        for page, allowed in expected.items():
            controls = {"edit": f'href="{page}/editar"', "delete": f'action="{page}/eliminar"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(page=page, role=role.name, control=name):
                        self.assertEqual(role in allowed.get(name, nobody), marker in body)
        for base, record in (("/procesos", free), ("/roles", below)):
            for role in RoleEnum:
                html = self._page(role, f"{base}/").get_data(as_text=True)
                with self.subTest(page=f"{base}/", role=role.name):
                    self.assertEqual(role in writers, f'href="{base}/nuevo"' in html)
                    self.assertEqual(role in writers, f'href="{base}/{record}/editar"' in html)
                    self.assertNotIn("/eliminar", html)

    def test_training_resource_and_audit_indicator_controls_follow_the_role(self) -> None:
        """Every role reads both legacy registers; administrators and auditors create and
        edit; administrators delete, from the record's page (RG-3 of process-role-registers)."""
        from app.services import audit_indicators, training_resources
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            resource = training_resources.create(
                db.session, system, {"recurso_necesario": "Sala"}).id_recurso
            indicator = audit_indicators.create(
                db.session, system, {"area_auditoria": "Ventas"}).id_auditoria
            db.session.commit()
        writers = {ADMIN, AUDITOR}
        for base, record in (("/recursos-formacion", resource),
                             ("/indicadores-auditoria", indicator)):
            for role in RoleEnum:
                listing = self._page(role, f"{base}/").get_data(as_text=True)
                page = self._page(role, f"{base}/{record}").get_data(as_text=True)
                with self.subTest(base=base, role=role.name):
                    self.assertEqual(role in writers, f'href="{base}/nuevo"' in listing)
                    self.assertEqual(role in writers, f'href="{base}/{record}/editar"' in listing)
                    self.assertNotIn("/eliminar", listing)
                    self.assertEqual(role in writers, f'href="{base}/{record}/editar"' in page)
                    self.assertEqual(role == ADMIN, f'action="{base}/{record}/eliminar"' in page)

    def test_provider_controls_follow_role_and_state(self) -> None:
        """Every role reads providers; administrators and auditors create and edit them and
        record and edit evaluations; only administrators delete a provider without
        evaluations or nonconformities, delete evaluations and decide the state, never
        offering the current one (EP2 and EP5 of external-providers)."""
        from app.services import providers
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            def provider(nombre):
                return providers.providers.create(db.session, system, {
                    "nombre": nombre, "tipo_suministro": "producto"}).id

            free, evaluated = provider("Pendiente"), provider("Aprobado")
            evaluation = providers.evaluations.create(db.session, system, {
                "proveedor_id": evaluated, "fecha": DAY, "evaluador_id": self.owner_id,
                "calidad": 4, "plazo": 4, "servicio": 4, "documentacion": 4}, today=DAY).id
            providers.approve(db.session, system, evaluated, today=DAY)
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        base = "/proveedores"
        item = f"{base}/{evaluated}/evaluaciones/{evaluation}"
        expected = {
            f"{base}/{free}": {"edit": writers, "delete": admins, "evaluate": writers,
                               "aprobar": admins, "condicionar": admins, "bloquear": admins},
            f"{base}/{evaluated}": {"edit": writers, "evaluate": writers, "condicionar": admins,
                                    "bloquear": admins, "edit evaluation": writers,
                                    "delete evaluation": admins},
        }
        for page, allowed in expected.items():
            controls = {"edit": f'href="{page}/editar"', "delete": f'action="{page}/eliminar"',
                        "evaluate": f'href="{page}/evaluaciones/nueva"',
                        **{name: f'action="{page}/{name}"'
                           for name in ("aprobar", "condicionar", "bloquear")},
                        "edit evaluation": f'href="{item}/editar"',
                        "delete evaluation": f'action="{item}/eliminar"'}
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for name, marker in controls.items():
                    with self.subTest(page=page, role=role.name, control=name):
                        self.assertEqual(role in allowed.get(name, nobody), marker in body)
        for role in RoleEnum:
            html = self._page(role, f"{base}/").get_data(as_text=True)
            with self.subTest(page=f"{base}/", role=role.name):
                self.assertEqual(role in writers, f'href="{base}/nuevo"' in html)
                self.assertEqual(role in writers, f'href="{base}/{free}/editar"' in html)
                self.assertNotIn("/eliminar", html)
                # Every role reads the approved-providers PDF (EP-3).
                self.assertIn(f'href="{base}/pdf"', html)

    def _check_controls(self, expected: dict) -> None:
        """Each page's ``{marker: roles}``: exactly those roles see the marker."""
        for page, controls in expected.items():
            for role in RoleEnum:
                body = self._page(role, page).get_data(as_text=True)
                for marker, roles in controls.items():
                    with self.subTest(page=page, role=role.name, control=marker):
                        self.assertEqual(role in roles, marker in body)

    def test_context_issue_controls_follow_the_role(self) -> None:
        """Every role reads context issues; administrators and auditors create and edit
        them; only administrators delete one no risk names (OC4 of organization-context)."""
        from app.services import context_issues
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            issue = context_issues.issues.create(db.session, system, {
                "tipo": "externa", "categoria": "legal", "titulo": "Normativa",
                "descripcion": "d", "efecto": "neutro", "pertinente": True,
                "fecha_identificacion": DAY}, today=DAY).id
            db.session.commit()
        writers, admins = {ADMIN, AUDITOR}, {ADMIN}
        self._check_controls({
            "/contexto/": {'href="/contexto/nueva"': writers,
                           f'href="/contexto/{issue}/editar"': writers},
            f"/contexto/{issue}": {f'href="/contexto/{issue}/editar"': writers,
                                   f'action="/contexto/{issue}/eliminar"': admins},
        })

    def test_scope_controls_follow_role_and_state(self) -> None:
        """Every role reads the scope; administrators and auditors start, edit and delete
        drafts; only administrators approve, so OPERATIVO and AUDITOR never see «Aprobar»;
        an approved version offers no draft action (OC4 of organization-context)."""
        from app.services import qms_scope
        from app.services.actor import Actor

        system = Actor(None, "seed", ADMIN, "system")
        with self.app.app_context():
            draft = qms_scope.new_draft(db.session, system, {"declaracion": "Alcance"}).id
            db.session.get(Person, self.owner_id).user_id = self.ids[ADMIN]  # the approver
            db.session.commit()
        writers, admins, nobody = {ADMIN, AUDITOR}, {ADMIN}, set()
        draft_page = f"/alcance/{draft}"
        self._check_controls({
            "/alcance/": {f'href="{draft_page}"': EVERYONE, 'href="/alcance/nuevo"': nobody},
            draft_page: {f'href="{draft_page}/editar"': writers,
                         f'action="{draft_page}/eliminar"': writers,
                         f'action="{draft_page}/aprobar"': admins, ">Aprobar<": admins},
        })
        with self.app.app_context():
            qms_scope.approve(db.session, Actor(self.ids[ADMIN], "admin", ADMIN, "web"), draft,
                              today=DAY)
            db.session.commit()
        self._check_controls({
            "/alcance/": {'href="/alcance/nuevo"': writers, ">Aprobar<": nobody},
            draft_page: {f'href="{draft_page}/editar"': nobody,
                         f'action="{draft_page}/eliminar"': nobody,
                         f'action="{draft_page}/aprobar"': nobody},
        })

    def test_navigation_links_follow_the_read_permission(self) -> None:
        expected = {"/auditorias/": {ADMIN, AUDITOR}, "/programas-auditoria/": {ADMIN, AUDITOR},
                    "/documents/": set(RoleEnum), "/no_conformidades/": set(RoleEnum),
                    "/usuarios/": {ADMIN, AUDITOR}, "/perfil/": set(RoleEnum),
                    "/personas/": set(RoleEnum), "/competencias/requisitos/": set(RoleEnum),
                    "/competencias/matriz": set(RoleEnum),
                    "/objetivos-calidad/": set(RoleEnum),
                    "/riesgos/": set(RoleEnum), "/oportunidades/": set(RoleEnum),
                    "/revisiones-direccion/": {ADMIN, AUDITOR},
                    "/procesos/": set(RoleEnum), "/roles/": set(RoleEnum),
                    "/recursos-formacion/": set(RoleEnum),
                    "/indicadores-auditoria/": set(RoleEnum),
                    "/proveedores/": set(RoleEnum), "/contexto/": set(RoleEnum),
                    "/partes_interesadas/": set(RoleEnum), "/alcance/": set(RoleEnum)}
        for role in RoleEnum:
            html = self._page(role, "/dashboard/").get_data(as_text=True)
            self.assertIn('class="nav__group">Personas y competencia<', html)
            # Clauses 4.1 to 4.3 share one group, in clause order (OC-3).
            group = html[html.index('class="nav__group">Contexto<'):
                         html.index('class="nav__group">Calidad<')]
            self.assertEqual(["/contexto/", "/partes_interesadas/", "/alcance/"],
                             re.findall(r'class="nav__link" href="([^"]+)"', group))
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
