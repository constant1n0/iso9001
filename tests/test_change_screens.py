"""QMS change screens (CP-3 of ``change-planning``).

Through the web (ISO 9001 clause 6.3, decisions CP1 to CP5): a change is
proposed from an origin and, optionally, the record behind it (review actions
of type ``cambio_sgc`` and context issues in force only, and only a record of
the chosen origin), with its purpose, the four 6.3 considerations, the
communication, an owner, its dates, processes and documents. The form shows
only what the state lets change: everything while proposed; owner, planned
date, communication, processes and documents once approved; nothing after.
Its page shows the plan, the documents the role may read, the tasks with an
overdue badge, each stage's data and the transitions the guards allow:
administrators approve and cancel, administrators and auditors record the
implementation and review the effectiveness; every state change is a POST that
re-checks its guard. Tasks are written under their change while it is open.
The list filters by state, origin, owner, process, delay, overdue tasks and
text, and pages. Every role reads; administrators and auditors write;
administrators delete a proposed change. Every test runs with the audit flush
guard.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_delete_forms import _PostForms
from test_process_role_screens import RegisterScreensBase

from app.extensions import db
from app.models import (
    CambioSGC, Document, DocumentCategory, DocumentRevision, EstadoCambio, EstadoRevision,
    Person, RoleEnum, TareaCambio,
)
from app.services import context_issues, management_reviews, qms_changes, risks
from app.services.actor import Actor

BASE = "/cambios"
TODAY = date(2026, 10, 23)
ROUTES = ("app.routes.change_routes",)
WRITERS, ADMINS = {ADMIN, AUDITOR}, {ADMIN}
# What a change is proposed with through the service, and the plan approval needs.
CHANGE = {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos",
          "origen": "otro", "proposito": "Trazabilidad de los pedidos",
          "fecha_propuesta": date(2026, 10, 1)}
PLAN = {"consecuencias": "Formación del equipo comercial",
        "integridad": "Ambos sistemas conviven un mes", "recursos": "Licencias y consultora",
        "responsabilidades": "Dirección comercial", "fecha_prevista": date(2026, 12, 31)}
# The web forms.
CHANGE_FORM = {"titulo": "Portal de proveedores", "descripcion": "Pedidos a proveedores en línea",
               "origen": "otro", "registro_origen": "", "proposito": "Menos errores de pedido",
               "consecuencias": "", "integridad": "", "recursos": "", "responsabilidades": "",
               "comunicacion": "", "fecha_propuesta": "2026-10-20", "fecha_prevista": ""}
TASK_FORM = {"descripcion": "Configurar el portal", "fecha_limite": "2026-11-30",
             "estado": "pendiente", "fecha_completada": "", "resultado": ""}


class ChangeScreensBase(RegisterScreensBase):
    """Today pinned in the routes; Luis is the auditor's person, Ana the administrator's."""

    routes = ROUTES

    def setUp(self) -> None:
        super().setUp()
        for module in self.routes:
            patcher = patch(f"{module}.local_today", return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.app.app_context():
            db.session.execute(Person.__table__.update().where(Person.id == self.luis)
                               .values(user_id=self.ids[AUDITOR]))
            db.session.commit()

    def web(self, role: RoleEnum = ADMIN) -> Actor:
        return Actor(self.ids[role], role.name.lower(), role, "web")

    def change(self, titulo: str = "Nuevo ERP", **values) -> int:
        """A proposed change owned by Eva, through the service."""
        with self.app.app_context():
            found = qms_changes.changes.create(db.session, SYSTEM, CHANGE | {
                "titulo": titulo, "responsable_id": self.eva} | values, today=TODAY)
            db.session.commit()
            return found.id

    def task(self, change_id: int, **values) -> int:
        with self.app.app_context():
            found = qms_changes.tasks.create(db.session, SYSTEM, {
                "cambio_id": change_id, "descripcion": "Migrar los pedidos abiertos",
                "responsable_id": self.luis, "fecha_limite": date(2026, 11, 30)} | values,
                today=TODAY)
            db.session.commit()
            return found.id

    def approved(self, titulo: str = "Nuevo ERP", **values) -> int:
        """A change with its plan and one pending task, approved by Ana."""
        change = self.change(titulo, **(PLAN | values))
        self.task(change)
        with self.app.app_context():
            qms_changes.approve(db.session, self.web(ADMIN), change, today=TODAY)
            db.session.commit()
        return change

    def implemented(self, titulo: str = "Nuevo ERP", **values) -> int:
        change = self.approved(titulo, **values)
        with self.app.app_context():
            for task in qms_changes.tasks.list_(db.session, SYSTEM, cambio_id=change):
                qms_changes.tasks.update(db.session, SYSTEM, task.id, {
                    "estado": "completada", "fecha_completada": TODAY}, today=TODAY)
            qms_changes.implement(db.session, self.web(ADMIN), change, fecha=TODAY, today=TODAY)
            db.session.commit()
        return change

    def reviewed(self, titulo: str = "Nuevo ERP") -> int:
        change = self.implemented(titulo)
        with self.app.app_context():
            qms_changes.review_effectiveness(db.session, self.web(ADMIN), change,
                                             eficacia="no_eficaz", fecha=TODAY, today=TODAY,
                                             comentario="Siguen los pedidos en papel")
            db.session.commit()
        return change

    def cancelled(self, titulo: str = "Nuevo ERP") -> int:
        change = self.change(titulo)
        with self.app.app_context():
            qms_changes.cancel(db.session, self.web(ADMIN), change, motivo="Sin presupuesto",
                               today=TODAY)
            db.session.commit()
        return change

    def stored(self, change_id: int) -> CambioSGC:
        """The change as stored (call inside an app context)."""
        db.session.expire_all()
        return db.session.get(CambioSGC, change_id)

    def state(self, change_id: int) -> EstadoCambio:
        with self.app.app_context():
            return self.stored(change_id).estado

    def document(self, code: str, in_force: bool = True) -> int:
        """A document; one not in force is a draft only administrators and auditors read."""
        with self.app.app_context():
            document_id = db.session.execute(Document.__table__.insert().values(
                title=f"Procedimiento {code}", code=code, category=DocumentCategory.OTRO)
            ).inserted_primary_key[0]
            db.session.execute(DocumentRevision.__table__.insert().values(
                document_id=document_id, numero=1, content="c",
                estado=EstadoRevision.vigente if in_force else EstadoRevision.borrador,
                effective_from=TODAY if in_force else None, author_id=self.ana))
            db.session.commit()
            return document_id

    def origin_records(self) -> dict[str, int]:
        """A review with a ``cambio_sgc`` action and a ``mejora`` one, an issue in force
        and a superseded one, a risk; keyed by what they are."""
        with self.app.app_context():
            review = management_reviews.reviews.create(db.session, SYSTEM, {
                "fecha": date(2026, 7, 10), "periodo_desde": date(2026, 4, 1),
                "periodo_hasta": date(2026, 6, 30), "asistente_ids": [self.ana]})
            db.session.flush()
            ids = {"review": review.id}
            for key, tipo, text in (("action", "cambio_sgc", "Implantar un ERP"),
                                    ("improvement", "mejora", "Mejorar la acogida")):
                ids[key] = management_reviews.actions.create(db.session, SYSTEM, {
                    "revision_id": review.id, "tipo": tipo, "descripcion": text,
                    "responsable_id": self.eva, "fecha_limite": date(2026, 12, 31),
                    "estado": "pendiente"}, today=TODAY).id
            for key, titulo, estado in (("issue", "Saber disperso", "vigente"),
                                        ("superseded", "Normativa derogada", "superada")):
                ids[key] = context_issues.issues.create(db.session, SYSTEM, {
                    "tipo": "interna", "categoria": "conocimiento", "titulo": titulo,
                    "descripcion": "d", "efecto": "negativo", "pertinente": True,
                    "fecha_identificacion": date(2026, 9, 5), "estado": estado},
                    today=TODAY).id
            ids["risk"] = risks.risks.create(db.session, SYSTEM, {
                "titulo": "Pérdida de pedidos", "descripcion": "Hoja compartida",
                "origen": "interno", "responsable_id": self.eva}, today=TODAY).id
            db.session.commit()
            return ids

    def form_of(self, **values) -> dict:
        return CHANGE_FORM | {"responsable_id": str(self.eva)} | values

    def created(self, data: dict) -> int:
        response = self.post(f"{BASE}/nuevo", data)
        match = re.fullmatch(rf"{BASE}/(\d+)", response.headers["Location"])
        self.assertIsNotNone(match, response.headers["Location"])
        return int(match.group(1))

    @staticmethod
    def field_html(html: str, name: str) -> str:
        """The ``field`` block holding the input named ``name``."""
        start = r'<div class="field[ "]'
        match = re.search(rf'{start}[^>]*>(?:(?!{start}).)*?name="{name}"(?:(?!{start}).)*',
                          html, re.S)
        assert match is not None, f"no field {name}"
        return match.group(0)

    @staticmethod
    def names(html: str) -> list[str]:
        """The names of a form's inputs, selects and textareas, in order."""
        return [name for name in re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"',
                                            html) if name != "csrf_token"]

    def listed(self, url: str) -> list[int]:
        return list(self.rows(url))


