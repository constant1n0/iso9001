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
    Auditoria,
    AuditoriaIndicador,
    Capacitacion,
    CompetenceRecord,
    CompetenceRequirement,
    CompetenceType,
    Document,
    DocumentCategory,
    Mejora,
    NoConformidad,
    ParteInteresada,
    Person,
    ProcesoOperacion,
    RecursoCapacitacion,
    RiesgoOportunidad,
    RoleEnum,
    RolResponsabilidad,
    SatisfaccionCliente,
    TipoEnum,
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
DOC = dict(
    title="Manual",
    code="DOC-NEW",
    category="MANUAL_CALIDAD",
    version="1.0",
    issued_date="2026-10-05",
    approved_by="Dirección",
    content="Texto",
)
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
    *_html("DOCUMENTS", "/documents/", ("new", DOC), ("edit/1", DOC), "delete/1"),
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
    "DOCUMENTS": ({ADMIN}, {ADMIN}, {ADMIN}),
    "USERS": (ADMIN_AUDITOR, {ADMIN}, set()),  # nobody deletes; test_policy pins it
    "AUDIT_INDICATORS": JSON_REGISTER,
    "ROLES_RESPONSIBILITIES": JSON_REGISTER,
    "RISKS_OPPORTUNITIES": JSON_REGISTER,
    "TRAINING_RESOURCES": JSON_REGISTER,
    "PROCESS_OPERATIONS": JSON_REGISTER,
    "PEOPLE": JSON_REGISTER,  # decision Q1 of qms-people
    "COMPETENCE": JSON_REGISTER,
    "CORRECTIVE_ACTIONS": (ALL, ALL, {ADMIN}),
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
                    Document(
                        title="T", code="DOC-1", category=DocumentCategory.OTRO,
                        content="c",
                    ),
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
                ]
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
