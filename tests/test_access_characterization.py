"""Characterization of the HTTP access rules, plus policy consistency.

The endpoint table below locks the observed behaviour of every blueprint so
refactors (services, policy-driven routes) cannot change who may do what by
accident; the role table is the approved matrix, written by hand. ``test_policy_agrees_with_http`` keeps the policy module
and the routes from drifting apart.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    AccionCorrectiva,
    AccionRevision,
    Auditoria,
    AuditoriaIndicador,
    Capacitacion,
    CompetenceRecord,
    CompetenceRequirement,
    CompetenceType,
    Document,
    DocumentCategory,
    DocumentRevision,
    EstadoRevision,
    EstadoRevisionDireccion,
    FrecuenciaMedicion,
    HallazgoAuditoria,
    MedicionObjetivo,
    Mejora,
    NoConformidad,
    ObjetivoCalidad,
    ParteInteresada,
    Person,
    ProcesoOperacion,
    ProgramaAuditoria,
    RecursoCapacitacion,
    RevisionDireccion,
    RiesgoOportunidad,
    RoleEnum,
    RolResponsabilidad,
    SatisfaccionCliente,
    SentidoObjetivo,
    TipoAccionRevision,
    TipoEnum,
    TipoHallazgo,
    User,
)

PASSWORD_HASH = generate_password_hash("StrongPassword123!")  # hashed once: scrypt is slow
DASHBOARD = "/dashboard/"


@dataclass(frozen=True)
class Endpoint:
    """One characterized route: what it does and what a success looks like."""

    resource: str  # policy Resource member name
    action: str  # policy Action member value
    method: str
    url: str
    ok_status: int
    body: dict = field(default_factory=dict)
    as_json: bool = False



NC = dict(
    descripcion="Desviacion",
    fecha_detectada="2026-10-05",
    responsable="Ana",
    estado="Abierta",
    accion_correctiva="",
)
AUDIT = dict(
    area_auditada="Compras",
    fecha="2026-10-05",
    auditor="Luis",
    resultado="OK",
    accion_correctiva="",
    estado="PENDIENTE",
)
PROGRAMME = dict(anio="2027", objetivos="Objetivos", alcance="Alcance")
PROGRAMME_EDIT = PROGRAMME | {"anio": "2026"}
FINDING = dict(tipo="observacion", clausula="8.5.1", descripcion="Hallazgo", evidencia="")
DOC = dict(
    title="Manual",
    code="DOC-NEW",
    category="MANUAL_CALIDAD",
    owner_id="1",
    next_review_date="",
    author_id="1",
    content="Texto",
)
# Document 1 has a revision in force, so its code is locked (DC-4): the edit keeps it.
DOC_EDIT = DOC | {"code": "DOC-1"}
DRAFT = dict(content="Texto nuevo", change_summary="Cambio", author_id="1")
IMPROVEMENT = dict(no_conformidad="NC", accion_correctiva="a", accion_preventiva="b")
SURVEY = dict(
    cliente="ACME", fecha_encuesta="2026-10-05", puntuacion="8", comentarios=""
)
TRAINING = dict(
    tema="Calidad",
    fecha="2026-10-05",
    personal="Ana",
    duracion_horas="2",
    evaluacion_final="A",
)
PARTY = dict(nombre="Clientes new")
NEW_USER = dict(
    username="persona.nueva",
    email="persona.nueva@example.com",
    role="OPERATIVO",
    password="ClaveNueva2026",
    confirm_password="ClaveNueva2026",
)
USER_EDIT = dict(email="operativo.nuevo@example.com", role="OPERATIVO")
PERSON = dict(nombre="Persona nueva", email="", rol_ids="1", user_id="", activo="y",
              notas="")
REQUIREMENT = dict(rol_id="1", tipo="formacion", descripcion="Curso", criterio="")
RECORD = dict(
    requisito_id="",
    evidencia="Certificado",
    capacitacion_id="",
    fecha_obtencion="2026-10-05",
    fecha_caducidad="",
    evaluacion_eficacia="pendiente",
    fecha_evaluacion="",
    evaluador_id="",
)


ACTION = dict(descripcion="Afilar el molde", responsable_id="1", fecha_prevista="2026-10-20",
              fecha_realizada="")
OBJECTIVE = dict(titulo="Entregas", descripcion="", proceso_id="", indicador="% a tiempo",
                 unidad="%", meta="95", sentido="mayor_igual", valor_inicial="",
                 responsable_id="1", anio="", fecha_inicio="2026-01-01",
                 fecha_limite="2026-12-31", frecuencia="mensual", recursos="", evaluacion="")
# Dated well before any day the suite runs on: a measurement is never in the future.
MEASUREMENT = dict(fecha="2026-02-28", valor="96", comentario="", registrado_por_id="")
REVIEW = dict(fecha="2026-07-10", periodo_desde="2026-04-01", periodo_hasta="2026-06-30",
              asistente_ids="1", comentario_contexto="Cambio")
REVIEW_ACTION = dict(tipo="mejora", descripcion="Seguir", responsable_id="1",
                     fecha_limite="2026-12-31", estado="pendiente", fecha_completada="",
                     resultado="")


def _html(resource, base, create, update, delete):
    """Endpoints for a classic HTML register (list/new/edit/delete).

    ``delete=None`` for a register without a delete route.
    """
    return [
        Endpoint(resource, "read", "GET", base, 200),
        Endpoint(resource, "create", "POST", f"{base}{create[0]}", 302, create[1]),
        Endpoint(resource, "update", "POST", f"{base}{update[0]}", 302, update[1]),
        *([Endpoint(resource, "delete", "POST", f"{base}{delete}", 302)] if delete else []),
    ]


def _json(resource, base, create, update):
    """Endpoints for a JSON register under ``base`` (trailing slash)."""
    return [
        Endpoint(resource, "read", "GET", base, 200),
        Endpoint(resource, "create", "POST", base, 201, create, True),
        Endpoint(resource, "update", "PUT", f"{base}1", 200, update, True),
        Endpoint(resource, "delete", "DELETE", f"{base}1", 200),
    ]


ENDPOINTS = [
    *_html("NONCONFORMITIES", "/no_conformidades/", ("nueva", NC),
           ("editar/1", NC), "eliminar/1"),
    # Corrective actions live under their nonconformity (NC-3 of nc-capa-loop);
    # its page lists them. Verifying and closing narrow the role in the service
    # (administrators and auditors), so tests/test_corrective_action_routes.py
    # characterizes them, like cancel and reopen.
    Endpoint("CORRECTIVE_ACTIONS", "read", "GET", "/no_conformidades/1", 200),
    Endpoint("CORRECTIVE_ACTIONS", "create", "POST", "/no_conformidades/1/acciones/nueva",
             302, ACTION),
    Endpoint("CORRECTIVE_ACTIONS", "update", "POST",
             "/no_conformidades/1/acciones/1/editar", 302, ACTION),
    Endpoint("CORRECTIVE_ACTIONS", "delete", "POST",
             "/no_conformidades/1/acciones/1/eliminar", 302),
    *_html("AUDITS", "/auditorias/", ("nueva", AUDIT), ("editar/1", AUDIT),
           "eliminar/1"),
    Endpoint("AUDITS", "read", "GET", "/auditorias/1", 200),
    # Findings live under their audit (AP-3 of audit-programme); audit 2 holds
    # finding 1, so audit 1 stays deletable.
    Endpoint("AUDITS", "create", "POST", "/auditorias/2/hallazgos/nuevo", 302, FINDING),
    Endpoint("AUDITS", "update", "POST", "/auditorias/2/hallazgos/1/editar", 302, FINDING),
    Endpoint("AUDITS", "delete", "POST", "/auditorias/2/hallazgos/1/eliminar", 302),
    # Programme 1 (2026) has no audits, so deleting it succeeds. Approving narrows
    # the role to administrators, so tests/test_audit_screens.py characterizes it.
    *_html("AUDITS", "/programas-auditoria/", ("nuevo", PROGRAMME),
           ("1/editar", PROGRAMME_EDIT), "1/eliminar"),
    Endpoint("AUDITS", "read", "GET", "/programas-auditoria/1", 200),
    # Documents are withdrawn, never deleted (DC6); document 1 is in force, so
    # every role reads its page. The workflow narrows roles in its service.
    *_html("DOCUMENTS", "/documents/", ("new", DOC), ("edit/1", DOC_EDIT), None),
    Endpoint("DOCUMENTS", "read", "GET", "/documents/1", 200),
    # The revision workflow (DC-3). Revision 2 is a draft: editing and
    # submitting it succeed, the form pages answer 200, and publishing a draft
    # is refused with a flash on the document's page. Revision 3 (document 2)
    # is in review, which the reject page needs (DC-4). Approving and
    # withdrawing narrow the role to administrators, so
    # tests/test_document_screens.py characterizes them.
    Endpoint("DOCUMENTS", "create", "GET", "/documents/1/revisions/new", 200),
    Endpoint("DOCUMENTS", "update", "POST", "/documents/1/revisions/2/edit", 302, DRAFT),
    Endpoint("DOCUMENTS", "update", "POST", "/documents/1/revisions/2/submit", 302),
    Endpoint("DOCUMENTS", "update", "GET", "/documents/2/revisions/3/reject", 200),
    Endpoint("DOCUMENTS", "update", "POST", "/documents/1/revisions/2/publish", 302),
    *_html("IMPROVEMENTS", "/mejoras/", ("nueva", IMPROVEMENT),
           ("editar/1", IMPROVEMENT), "eliminar/1"),
    *_html("CUSTOMER_SATISFACTION", "/satisfaccion_cliente/", ("nueva", SURVEY),
           ("editar/1", SURVEY), "eliminar/1"),
    *_html("TRAINING", "/capacitaciones/", ("nueva", TRAINING),
           ("editar/1", TRAINING), "eliminar/1"),
    *_html("INTERESTED_PARTIES", "/partes_interesadas/", ("nueva", PARTY),
           ("editar/1", PARTY), "eliminar/1"),
    # Users are deactivated, never deleted (U2): no delete route. User 3 is
    # the seeded "operativo" account (one user per role, in RoleEnum order).
    *_html("USERS", "/usuarios/", ("nuevo", NEW_USER), ("3/editar", USER_EDIT), None),
    # Account actions are updates too; without a reset base URL the link
    # action answers with a flash on the list, so nothing is mailed.
    *[Endpoint("USERS", "update", "POST", f"/usuarios/3/{action}", 302)
      for action in ("desactivar", "reactivar", "enviar-enlace")],
    # Person 1 and competence record 1 are seeded; nothing cites requirement 1
    # or person 2, so the deletes succeed.
    *_html("PEOPLE", "/personas/", ("nueva", PERSON), ("1/editar", PERSON), "2/eliminar"),
    Endpoint("PEOPLE", "read", "GET", "/personas/1", 200),
    *_html("COMPETENCE", "/competencias/requisitos/", ("nuevo", REQUIREMENT),
           ("1/editar", REQUIREMENT), "1/eliminar"),
    Endpoint("COMPETENCE", "create", "POST", "/competencias/personas/1/nueva", 302, RECORD),
    Endpoint("COMPETENCE", "update", "POST", "/competencias/1/editar", 302, RECORD),
    Endpoint("COMPETENCE", "delete", "POST", "/competencias/1/eliminar", 302),
    Endpoint("COMPETENCE", "read", "GET", "/competencias/matriz", 200),
    # Objective 1 has measurement 1, so its target is fixed and it cannot be
    # deleted; objective 2 has none, so deleting it succeeds (QO-2).
    *_html("QUALITY_OBJECTIVES", "/objetivos-calidad/", ("nuevo", OBJECTIVE),
           ("1/editar", OBJECTIVE), "2/eliminar"),
    Endpoint("QUALITY_OBJECTIVES", "read", "GET", "/objetivos-calidad/1", 200),
    # The yearly PDF (QO-3) is rendered for real, so the print template is exercised.
    Endpoint("QUALITY_OBJECTIVES", "read", "GET", "/objetivos-calidad/pdf?anio=2026", 200),
    Endpoint("QUALITY_OBJECTIVES", "update", "POST", "/objetivos-calidad/1/estado/alcanzado",
             302),
    Endpoint("QUALITY_OBJECTIVES", "create", "POST", "/objetivos-calidad/1/mediciones/nueva",
             302, MEASUREMENT),
    Endpoint("QUALITY_OBJECTIVES", "update", "POST",
             "/objetivos-calidad/1/mediciones/1/editar", 302, MEASUREMENT),
    Endpoint("QUALITY_OBJECTIVES", "delete", "POST",
             "/objetivos-calidad/1/mediciones/1/eliminar", 302),
    # Review 1 is a draft with action 1; review 2 a draft without actions, so
    # deleting it succeeds; review 3 is held, so it takes conclusions (MR-2).
    # Holding and closing narrow the role to administrators, so
    # tests/test_management_review_screens.py characterizes them.
    *_html("MANAGEMENT_REVIEW", "/revisiones-direccion/", ("nueva", REVIEW),
           ("1/editar", REVIEW), "2/eliminar"),
    Endpoint("MANAGEMENT_REVIEW", "read", "GET", "/revisiones-direccion/1", 200),
    Endpoint("MANAGEMENT_REVIEW", "update", "POST", "/revisiones-direccion/3/conclusiones",
             302, dict(conclusiones="Conforme")),
    Endpoint("MANAGEMENT_REVIEW", "create", "POST", "/revisiones-direccion/1/acciones/nueva",
             302, REVIEW_ACTION),
    Endpoint("MANAGEMENT_REVIEW", "update", "POST",
             "/revisiones-direccion/1/acciones/1/editar", 302, REVIEW_ACTION),
    Endpoint("MANAGEMENT_REVIEW", "delete", "POST",
             "/revisiones-direccion/1/acciones/1/eliminar", 302),
    *_json("IMPROVEMENTS", "/mejoras/api/", {"no_conformidad": "NC"},
           {"accion_correctiva": "x"}),
    *_json("AUDIT_INDICATORS", "/auditoria_indicador/",
           {"area_auditoria": "Ventas"}, {"resultado": "ok"}),
    *_json("ROLES_RESPONSIBILITIES", "/rol_responsabilidad/", {"rol": "Nuevo"},
           {"descripcion_politica_calidad": "x"}),
    *_json("RISKS_OPPORTUNITIES", "/riesgo_oportunidad/",
           {"tipo": "Riesgo", "descripcion": "d"}, {"plan_accion": "x"}),
    *_json("TRAINING_RESOURCES", "/recurso_capacitacion/",
           {"recurso_necesario": "Sala"}, {"descripcion_documentacion": "x"}),
    *_json("PROCESS_OPERATIONS", "/proceso_operacion/", {"proceso": "Nuevo"},
           {"criterio_calidad": "x"}),
]

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
ADMIN_AUDITOR = {ADMIN, AUDITOR}

# The approved matrix (D1) as HTTP behaviour, written by hand: roles allowed
# per resource for (read, create/update, delete).  Everything else is denied.
ALL = set(RoleEnum)
JSON_REGISTER = (ALL, ADMIN_AUDITOR, {ADMIN})
ALLOWED = {
    "NONCONFORMITIES": (ALL, ALL, {ADMIN}),
    "IMPROVEMENTS": (ALL, ALL, {ADMIN}),
    "CUSTOMER_SATISFACTION": (ALL, ALL, {ADMIN}),
    "TRAINING": (ALL, ALL, {ADMIN}),
    "INTERESTED_PARTIES": (ALL, ALL, {ADMIN}),
    "AUDITS": (ADMIN_AUDITOR, ADMIN_AUDITOR, {ADMIN}),
    "DOCUMENTS": (ALL, ADMIN_AUDITOR, set()),  # DC4 and DC6 of document-control
    "USERS": (ADMIN_AUDITOR, {ADMIN}, set()),  # nobody deletes; test_policy pins it
    "AUDIT_INDICATORS": JSON_REGISTER,
    "ROLES_RESPONSIBILITIES": JSON_REGISTER,
    "RISKS_OPPORTUNITIES": JSON_REGISTER,
    "TRAINING_RESOURCES": JSON_REGISTER,
    "PROCESS_OPERATIONS": JSON_REGISTER,
    "PEOPLE": JSON_REGISTER,  # decision Q1 of qms-people
    "COMPETENCE": JSON_REGISTER,
    "CORRECTIVE_ACTIONS": (ALL, ALL, {ADMIN}),
    "QUALITY_OBJECTIVES": JSON_REGISTER,  # decision QO4 of quality-objectives
    "MANAGEMENT_REVIEW": (ADMIN_AUDITOR, ADMIN_AUDITOR, {ADMIN}),  # MR5 of management-review
}
# Policy entries without routes. AUDIT_LOG has no adapter yet. The API_TOKENS
# matrix (issue, list, revoke any token) stays CLI-only; "Mi perfil" bypasses it
# with an ownership check, so tests/test_profile_routes.py characterizes it.
NO_ROUTES = {"AUDIT_LOG", "API_TOKENS"}


def allowed_roles(endpoint: Endpoint) -> set:
    read, write, delete = ALLOWED[endpoint.resource]
    return {"read": read, "delete": delete}.get(endpoint.action, write)


_OBSERVED: dict = {}  # (role, method, url) -> allowed; shared by two tests


class AccessCharacterizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _fresh(self, role: RoleEnum | None):
        """Reset the database, seed one record of everything, log in as role."""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()
            for r in RoleEnum:
                db.session.add(
                    User(
                        username=r.name.lower(),
                        email=f"{r.name.lower()}@example.com",
                        password=PASSWORD_HASH,
                        role=r,
                    )
                )
            db.session.add_all(
                [
                    NoConformidad(descripcion="NC", fecha_detectada=date(2026, 10, 5)),
                    Auditoria(
                        area_auditada="A", fecha=date(2026, 10, 5), auditor="x",
                        resultado="r",
                    ),
                    Auditoria(
                        area_auditada="B", fecha=date(2026, 10, 6), auditor="y",
                        resultado="r",
                    ),
                    ProgramaAuditoria(anio=2026, objetivos="O", alcance="A"),
                    Document(title="T", code="DOC-1", category=DocumentCategory.OTRO),
                    Document(title="T2", code="DOC-2", category=DocumentCategory.OTRO),
                    Mejora(no_conformidad="NC"),
                    SatisfaccionCliente(
                        cliente="C", fecha_encuesta=date(2026, 10, 5), puntuacion=5
                    ),
                    Capacitacion(tema="T", fecha=date(2026, 10, 5), personal="P"),
                    ParteInteresada(nombre="Seed"),
                    AuditoriaIndicador(area_auditoria="Seed"),
                    RolResponsabilidad(rol="Seed"),
                    RiesgoOportunidad(tipo=TipoEnum.Riesgo, descripcion="d"),
                    RecursoCapacitacion(recurso_necesario="Seed"),
                    ProcesoOperacion(proceso="Seed"),
                    RolResponsabilidad(rol="Seed 2"),  # role 1 stays deletable
                    Person(nombre="Seed"),
                    Person(nombre="Unreferenced"),
                ]
            )
            db.session.flush()  # the competence rows cite role 2 and person 1
            db.session.add_all(
                [
                    HallazgoAuditoria(
                        auditoria_id=2, tipo=TipoHallazgo.observacion, descripcion="Seed"
                    ),
                    CompetenceRequirement(
                        rol_id=2, tipo=CompetenceType.formacion, descripcion="Seed"
                    ),
                    CompetenceRecord(
                        persona_id=1, evidencia="Seed", fecha_obtencion=date(2026, 10, 5)
                    ),
                    AccionCorrectiva(
                        no_conformidad_id=1, descripcion="Seed", responsable_id=1,
                        fecha_prevista=date(2026, 10, 20),
                    ),
                    DocumentRevision(
                        document_id=1, numero=1, estado=EstadoRevision.vigente,
                        content="c", effective_from=date(2026, 10, 5),
                    ),
                    DocumentRevision(
                        document_id=1, numero=2, estado=EstadoRevision.borrador,
                        content="c", change_summary="Cambio", author_id=1,
                    ),
                    DocumentRevision(
                        document_id=2, numero=1, estado=EstadoRevision.en_revision,
                        content="c", change_summary="Alta", author_id=1,
                    ),
                    *(
                        ObjetivoCalidad(
                            titulo=titulo, indicador="% a tiempo", meta=95,
                            sentido=SentidoObjetivo.mayor_igual, responsable_id=1, anio=2026,
                            fecha_inicio=date(2026, 1, 1), fecha_limite=date(2026, 12, 31),
                            frecuencia=FrecuenciaMedicion.mensual,
                        )
                        for titulo in ("Seed", "Unmeasured")
                    ),
                    *(
                        RevisionDireccion(
                            fecha=date(2026, 7, 10), periodo_desde=date(2026, 4, 1),
                            periodo_hasta=date(2026, 6, 30), **held,
                        )
                        for held in ({}, {}, dict(estado=EstadoRevisionDireccion.celebrada,
                                                  datos_entrada={}))
                    ),
                ]
            )
            db.session.flush()  # the measurement cites objective 1, the action review 1
            db.session.add(
                MedicionObjetivo(objetivo_id=1, fecha=date(2026, 1, 31), valor=90)
            )
            db.session.add(
                AccionRevision(revision_id=1, tipo=TipoAccionRevision.mejora, descripcion="Seed",
                               responsable_id=1, fecha_limite=date(2026, 12, 31))
            )
            db.session.commit()
        client = self.app.test_client()
        if role is not None:
            with self.app.app_context():
                user_id = User.query.filter_by(username=role.name.lower()).one().id
            # Same session keys Flask-Login writes on a real login.
            with client.session_transaction() as session:
                session["_user_id"] = str(user_id)
                session["_fresh"] = True
        return client

    def _request(self, client, endpoint: Endpoint):
        kwargs = {}
        if endpoint.as_json:
            kwargs["json"] = endpoint.body
        elif endpoint.body:
            kwargs["data"] = endpoint.body
        return client.open(endpoint.url, method=endpoint.method, **kwargs)

    @staticmethod
    def _is_denied(response) -> bool:
        """HTML refusals redirect to the dashboard; JSON bodies get a 403."""
        if response.status_code == 403:
            return "error" in response.get_json()
        return response.status_code == 302 and response.headers[
            "Location"
        ].endswith(DASHBOARD)

    def _observed_allowed(self, role, endpoint: Endpoint) -> bool:
        key = (role, endpoint.method, endpoint.url)
        if key not in _OBSERVED:
            _OBSERVED[key] = self._observe(role, endpoint)
        return _OBSERVED[key]

    def _observe(self, role, endpoint: Endpoint) -> bool:
        client = self._fresh(role)
        response = self._request(client, endpoint)
        if self._is_denied(response):
            if response.status_code == 302:
                with client.session_transaction() as session:
                    self.assertIn(
                        ("danger", "No tienes permiso para acceder a esta página."),
                        session["_flashes"],
                    )
            return False
        self.assertEqual(
            endpoint.ok_status,
            response.status_code,
            f"{role.name} {endpoint.method} {endpoint.url}",
        )
        return True

    def test_unauthenticated_requests_redirect_to_login(self) -> None:
        for endpoint in ENDPOINTS:
            with self.subTest(method=endpoint.method, url=endpoint.url):
                response = self._request(self._fresh(None), endpoint)
                self.assertEqual(302, response.status_code)
                self.assertIn("/login", response.headers["Location"])

    def test_role_outcomes_match_the_approved_matrix(self) -> None:
        for endpoint in ENDPOINTS:
            for role in RoleEnum:
                with self.subTest(
                    role=role.name, method=endpoint.method, url=endpoint.url
                ):
                    self.assertEqual(
                        role in allowed_roles(endpoint),
                        self._observed_allowed(role, endpoint),
                    )

    def test_policy_agrees_with_http(self) -> None:
        from app.services.actor import Actor
        from app.services.policy import Action, Resource, can

        for endpoint in ENDPOINTS:
            for role in RoleEnum:
                with self.subTest(
                    role=role.name, method=endpoint.method, url=endpoint.url
                ):
                    actor = Actor(
                        user_id=1, label=role.name, role=role, channel="web",
                        scopes=None,
                    )
                    self.assertEqual(
                        self._observed_allowed(role, endpoint),
                        can(
                            actor,
                            Action(endpoint.action),
                            Resource[endpoint.resource],
                        ),
                    )

    def test_every_resource_has_a_characterized_endpoint(self) -> None:
        from app.services.policy import Resource

        self.assertEqual(
            {r.name for r in Resource} - NO_ROUTES, {e.resource for e in ENDPOINTS}
        )


if __name__ == "__main__":
    unittest.main()