class ChangeListTestCase(ChangeScreensBase):
    def test_the_list_shows_origin_owner_state_plan_and_open_tasks(self) -> None:
        records = self.origin_records()
        late = self.approved("ERP con retraso", origen="riesgo", riesgo_id=records["risk"],
                             fecha_prevista=TODAY - timedelta(days=1))
        self.task(late, fecha_limite=TODAY - timedelta(days=3))
        proposed = self.change("Firma digital", fecha_propuesta=date(2026, 10, 5))
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                html = self.html(f"{BASE}/")
                self.assertEqual(role in WRITERS, f'href="{BASE}/nuevo"' in html)
                rows = self.rows(f"{BASE}/")
                self.assertEqual([proposed, late], list(rows))  # the latest proposed first
                self.assertEqual(["ERP con retraso", "Riesgo Pérdida de pedidos", "Eva Ruiz",
                                  "Aprobado", "22/10/2026 Fuera de plazo", "2"],
                                 rows[late][:6])
                self.assertEqual(["Firma digital", "Otro", "Eva Ruiz", "Propuesto", "—", "0"],
                                 rows[proposed][:6])
                self.assertIn(f'href="/riesgos/{records["risk"]}"', html)
        self.assertIn('class="nav__link" href="/cambios/"', self.html("/dashboard/"))

    def test_the_list_filters_and_ignores_values_naming_nothing(self) -> None:
        compras = self.process("Compras")
        late = self.approved("ERP con retraso", fecha_prevista=TODAY - timedelta(days=1),
                             procesos=[compras])
        overdue = self.change("Firma digital", responsable_id=self.luis,
                              fecha_propuesta=date(2026, 10, 5))
        self.task(overdue, fecha_limite=TODAY - timedelta(days=1))
        quiet = self.change("Portal de clientes", origen="riesgo",
                            descripcion="Pedidos en línea sin ERP",
                            fecha_propuesta=date(2026, 10, 2))
        self.login(OPERATIVO)
        every = [overdue, quiet, late]
        cases = {"": every, "?estado=aprobado": [late], "?estado=propuesto": [overdue, quiet],
                 "?origen=riesgo": [quiet], f"?responsable_id={self.luis}": [overdue],
                 f"?proceso_id={compras}": [late], "?fuera_de_plazo=1": [late],
                 "?fuera_de_plazo=0": [overdue, quiet], "?tareas_vencidas=1": [overdue],
                 "?texto=erp": [quiet, late], "?texto=+ERP+&estado=aprobado": [late],
                 "?estado=nada&origen=nada&responsable_id=999&fuera_de_plazo=quizas": every}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, self.listed(f"{BASE}/{query}"))
        self.assertIn("2 activos", self.html(f"{BASE}/?texto=erp&estado=aprobado"))
        owners = self.select(self.html(f"{BASE}/"), "responsable_id")
        self.assertIn(">Luis Gil</option>", owners)
        self.assertNotIn("Inés Mora", owners)  # only owners filter
        self.assertIn('value="erp"', self.html(f"{BASE}/?texto=erp"))
        self.assertIn("Ningún cambio coincide con los filtros.",
                      self.html(f"{BASE}/?estado=cancelado"))

    def test_the_list_pages_and_keeps_the_filters(self) -> None:
        made = [self.change(f"Cambio {n}", fecha_propuesta=date(2026, 10, n))
                for n in range(1, 6)]
        self.login(AUDITOR)
        with patch("app.routes.change_routes.PER_PAGE", 2):
            self.assertEqual([made[4], made[3]], self.listed(f"{BASE}/"))
            html = self.html(f"{BASE}/?page=2&estado=propuesto")
            self.assertEqual([made[2], made[1]], self.listed(f"{BASE}/?page=2&estado=propuesto"))
            self.assertIn("Página 2 de 3", html)
            for page in (1, 3):
                self.assertRegex(html, rf'href="/cambios/\?[^"]*page={page}[^"]*"')
            self.assertIn("estado=propuesto", re.search(r'href="(/cambios/\?[^"]*page=3[^"]*)"',
                                                        html).group(1))
            self.assertEqual([made[0]], self.listed(f"{BASE}/?page=3"))
        self.assertNotIn("Página", self.html(f"{BASE}/"))


