"""MCP read tools: ``qms_list`` and ``qms_get`` over every module."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mcp_support import ADMIN, OPERATIVO, SEEDS, McpDbCase, mcp_actor

from app.services import crud, nonconformities


def error_text(result) -> str:
    return result.content[0].text


class ReadToolsRegistrationTestCase(McpDbCase):
    async def test_list_and_get_are_registered_read_only(self) -> None:
        tools = {t.name: t for t in await self.list_tools()}
        self.assertLessEqual({"qms_modules", "qms_list", "qms_get"}, set(tools))
        for name in ("qms_list", "qms_get"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)


class EveryModuleTestCase(McpDbCase):
    async def test_every_module_lists_and_gets_through_its_service(self) -> None:
        for slug in SEEDS:
            with self.subTest(module=slug):
                record_id = self.seed(slug)
                listed = await self.call(mcp_actor(ADMIN), "qms_list", {"module": slug})
                self.assertFalse(listed.is_error, listed.content)
                page = listed.structured_content
                self.assertEqual((1, 1, 1), (page["total"], page["page"], len(page["items"])))
                self.assertEqual(record_id, page["items"][0]["id"])
                got = await self.call(mcp_actor(ADMIN), "qms_get",
                                      {"module": slug, "id": record_id})
                self.assertFalse(got.is_error, got.content)
                self.assertEqual(page["items"][0], got.structured_content)


class FilterTestCase(McpDbCase):
    async def test_filters_are_validated_and_applied(self) -> None:
        self.seed("no_conformidades", estado="En proceso")
        self.seed("no_conformidades", descripcion="Otra", fecha_detectada="2026-09-01")
        actor = mcp_actor(ADMIN)

        async def total(slug, filters):
            result = await self.call(actor, "qms_list", {"module": slug, "filters": filters})
            self.assertFalse(result.is_error, result.content)
            return result.structured_content["total"]

        self.assertEqual(1, await total("no_conformidades", {"estado": "En proceso"}))
        self.assertEqual(1, await total("no_conformidades", {"fecha_detectada": "2026-09-01"}))
        self.assertEqual(1, await total("no_conformidades", {"descripcion": "otra"}))
        self.seed("auditorias", estado="COMPLETADA")
        self.assertEqual(1, await total("auditorias", {"estado": "COMPLETADA"}))
        self.assertEqual(0, await total("auditorias", {"estado": "PENDIENTE"}))

    async def test_people_filter_by_name_role_and_active_flag(self) -> None:
        quality = self.seed("roles_responsabilidades", rol="Calidad")
        ana = self.seed("personas", nombre="Ana Pérez", rol_ids=[quality])
        eva = self.seed("personas", nombre="Eva Gómez", activo=False)

        async def ids(filters):
            result = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                     {"module": "personas", "filters": filters})
            self.assertFalse(result.is_error, result.content)
            return [item["id"] for item in result.structured_content["items"]]

        self.assertEqual([ana], await ids({"nombre": "pérez"}))
        self.assertEqual([ana], await ids({"rol_id": quality}))
        self.assertEqual([ana], await ids({"activo": True}))
        self.assertEqual([eva], await ids({"activo": False}))
        for filters in ({"rol_id": "Calidad"}, {"activo": "true"}, {"activo": 1}):
            with self.subTest(filters=filters):
                bad = await self.call(mcp_actor(ADMIN), "qms_list",
                                      {"module": "personas", "filters": filters})
                self.assertTrue(bad.is_error)
                self.assertIn(next(iter(filters)), error_text(bad))

    async def test_people_records_carry_their_role_ids(self) -> None:
        quality = self.seed("roles_responsabilidades", rol="Calidad")
        purchasing = self.seed("roles_responsabilidades", rol="Compras")
        ana = self.seed("personas", rol_ids=[purchasing, quality])
        listed = await self.call(mcp_actor(OPERATIVO), "qms_list", {"module": "personas"})
        got = await self.call(mcp_actor(OPERATIVO), "qms_get", {"module": "personas", "id": ana})
        self.assertEqual([quality, purchasing], listed.structured_content["items"][0]["rol_ids"])
        self.assertEqual(listed.structured_content["items"][0], got.structured_content)
        # Other modules keep their plain column output.
        nc = await self.call(mcp_actor(ADMIN), "qms_get",
                             {"module": "no_conformidades", "id": self.seed("no_conformidades")})
        self.assertNotIn("rol_ids", nc.structured_content)

    async def test_every_enum_filter_takes_exactly_the_values_its_registry_entry_allows(self) -> None:
        from app.mcp_server.registry import MODULES

        enum_filters = [(m.slug, f) for m in MODULES.values() for f in m.filters if f.type == "enum"]
        self.assertIn(("no_conformidades", "estado"), [(s, f.name) for s, f in enum_filters])
        for slug, field in enum_filters:
            for value in (*field.allowed, "VALOR_INVENTADO"):
                with self.subTest(module=slug, filter=field.name, value=value):
                    result = await self.call(mcp_actor(ADMIN), "qms_list",
                                             {"module": slug, "filters": {field.name: value}})
                    self.assertEqual(value == "VALOR_INVENTADO", result.is_error, result.content)
                    if result.is_error:
                        self.assertIn(field.allowed[0], error_text(result))

    async def test_the_nonconformity_state_filter_is_the_fixed_state_list(self) -> None:
        self.seed("no_conformidades", estado="Cerrada")
        self.seed("no_conformidades", descripcion="Otra")
        ok = await self.call(mcp_actor(ADMIN), "qms_list",
                             {"module": "no_conformidades", "filters": {"estado": "Cerrada"}})
        self.assertEqual(1, ok.structured_content["total"])
        # Legacy free-text states are listed but deliberately not filterable.
        legacy = await self.call(mcp_actor(ADMIN), "qms_list",
                                 {"module": "no_conformidades", "filters": {"estado": "Pendiente de revisar"}})
        self.assertTrue(legacy.is_error)
        self.assertIn("Cerrada", error_text(legacy))

    def test_enum_coercion_lives_in_the_registry_not_in_operations(self) -> None:
        from app.mcp_server import operations

        self.assertFalse(hasattr(operations, "_ENUM_FILTERS"))

    async def test_bad_filters_give_clean_errors(self) -> None:
        cases = [
            ("no_conformidades", {"color": "rojo"}, "color"),
            ("documentos", {"title": "x"}, "title"),
            ("no_conformidades", {"fecha_detectada": "ayer"}, "fecha_detectada"),
            ("auditorias", {"estado": "NOPE"}, "estado"),
            ("satisfaccion_clientes", {"puntuacion_minima": "alta"}, "puntuacion_minima"),
        ]
        for slug, filters, word in cases:
            with self.subTest(module=slug, filters=filters):
                result = await self.call(mcp_actor(ADMIN), "qms_list",
                                         {"module": slug, "filters": filters})
                self.assertTrue(result.is_error)
                self.assertIn(word, error_text(result))
                self.assertNotIn("Traceback", error_text(result))


class PagingTestCase(McpDbCase):
    async def test_pages_are_bounded(self) -> None:
        for slug in ("no_conformidades", "auditorias"):
            for n in range(3):
                self.seed(slug, **({"descripcion": f"NC {n}"} if slug == "no_conformidades"
                                   else {"auditor": f"A{n}"}))
        with patch.object(crud, "MAX_PER_PAGE", 2):
            for slug in ("no_conformidades", "auditorias"):
                with self.subTest(module=slug):
                    first = (await self.call(mcp_actor(ADMIN), "qms_list",
                             {"module": slug, "per_page": 1000})).structured_content
                    self.assertEqual((3, 2, 2, True), (first["total"], first["per_page"],
                                                        len(first["items"]), first["has_more"]))
                    last = (await self.call(mcp_actor(ADMIN), "qms_list",
                            {"module": slug, "per_page": 1000, "page": 2})).structured_content
                    self.assertEqual((1, False), (len(last["items"]), last["has_more"]))
                    clamped = (await self.call(mcp_actor(ADMIN), "qms_list",
                               {"module": slug, "page": 0, "per_page": 0})).structured_content
                    self.assertEqual(1, clamped["page"])

    async def test_formerly_in_memory_registers_page_in_the_database(self) -> None:
        from app.mcp_server.registry import MODULES

        unique = {"documentos": "code", "partes_interesadas": "nombre"}
        slugs = ("no_conformidades", "documentos", "capacitaciones",
                 "satisfaccion_clientes", "partes_interesadas")
        for slug in slugs:
            for n in range(3):
                self.seed(slug, **({unique[slug]: f"X-{n}"} if slug in unique else {}))
        for slug in slugs:
            with (self.subTest(module=slug),
                  patch.object(MODULES[slug].service, "list_") as list_everything):
                result = await self.call(mcp_actor(ADMIN), "qms_list",
                                         {"module": slug, "page": 2, "per_page": 2})
                self.assertFalse(result.is_error, result.content)
                page = result.structured_content
                self.assertEqual((3, 2, 1, False), (page["total"], page["page"],
                                                    len(page["items"]), page["has_more"]))
                list_everything.assert_not_called()


class ErrorTestCase(McpDbCase):
    async def test_read_scope_reads_and_write_only_scope_does_not(self) -> None:
        self.seed("no_conformidades")
        ok = await self.call(mcp_actor(ADMIN, scopes=("read",)), "qms_list",
                             {"module": "no_conformidades"})
        self.assertFalse(ok.is_error)
        denied = await self.call(mcp_actor(ADMIN, scopes=("write",)), "qms_list",
                                 {"module": "no_conformidades"})
        self.assertTrue(denied.is_error)
        self.assertIn("No tienes permiso", error_text(denied))

    async def test_policy_still_applies_to_the_role(self) -> None:
        self.seed("documentos")
        denied = await self.call(mcp_actor(OPERATIVO), "qms_list", {"module": "documentos"})
        self.assertTrue(denied.is_error)
        self.assertIn("No tienes permiso", error_text(denied))

    async def test_unknown_module_and_unknown_id(self) -> None:
        unknown = await self.call(mcp_actor(ADMIN), "qms_list", {"module": "nada"})
        self.assertTrue(unknown.is_error)
        self.assertIn("nada", error_text(unknown))
        self.assertIn("no_conformidades", error_text(unknown))
        missing = await self.call(mcp_actor(ADMIN), "qms_get",
                                  {"module": "no_conformidades", "id": 999})
        self.assertTrue(missing.is_error)
        self.assertIn("No conformidad no encontrada.", error_text(missing))

    async def test_unexpected_errors_are_generic(self) -> None:
        with (
            patch.object(nonconformities, "list_page", side_effect=RuntimeError("secret boom")),
            self.assertLogs("mcp.server.mcpserver.server", level="ERROR") as logged,
        ):
            result = await self.call(mcp_actor(ADMIN), "qms_list", {"module": "no_conformidades"})
        self.assertIn("unexpected exception", logged.output[0])  # the traceback stays on the server
        self.assertTrue(result.is_error)
        self.assertNotIn("secret boom", error_text(result))
        self.assertNotIn("Traceback", error_text(result))


if __name__ == "__main__":
    unittest.main()
