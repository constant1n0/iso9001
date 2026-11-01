"""Shared fixtures for the MCP adapter tests: an in-memory app and an in-memory SDK client."""

from __future__ import annotations

import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from mcp import Client
from sqlalchemy import inspect as sa_inspect

from app.extensions import db
from app.mcp_server import context
from app.mcp_server.server import build_server
from app.mcp_server.registry import MODULES
from app.models import (
    CambioSGC, CanalContacto, Cliente, EstadoNoConformidad, NoConformidad, ObjetivoCalidad, Person, Proveedor,
    TipoEncuesta,
    RevisionDireccion, Riesgo, RoleEnum, User,
)
from app.services import api_tokens
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO


def mcp_actor(role=ADMIN, scopes=("read", "write"), user_id=7) -> Actor:
    return Actor(user_id=user_id, label=f"user{user_id}", role=role, channel="mcp",
                 scopes=None if scopes is None else frozenset(scopes))


# Minimal valid payload per module, as an agent would send it (dates as ISO text).
SEEDS = {
    "no_conformidades": {"descripcion": "Pieza fuera de tolerancia", "fecha_detectada": "2026-10-01"},
    "auditorias": {"area_auditada": "Calidad", "fecha": "2026-10-01", "auditor": "Ana", "resultado": "Sin hallazgos"},
    "capacitaciones": {"tema": "Seguridad", "fecha": "2026-10-01", "personal": "Ana"},
    "clientes": {"nombre": "ACME"},
    "reclamaciones": {"cliente_id": 1, "descripcion": "Entrega retrasada"},
    "satisfaccion_clientes": {"cliente_id": 1, "tipo_encuesta": "otra", "canal": "otro",
                              "fecha_encuesta": "2026-10-01", "puntuacion": 8},
    "partes_interesadas": {"nombre": "Clientes"},
    "mejoras": {"descripcion": "NC-1", "fecha_identificacion": "2026-10-01"},
    "roles_responsabilidades": {"rol": "Director"},
    "recursos_capacitacion": {"recurso_necesario": "Formacion"},
    "procesos": {"proceso": "Compras"},
    "indicadores_auditoria": {"area_auditoria": "Calidad"},
    "personas": {"nombre": "Ana Pérez"},
    # Documents are not written through MCP; ``seed`` creates them through the service,
    # citing the person above (or creating it first when seeded alone).
    "documentos": {"title": "Manual", "code": "MC-1", "category": "MANUAL_CALIDAD",
                   "owner_id": 1, "author_id": 1, "content": "Texto"},
    # Competence cites the role and the person created above: modules are seeded in
    # this order on an empty database, so both have id 1.
    "competencias_requeridas": {"rol_id": 1, "tipo": "formacion", "descripcion": "Curso de seguridad"},
    "competencias_acreditadas": {"persona_id": 1, "evidencia": "Certificado 123",
                                 "fecha_obtencion": "2026-10-01"},
    # Cites the nonconformity and the person seeded above (both id 1).
    "acciones_correctivas": {"no_conformidad_id": 1, "descripcion": "Reajustar el molde",
                             "responsable_id": 1, "fecha_prevista": "2026-10-15"},
    "programas_auditoria": {"anio": 2026, "objetivos": "Auditar todos los procesos",
                            "alcance": "Todo el SGC"},
    # Cites the audit seeded above (id 1); an observation creates no non-conformity.
    "hallazgos_auditoria": {"auditoria_id": 1, "tipo": "observacion",
                            "descripcion": "Registro de compras sin firmar"},
    # Cites the person seeded above (id 1).
    "objetivos_calidad": {"titulo": "Entregas a tiempo", "indicador": "% de pedidos a tiempo",
                          "meta": 95, "sentido": "mayor_igual", "responsable_id": 1,
                          "fecha_inicio": "2026-01-01", "fecha_limite": "2026-12-31",
                          "frecuencia": "mensual"},
    # Cites the objective above (id 1), on a past date inside its period.
    "mediciones_objetivo": {"objetivo_id": 1, "fecha": "2026-01-31", "valor": 92.5},
    "revisiones_direccion": {"fecha": "2026-10-01", "periodo_desde": "2026-01-01",
                             "periodo_hasta": "2026-09-30"},
    # Cites the review and the person seeded above (both id 1).
    "acciones_revision": {"revision_id": 1, "tipo": "mejora",
                          "descripcion": "Automatizar el seguimiento", "responsable_id": 1,
                          "fecha_limite": "2026-12-31"},
    # Owned by the person seeded above (id 1); identified today by default.
    "riesgos": {"titulo": "Fallo de proveedor", "descripcion": "Proveedor único de acero",
                "origen": "externo", "responsable_id": 1},
    "oportunidades": {"titulo": "Nuevo mercado", "descripcion": "Sector naval",
                      "origen": "externo", "responsable_id": 1},
    # Cites the risk and the person seeded above (both id 1).
    "acciones_riesgo": {"riesgo_id": 1, "descripcion": "Homologar un segundo proveedor",
                        "responsable_id": 1, "fecha_limite": "2026-12-31"},
    "proveedores": {"nombre": "Aceros del Norte", "tipo_suministro": "producto"},
    # Identified on a past date.
    "cuestiones_contexto": {"tipo": "externa", "categoria": "legal",
                            "titulo": "Nueva normativa de envases",
                            "descripcion": "Obliga a rediseñar el embalaje",
                            "efecto": "negativo", "pertinente": True,
                            "fecha_identificacion": "2026-10-01"},
    # The first version of the scope needs its statement; later ones copy the current one.
    "alcances_sgc": {"declaracion": "Fabricación de envases metálicos"},
    # Cites the provider and the person seeded above (both id 1), on a past date.
    "evaluaciones_proveedor": {"proveedor_id": 1, "fecha": "2026-10-01", "evaluador_id": 1,
                               "calidad": 4, "plazo": 4, "servicio": 3, "documentacion": 5},
    # Owned by the person seeded above (id 1), proposed on a past date.
    "cambios_sgc": {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos",
                    "origen": "otro", "proposito": "Trazabilidad de los pedidos",
                    "responsable_id": 1, "fecha_propuesta": "2026-10-01"},
    # Cites the change and the person seeded above (both id 1).
    "tareas_cambio": {"cambio_id": 1, "descripcion": "Migrar los pedidos abiertos",
                      "responsable_id": 1, "fecha_limite": "2026-12-31"},
    "confirmaciones_lectura": {"persona_id": 1, "tipo": "objetivos",
                               "fecha_confirmacion": "2020-01-01"},
}