class ChangePageTestCase(ChangeScreensBase):
    def test_the_page_shows_the_plan_considerations_lists_tasks_and_stages(self) -> None:
        records = self.origin_records()
        compras = self.process("Compras")
        manual, draft = self.document("PR-01"), self.document("PR-02", in_force=False)
        change = self.approved("ERP", origen="cuestion_contexto", cuestion_id=records["issue"],
                               comunicacion="Reunión con comerciales", procesos=[compras],
                               documentos=[manual, draft])
        late = self.task(change, descripcion="Formar al equipo",
                         fecha_limite=TODAY - timedelta(days=2))
        self.login(OPERATIVO)
        page = self.html(f"{BASE}/{change}")
        plan = self.text(self.block(page, "plan"))
        for marker in ("Cuestión del contexto Saber disperso", "Eva Ruiz", "01/10/2026",
                       "31/12/2026", "Reunión con comerciales", "Sustituir la hoja de pedidos"):
            self.assertIn(marker, plan)
        self.assertIn(f'href="/contexto/{records["issue"]}"', self.block(page, "plan"))
        considerations = self.text(self.block(page, "consideraciones"))
        for marker in ("a) Propósito y consecuencias potenciales", "Trazabilidad de los pedidos",
                       "Formación del equipo comercial", "b) Integridad del SGC",
                       "Ambos sistemas conviven un mes", "c) Disponibilidad de recursos",
                       "Licencias y consultora", "d) Responsabilidades y autoridades",
                       "Dirección comercial"):
            self.assertIn(marker, considerations)
        self.assertIn(f'href="/procesos/{compras}">Compras</a>', self.block(page, "procesos"))
        documents = self.block(page, "documentos")
        self.assertIn(f'href="/documents/{manual}"', documents)
        self.assertNotIn("PR-02", documents)  # a draft: OPERATIVO does not read it
        self.login(AUDITOR)
        self.assertIn(f'href="/documents/{draft}"', self.block(self.html(f"{BASE}/{change}"),
                                                                  "documentos"))
        tasks = self.block(page, "tareas")
        self.assertIn("Vencida", re.search(rf'<tr id="tarea-{late}">.*?</tr>', tasks, re.S)
                      .group(0))
        self.assertIn("Luis Gil", tasks)
        stages = self.text(self.block(page, "ciclo"))
        for marker in ("Aprobado por Ana Pérez", "23/10/2026"):
            self.assertIn(marker, stages)
        reviewed = self.reviewed("Revisado")
        stages = self.text(self.block(self.html(f"{BASE}/{reviewed}"), "ciclo"))
        for marker in ("No eficaz", "Siguen los pedidos en papel", "Revisada por Ana Pérez"):
            self.assertIn(marker, stages)
        cancelled = self.cancelled("Cancelado")
        self.assertIn("Sin presupuesto", self.text(self.block(self.html(f"{BASE}/{cancelled}"),
                                                              "ciclo")))

    def test_the_buttons_follow_the_role_and_the_state(self) -> None:
        proposed, approved = self.change("Propuesto"), self.approved("Aprobado")
        implemented, reviewed = self.implemented("Implantado"), self.reviewed("Revisado")
        cancelled = self.cancelled("Cancelado")
        nobody = set()
        expected = {
            proposed: {"/editar": WRITERS, "/eliminar": ADMINS, "/aprobar": ADMINS,
                       "/implantar": nobody, "/eficacia": nobody, "/cancelar": ADMINS,
                       "/tareas/nueva": WRITERS},
            approved: {"/editar": WRITERS, "/eliminar": nobody, "/aprobar": nobody,
                       "/implantar": WRITERS, "/eficacia": nobody, "/cancelar": ADMINS,
                       "/tareas/nueva": WRITERS},
            implemented: {"/editar": nobody, "/eliminar": nobody, "/implantar": nobody,
                          "/eficacia": WRITERS, "/cancelar": nobody, "/tareas/nueva": nobody},
            reviewed: {"/editar": nobody, "/eficacia": nobody, "/cancelar": nobody},
            cancelled: {"/editar": nobody, "/eliminar": nobody, "/aprobar": nobody,
                        "/tareas/nueva": nobody},
        }
        for role in RoleEnum:
            self.login(role)
            for change, controls in expected.items():
                page = self.html(f"{BASE}/{change}")
                for suffix, roles in controls.items():
                    with self.subTest(role=role.name, change=change, control=suffix):
                        self.assertEqual(role in roles, f'"{BASE}/{change}{suffix}"' in page)
        self.login(ADMIN)
        forms = _PostForms()
        forms.feed(self.html(f"{BASE}/{proposed}"))
        delete = next(f for f in forms.forms if f["action"] == f"{BASE}/{proposed}/eliminar")
        self.assertIn("csrf_token", delete["fields"])
        self.assertRegex(self.html(f"{BASE}/{proposed}"),
                         rf'action="{BASE}/{proposed}/eliminar" method="post" data-confirm=')

    def test_an_unknown_change_or_a_task_of_another_change_is_not_found(self) -> None:
        change, other = self.change(), self.change("Otro")
        task = self.task(other)
        self.login(ADMIN)
        self.assertEqual(404, self.client.get(f"{BASE}/9999").status_code)
        self.assertEqual(404, self.client.get(f"{BASE}/{change}/tareas/{task}/editar").status_code)
        self.assertEqual(404, self.client.post(f"{BASE}/{change}/tareas/{task}/eliminar")
                         .status_code)


