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
    async def test_pages_are_bounded_for_both_listing_styles(self) -> None:
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
            patch.object(nonconformities, "list_", side_effect=RuntimeError("secret boom")),
            self.assertLogs("mcp.server.mcpserver.server", level="ERROR") as logged,
        ):
            result = await self.call(mcp_actor(ADMIN), "qms_list", {"module": "no_conformidades"})
        self.assertIn("unexpected exception", logged.output[0])  # the traceback stays on the server
        self.assertTrue(result.is_error)
        self.assertNotIn("secret boom", error_text(result))
        self.assertNotIn("Traceback", error_text(result))


if __name__ == "__main__":
    unittest.main()
