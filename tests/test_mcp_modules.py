"""MCP server skeleton: layout, module registry and the ``qms_modules`` tool."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from mcp_support import ADMIN, AUDITOR, OPERATIVO, McpDbCase, mcp_actor

ROOT = Path(__file__).resolve().parents[1]
SLUGS = {
    "no_conformidades", "auditorias", "documentos", "capacitaciones",
    "satisfaccion_clientes", "partes_interesadas", "mejoras",
    "roles_responsabilidades", "riesgos_oportunidades", "recursos_capacitacion",
    "procesos", "indicadores_auditoria",
}


def registry():
    from app.mcp_server import registry as module

    return module


class LayoutTestCase(unittest.TestCase):
    def test_server_package_does_not_shadow_the_sdk(self) -> None:
        self.assertFalse((ROOT / "app" / "mcp").exists())
        self.assertTrue((ROOT / "app" / "mcp_server" / "__init__.py").is_file())
        import mcp

        self.assertNotIn(str(ROOT / "app"), str(Path(mcp.__file__)))
        self.assertIn("mcp", sys.modules)


class RegistryTestCase(unittest.TestCase):
    def test_registry_holds_the_twelve_modules(self) -> None:
        self.assertEqual(SLUGS, set(registry().MODULES))

    def test_each_module_maps_to_its_own_policy_resource(self) -> None:
        resources = [m.resource for m in registry().MODULES.values()]
        self.assertEqual(len(SLUGS), len(set(resources)))

    def test_declared_fields_match_what_each_service_requires(self) -> None:
        from app.services import audits, documents, nonconformities

        bespoke = {
            "no_conformidades": (nonconformities.WRITABLE_FIELDS,
                                 frozenset({"descripcion", "fecha_detectada"})),
            "auditorias": (audits.WRITABLE_FIELDS, audits.REQUIRED_ON_CREATE),
            "documentos": (documents.WRITABLE_FIELDS, documents.REQUIRED_ON_CREATE),
        }
        for slug, module in registry().MODULES.items():
            with self.subTest(module=slug):
                if slug in bespoke:
                    writable, required = bespoke[slug]
                else:
                    spec = module.service.SPEC
                    writable = spec.writable
                    required = frozenset(f.name for f in spec.fields if f.required)
                self.assertEqual(writable, {f.name for f in module.fields})
                self.assertEqual(required, {f.name for f in module.fields if f.required})

    def test_every_module_pages_in_the_database(self) -> None:
        unpaged = [slug for slug, module in registry().MODULES.items()
                   if not callable(getattr(module.service, "list_page", None))]
        self.assertEqual([], unpaged)

    def test_nonconformity_state_filter_offers_the_fixed_states(self) -> None:
        from app.services.nonconformities import ESTADOS_NO_CONFORMIDAD

        field = {f.name: f for f in registry().MODULES["no_conformidades"].filters}["estado"]
        self.assertEqual(("enum", ESTADOS_NO_CONFORMIDAD), (field.type, field.allowed))

    def test_enum_fields_list_the_values_the_services_accept(self) -> None:
        from app.models import DocumentCategory, EstadoAuditoriaEnum, TipoEnum

        modules = registry().MODULES
        expected = {
            ("auditorias", "estado"): EstadoAuditoriaEnum,
            ("documentos", "category"): DocumentCategory,
            ("riesgos_oportunidades", "tipo"): TipoEnum,
        }
        for (slug, name), enum_cls in expected.items():
            field = {f.name: f for f in modules[slug].fields}[name]
            self.assertEqual(tuple(enum_cls.__members__), field.allowed, (slug, name))


class ModulesToolTestCase(McpDbCase):
    async def test_the_tool_is_registered_read_only(self) -> None:
        tools = {t.name: t for t in await self.list_tools()}
        annotations = tools["qms_modules"].annotations
        self.assertTrue(annotations.read_only_hint)
        self.assertFalse(annotations.open_world_hint)

    async def test_lists_every_module_with_fields_and_filters(self) -> None:
        result = await self.call(mcp_actor(ADMIN), "qms_modules")
        self.assertFalse(result.is_error)
        modules = {m["slug"]: m for m in result.structured_content["modules"]}
        self.assertEqual(SLUGS, set(modules))
        nc = modules["no_conformidades"]
        fields = {f["name"]: f for f in nc["fields"]}
        self.assertTrue(fields["descripcion"]["required"])
        self.assertEqual("date", fields["fecha_detectada"]["type"])
        self.assertEqual(["Abierta", "En proceso", "Cerrada"], fields["estado"]["allowed"])
        self.assertEqual({"descripcion", "estado", "fecha_detectada"},
                         {f["name"] for f in nc["filters"]})

    async def test_reports_what_the_caller_may_do(self) -> None:
        cases = [
            (mcp_actor(ADMIN), "documentos", dict(read=True, create=True, update=True)),
            (mcp_actor(OPERATIVO), "documentos", dict(read=False, create=False, update=False)),
            (mcp_actor(OPERATIVO), "no_conformidades", dict(read=True, create=True, update=True)),
            (mcp_actor(OPERATIVO), "procesos", dict(read=True, create=False, update=False)),
            (mcp_actor(AUDITOR, scopes=("read",)), "auditorias",
             dict(read=True, create=False, update=False)),
        ]
        for actor, slug, expected in cases:
            with self.subTest(role=actor.role.name, scopes=actor.scopes, module=slug):
                result = await self.call(actor, "qms_modules")
                modules = {m["slug"]: m for m in result.structured_content["modules"]}
                self.assertEqual(expected, modules[slug]["permissions"])

    async def test_never_exposes_credentials(self) -> None:
        result = await self.call(mcp_actor(ADMIN), "qms_modules")
        text = json.dumps(result.structured_content).lower()
        for word in ("password", "token", "secret", "hash"):
            self.assertNotIn(word, text)

    async def test_without_an_actor_the_tool_fails_cleanly(self) -> None:
        result = await self.call(None, "qms_modules")
        self.assertTrue(result.is_error)
        self.assertNotIn("Traceback", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