class ChangeFormTestCase(ChangeScreensBase):
    def test_a_change_is_proposed_with_its_origin_record_processes_and_documents(self) -> None:
        records = self.origin_records()
        compras, manual = self.process("Compras"), self.document("PR-01")
        self.login(AUDITOR)
        form = self.html(f"{BASE}/nuevo")
        self.assertIn('value="2026-10-23"', self.field_html(form, "fecha_propuesta"))
        picker = self.select(form, "registro_origen")
        self.assertIn('<option selected value="">— Sin registro de origen —</option>', picker)
        self.assertIn('<optgroup label="Revisión por la dirección">', picker)
        self.assertIn(f'value="accion_revision_id:{records["action"]}"', picker)
        self.assertNotIn(f'accion_revision_id:{records["improvement"]}', picker)  # not cambio_sgc
        self.assertIn(f'value="cuestion_id:{records["issue"]}"', picker)
        self.assertNotIn(f'cuestion_id:{records["superseded"]}', picker)  # superseded
        self.assertIn(f'value="riesgo_id:{records["risk"]}">Pérdida de pedidos</option>', picker)
        self.assertIn(">Compras</option>", self.select(form, "procesos"))
        self.assertIn(">PR-01 · Procedimiento PR-01</option>", self.select(form, "documentos"))
        change = self.created(self.form_of(
            origen="revision_direccion", registro_origen=f"accion_revision_id:{records['action']}",
            consecuencias="Ninguna grave", procesos=[str(compras)], documentos=[str(manual)]))
        self.assertIn(("success", "Cambio «Portal de proveedores» propuesto."), self.flashes())
        with self.app.app_context():
            found = self.stored(change)
            self.assertEqual(
                (EstadoCambio.propuesto, "revision_direccion", records["action"], None,
                 "Ninguna grave", None, self.eva, date(2026, 10, 20), [compras], [manual]),
                (found.estado, found.origen.name, found.accion_revision_id, found.riesgo_id,
                 found.consecuencias, found.integridad, found.responsable_id,
                 found.fecha_propuesta, found.proceso_ids, found.documento_ids))
        # Another origin's record is refused on its field, the input kept.
        page = self.rerendered(f"{BASE}/nuevo", self.form_of(
            origen="auditoria", registro_origen=f"riesgo_id:{records['risk']}",
            titulo="Otro cambio"))
        self.assertIn("El registro elegido no corresponde al origen «Auditoría»",
                      self.field_html(page, "registro_origen"))
        self.assertIn('value="Otro cambio"', page)
        # The service's refusals show on their field.
        page = self.rerendered(f"{BASE}/nuevo", self.form_of(fecha_propuesta="2026-10-24"))
        self.assertIn(qms_changes.PROPOSED_IN_FUTURE, self.field_html(page, "fecha_propuesta"))
        page = self.rerendered(f"{BASE}/nuevo", self.form_of(fecha_prevista="2026-10-01"))
        self.assertIn(qms_changes.PLANNED_BEFORE_PROPOSAL, self.field_html(page, "fecha_prevista"))
        with self.app.app_context():
            self.assertEqual(1, db.session.query(CambioSGC).count())

    def test_the_review_action_and_its_origin_can_be_preselected(self) -> None:
        records = self.origin_records()
        self.login(ADMIN)
        form = self.html(f"{BASE}/nuevo?origen=revision_direccion"
                         f"&accion_revision_id={records['action']}")
        self.assertIn('<option selected value="revision_direccion">',
                      self.select(form, "origen"))
        self.assertIn(f'<option selected value="accion_revision_id:{records["action"]}">',
                      self.select(form, "registro_origen"))
        # A record the picker does not offer is not preselected.
        form = self.html(f"{BASE}/nuevo?accion_revision_id={records['improvement']}")
        self.assertIn('<option selected value="">', self.select(form, "registro_origen"))

    def test_the_form_shows_only_what_the_state_lets_change(self) -> None:
        records = self.origin_records()
        proposed = self.change(origen="riesgo", riesgo_id=records["risk"])
        approved = self.approved("Aprobado")
        self.login(AUDITOR)
        form = self.html(f"{BASE}/{proposed}/editar")
        self.assertEqual(["titulo", "descripcion", "origen", "registro_origen", "proposito",
                          "consecuencias", "integridad", "recursos", "responsabilidades",
                          "comunicacion", "responsable_id", "fecha_propuesta", "fecha_prevista",
                          "procesos", "documentos"], self.names(form))
        self.assertIn(f'<option selected value="riesgo_id:{records["risk"]}">',
                      self.select(form, "registro_origen"))
        # Changing the origin clears the old link (the form posts every link).
        self.post(f"{BASE}/{proposed}/editar", self.form_of(
            titulo="Nuevo ERP", origen="cuestion_contexto",
            registro_origen=f"cuestion_id:{records['issue']}"), expect=f"{BASE}/{proposed}")
        with self.app.app_context():
            found = self.stored(proposed)
            self.assertEqual((None, records["issue"]), (found.riesgo_id, found.cuestion_id))
        form = self.html(f"{BASE}/{approved}/editar")
        self.assertEqual(["comunicacion", "responsable_id", "fecha_prevista", "procesos",
                          "documentos"], self.names(form))
        compras = self.process("Compras")
        self.post(f"{BASE}/{approved}/editar", {
            "titulo": "No se cambia", "comunicacion": "Correo a todos",
            "responsable_id": str(self.ines), "fecha_prevista": "2027-01-31",
            "procesos": [str(compras)]}, expect=f"{BASE}/{approved}")
        with self.app.app_context():
            found = self.stored(approved)
            self.assertEqual(("Aprobado", "Correo a todos", self.ines, date(2027, 1, 31),
                              [compras]),
                             (found.titulo, found.comunicacion, found.responsable_id,
                              found.fecha_prevista, found.proceso_ids))
        page = self.rerendered(f"{BASE}/{approved}/editar", {
            "comunicacion": "Correo", "responsable_id": str(self.ines), "fecha_prevista": ""})
        self.assertIn(qms_changes.PLANNED_DATE_KEPT, self.field_html(page, "fecha_prevista"))
        for make in (self.implemented, self.reviewed, self.cancelled):
            closed = make(make.__name__)
            with self.subTest(state=make.__name__):
                for method in ("get", "post"):
                    response = getattr(self.client, method)(f"{BASE}/{closed}/editar", data={})
                    self.assertEqual(f"{BASE}/{closed}", response.headers["Location"])
                    self.assertIn(("danger", qms_changes.READ_ONLY[self.state(closed)]),
                                  self.flashes())

    def test_only_administrators_delete_and_only_a_proposed_change(self) -> None:
        proposed, approved = self.change("Propuesto"), self.approved("Aprobado")
        self.task(proposed)
        for role in (AUDITOR, OPERATIVO):
            self.login(role)
            self.denied("post", f"{BASE}/{proposed}/eliminar")
        self.login(ADMIN)
        self.post(f"{BASE}/{approved}/eliminar", expect=f"{BASE}/{approved}")
        self.assertIn(("danger", qms_changes.ONLY_PROPOSED_DELETED), self.flashes())
        self.post(f"{BASE}/{proposed}/eliminar", expect=f"{BASE}/")
        self.assertIn(("success", "Cambio eliminado."), self.flashes())
        with self.app.app_context():
            self.assertEqual([approved], [c.id for c in db.session.query(CambioSGC)])
            self.assertEqual(1, db.session.query(TareaCambio).count())  # the approved one's

    def test_the_forms_are_for_writers_only(self) -> None:
        change = self.change()
        self.login(OPERATIVO)
        for url in (f"{BASE}/nuevo", f"{BASE}/{change}/editar", f"{BASE}/{change}/tareas/nueva"):
            with self.subTest(url=url):
                self.denied("get", url)