def survey_values(name="C") -> dict:
    """Existing or core-inserted customer plus required survey classification.

    Core statements keep read/report fixtures independent of service writes and
    the audit guard. Do not autoflush unrelated pending fixture rows.
    """
    with db.session.no_autoflush:
        customer_id = db.session.scalar(db.select(Cliente.id).where(Cliente.nombre == name))
        if customer_id is None:
            customer_id = db.session.execute(Cliente.__table__.insert().values(
                nombre=name)).inserted_primary_key[0]
    return {"cliente_id": customer_id, "tipo_encuesta": TipoEncuesta.otra,
            "canal": CanalContacto.otro}


class McpDbCase(unittest.IsolatedAsyncioTestCase):
    """In-memory app plus ``call``, which talks to a fresh in-memory MCP server."""

    #: Extra ``build_app`` configuration, e.g. a file database for concurrency tests.
    app_config: dict = {}

    def setUp(self) -> None:
        self.app = bootstrap.build_app(**self.app_config)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    async def call(self, actor: Actor | None, name: str, arguments: dict | None = None):
        # The actor is set before the client starts so the server tasks inherit it.
        token = context.set_actor(actor) if actor is not None else None
        try:
            async with Client(build_server(self.app)) as client:
                return await client.call_tool(name, arguments or {})
        finally:
            if token is not None:
                context.reset_actor(token)

    async def list_tools(self):
        async with Client(build_server(self.app)) as client:
            return (await client.list_tools()).tools

    def seed(self, slug: str, **overrides) -> int:
        """Create a record through its service (as an administrator) and return its id."""
        module = MODULES[slug]
        if (slug in ("documentos", "objetivos_calidad", "acciones_revision", "riesgos",
                     "oportunidades", "cambios_sgc", "confirmaciones_lectura") and db.session.get(Person, 1) is None):
            self.seed("personas")
        if slug == "tareas_cambio" and db.session.get(CambioSGC, 1) is None:
            self.seed("cambios_sgc")
        if slug == "acciones_riesgo" and db.session.get(Riesgo, 1) is None:
            self.seed("riesgos")
        if slug == "mediciones_objetivo" and db.session.get(ObjetivoCalidad, 1) is None:
            self.seed("objetivos_calidad")
        if slug == "acciones_revision" and db.session.get(RevisionDireccion, 1) is None:
            self.seed("revisiones_direccion")
        if slug == "evaluaciones_proveedor":
            if db.session.get(Person, 1) is None:
                self.seed("personas")
            if db.session.get(Proveedor, 1) is None:
                self.seed("proveedores")
        if slug in ("satisfaccion_clientes", "reclamaciones") and db.session.get(Cliente, 1) is None:
            self.seed("clientes")
        data = SEEDS[slug] | overrides
        for field in module.fields:
            if field.type == "date" and field.name in data:
                data[field.name] = date.fromisoformat(data[field.name])
        admin = Actor(user_id=None, label="seed", role=ADMIN, channel="cli")
        row = module.service.create(db.session, admin, data)
        db.session.commit()
        return sa_inspect(row).mapper.primary_key_from_instance(row)[0]

    @staticmethod
    def force_nc_state(record_id: int, estado: EstadoNoConformidad) -> None:
        """Put a nonconformity in a state no write tool sets (core update, no audit)."""
        db.session.execute(NoConformidad.__table__.update()
                           .where(NoConformidad.id == record_id).values(estado=estado))
        db.session.commit()

    @staticmethod
    def cli() -> Actor:
        return Actor(user_id=None, label="cli", role=ADMIN, channel="cli")

    def issue_token(self, role=ADMIN, scopes=("read", "write"), days=90):
        """Issue an API token for a new user; returns ``(plaintext, row, user)``."""
        user = User(username=f"user{User.query.count() + 1}", password="x", role=role)
        db.session.add(user)
        db.session.commit()
        plaintext, row = api_tokens.issue(
            db.session, self.cli(), secret_key=self.app.config["SECRET_KEY"], user_id=user.id,
            name="test", scopes=scopes, days=days)
        db.session.commit()
        return plaintext, row, user
