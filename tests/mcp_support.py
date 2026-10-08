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
from app.models import RoleEnum, User
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
    "documentos": {"title": "Manual", "code": "MC-1", "category": "MANUAL_CALIDAD", "version": "1",
                   "issued_date": "2026-10-01", "content": "Texto"},
    "capacitaciones": {"tema": "Seguridad", "fecha": "2026-10-01", "personal": "Ana"},
    "satisfaccion_clientes": {"cliente": "ACME", "fecha_encuesta": "2026-10-01", "puntuacion": 8},
    "partes_interesadas": {"nombre": "Clientes"},
    "mejoras": {"no_conformidad": "NC-1"},
    "roles_responsabilidades": {"rol": "Director"},
    "riesgos_oportunidades": {"tipo": "Riesgo", "descripcion": "Fallo de proveedor"},
    "recursos_capacitacion": {"recurso_necesario": "Formacion"},
    "procesos": {"proceso": "Compras"},
    "indicadores_auditoria": {"area_auditoria": "Calidad"},
    "personas": {"nombre": "Ana Pérez"},
    # Competence cites the role and the person created above: modules are seeded in
    # this order on an empty database, so both have id 1.
    "competencias_requeridas": {"rol_id": 1, "tipo": "formacion", "descripcion": "Curso de seguridad"},
    "competencias_acreditadas": {"persona_id": 1, "evidencia": "Certificado 123",
                                 "fecha_obtencion": "2026-10-01"},
}


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
        data = SEEDS[slug] | overrides
        for field in module.fields:
            if field.type == "date" and field.name in data:
                data[field.name] = date.fromisoformat(data[field.name])
        admin = Actor(user_id=None, label="seed", role=ADMIN, channel="cli")
        row = module.service.create(db.session, admin, data)
        db.session.commit()
        return sa_inspect(row).mapper.primary_key_from_instance(row)[0]

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