class TransitionTestCase(ChangeScreensBase):
    def test_a_change_is_approved_implemented_and_reviewed_through_its_page(self) -> None:
        change = self.change(**PLAN)
        task = self.task(change)
        self.login(ADMIN)
        self.post(f"{BASE}/{change}/aprobar", expect=f"{BASE}/{change}")
        self.assertIn(("success", "Cambio aprobado."), self.flashes())
        with self.app.app_context():
            found = self.stored(change)
            self.assertEqual((EstadoCambio.aprobado, self.ana, TODAY),
                             (found.estado, found.aprobado_por_id, found.fecha_aprobacion))
        self.login(AUDITOR)
        page = self.html(f"{BASE}/{change}")
        self.assertIn('value="2026-10-23"', self.field_html(page, "fecha_implantacion"))
        self.post(f"{BASE}/{change}/implantar", {"fecha_implantacion": "2026-10-23"},
                  expect=f"{BASE}/{change}")
        self.assertIn(("danger", qms_changes.TASKS_OPEN), self.flashes())
        self.post(f"{BASE}/{change}/tareas/{task}/editar", TASK_FORM | {
            "responsable_id": str(self.luis), "estado": "completada",
            "fecha_completada": "2026-10-22", "resultado": "Migrados"},
            expect=f"{BASE}/{change}")
        self.post(f"{BASE}/{change}/implantar", {"fecha_implantacion": "2026-10-23"},
                  expect=f"{BASE}/{change}")
        self.assertIn(("success", "Implantación del cambio registrada."), self.flashes())
        self.post(f"{BASE}/{change}/eficacia", {"eficacia": "no_eficaz",
                                                 "fecha_eficacia": "2026-10-23",
                                                 "comentario_eficacia": ""},
                  expect=f"{BASE}/{change}")
        self.assertIn(("danger", qms_changes.COMMENT_REQUIRED), self.flashes())
        self.post(f"{BASE}/{change}/eficacia", {"eficacia": "eficaz",
                                                 "fecha_eficacia": "2026-10-23",
                                                 "comentario_eficacia": "Pedidos trazados"},
                  expect=f"{BASE}/{change}")
        self.assertIn(("success", "Eficacia del cambio revisada."), self.flashes())
        with self.app.app_context():
            found = self.stored(change)
            self.assertEqual((EstadoCambio.eficacia_revisada, TODAY, "eficaz", self.luis),
                             (found.estado, found.fecha_implantacion, found.eficacia.name,
                              found.revisor_eficacia_id))

    def test_each_transition_refuses_a_role_its_guard_does_not_allow(self) -> None:
        proposed, approved = self.change(**PLAN), self.approved("Aprobado")
        implemented = self.implemented("Implantado")
        self.task(proposed)
        refused = {AUDITOR: [(proposed, "aprobar", {}), (approved, "cancelar",
                                                         {"motivo_cancelacion": "No"})],
                   OPERATIVO: [(proposed, "aprobar", {}),
                               (approved, "implantar", {"fecha_implantacion": "2026-10-23"}),
                               (implemented, "eficacia", {"eficacia": "eficaz",
                                                          "fecha_eficacia": "2026-10-23"}),
                               (approved, "cancelar", {"motivo_cancelacion": "No"})]}
        for role, posts in refused.items():
            self.login(role)
            for change, path, data in posts:
                with self.subTest(role=role.name, path=path):
                    response = self.client.post(f"{BASE}/{change}/{path}", data=data)
                    self.assertTrue(response.headers["Location"].endswith("/dashboard/"))
                    self.assertIn(("danger", "No tienes permiso para acceder a esta página."),
                                  self.flashes())
        self.assertEqual((EstadoCambio.propuesto, EstadoCambio.aprobado, EstadoCambio.implantado),
                         (self.state(proposed), self.state(approved), self.state(implemented)))

    def test_the_service_refusals_are_flashed_on_the_change_page(self) -> None:
        bare = self.change("Sin plan")
        approved = self.approved("Aprobado", responsable_id=self.luis)
        implemented = self.implemented("Implantado", responsable_id=self.luis)
        self.login(ADMIN)
        self.post(f"{BASE}/{bare}/aprobar", expect=f"{BASE}/{bare}")
        (category, message), = [f for f in self.flashes() if f[0] == "danger"]
        self.assertTrue(message.startswith("Para aprobar el cambio falta: "), message)
        self.post(f"{BASE}/{approved}/aprobar", expect=f"{BASE}/{approved}")
        self.assertIn(("danger", qms_changes.NOT_PROPOSED), self.flashes())
        self.post(f"{BASE}/{approved}/cancelar", {"motivo_cancelacion": ""},
                  expect=f"{BASE}/{approved}")
        self.assertIn(("danger", "Motivo de la cancelación: Este campo es obligatorio."),
                      self.flashes())
        self.post(f"{BASE}/{approved}/implantar", {"fecha_implantacion": "2026-10-24"},
                  expect=f"{BASE}/{approved}")
        self.assertIn(("danger", qms_changes.TASKS_OPEN), self.flashes())
        self.login(AUDITOR)  # Luis owns the implemented change
        self.post(f"{BASE}/{implemented}/eficacia", {"eficacia": "eficaz",
                                                      "fecha_eficacia": "2026-10-23"},
                  expect=f"{BASE}/{implemented}")
        self.assertIn(("danger", qms_changes.REVIEWER_IS_OWNER), self.flashes())
        self.login(ADMIN)
        self.post(f"{BASE}/{approved}/cancelar", {"motivo_cancelacion": "Sin presupuesto"},
                  expect=f"{BASE}/{approved}")
        self.assertIn(("success", "Cambio cancelado."), self.flashes())
        self.assertEqual(EstadoCambio.cancelado, self.state(approved))


class TaskScreensTestCase(ChangeScreensBase):
    def test_tasks_are_added_followed_up_and_deleted_under_an_open_change(self) -> None:
        change = self.change()
        self.login(AUDITOR)
        form = self.html(f"{BASE}/{change}/tareas/nueva")
        self.assertIn(">Luis Gil</option>", self.select(form, "responsable_id"))
        self.post(f"{BASE}/{change}/tareas/nueva", TASK_FORM | {"responsable_id": str(self.luis)},
                  expect=f"{BASE}/{change}")
        self.assertIn(("success", "Tarea añadida."), self.flashes())
        with self.app.app_context():
            (task,) = qms_changes.tasks.list_(db.session, SYSTEM, cambio_id=change)
            task_id = task.id
        page = self.rerendered(f"{BASE}/{change}/tareas/{task_id}/editar", TASK_FORM | {
            "responsable_id": str(self.luis), "estado": "completada"})
        self.assertIn(qms_changes.COMPLETION_REQUIRED, self.field_html(page, "fecha_completada"))
        self.assertIn("Configurar el portal", page)
        self.post(f"{BASE}/{change}/tareas/{task_id}/editar", TASK_FORM | {
            "responsable_id": str(self.luis), "estado": "en_curso"}, expect=f"{BASE}/{change}")
        self.assertIn(("success", "Tarea actualizada."), self.flashes())
        self.denied("post", f"{BASE}/{change}/tareas/{task_id}/eliminar")
        self.login(ADMIN)
        self.post(f"{BASE}/{change}/tareas/{task_id}/eliminar", expect=f"{BASE}/{change}")
        self.assertIn(("success", "Tarea eliminada."), self.flashes())
        with self.app.app_context():
            self.assertEqual(0, db.session.query(TareaCambio).count())

    def test_a_closed_change_takes_no_task_writes(self) -> None:
        implemented = self.implemented()
        with self.app.app_context():
            (task,) = qms_changes.tasks.list_(db.session, SYSTEM, cambio_id=implemented)
            task_id = task.id
        self.login(ADMIN)
        message = qms_changes.TASKS_CLOSED.format(estado="Implantado")
        for method, url in (("get", f"{BASE}/{implemented}/tareas/nueva"),
                            ("get", f"{BASE}/{implemented}/tareas/{task_id}/editar"),
                            ("post", f"{BASE}/{implemented}/tareas/{task_id}/eliminar")):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(f"{BASE}/{implemented}", response.headers["Location"])
                self.assertIn(("danger", message), self.flashes())
        with self.app.app_context():
            self.assertEqual(1, db.session.query(TareaCambio).count())
