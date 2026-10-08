"""MCP write tools: ``qms_create`` and ``qms_update`` go through the services."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy.orm import Session

from mcp_support import ADMIN, AUDITOR, OPERATIVO, SEEDS, McpDbCase, mcp_actor

from app.extensions import db
from app.mcp_server import operations
from app.mcp_server.registry import MODULES
from app.models import AuditLog, NoConformidad, User
from app.services import audit

JSON_REGISTERS = ("roles_responsabilidades", "riesgos_oportunidades", "recursos_capacitacion",
                  "procesos", "indicadores_auditoria")


def error_text(result) -> str:
    return result.content[0].text


def audit_rows():
    return db.session.query(AuditLog).order_by(AuditLog.id).all()


class WriteToolsRegistrationTestCase(McpDbCase):
    async def test_exposes_exactly_the_five_tools_and_no_delete(self) -> None:
        tools = {t.name: t for t in await self.list_tools()}
        self.assertEqual(
            {"qms_modules", "qms_list", "qms_get", "qms_create", "qms_update"}, set(tools))
        create, update = tools["qms_create"].annotations, tools["qms_update"].annotations
        self.assertEqual((False, False, False), (
            create.read_only_hint, create.destructive_hint, create.idempotent_hint))
        self.assertEqual((False, True, True), (
            update.read_only_hint, update.destructive_hint, update.idempotent_hint))


class EveryModuleTestCase(McpDbCase):
    async def test_every_module_creates_and_updates_with_audit_and_stamping(self) -> None:
        actor = mcp_actor(ADMIN)
        for slug, module in MODULES.items():
            with self.subTest(module=slug):
                created = await self.call(actor, "qms_create", {"module": slug, "data": SEEDS[slug]})
                self.assertFalse(created.is_error, created.content)
                record = created.structured_content
                self.assertTrue(all(not audit.is_sensitive(key) for key in record))
                json.dumps(record)
                field = next(f.name for f in module.fields if f.required and f.type == "string")
                updated = await self.call(actor, "qms_update", {
                    "module": slug, "id": record["id"], "data": {field: "Cambiado"}})
                self.assertFalse(updated.is_error, updated.content)
                self.assertEqual("Cambiado", updated.structured_content[field])
                self.assertEqual(record["id"], updated.structured_content["id"])
        db.session.expire_all()
        rows = audit_rows()
        self.assertEqual(2 * len(MODULES), len(rows))
        self.assertEqual({"mcp"}, {r.channel for r in rows})
        self.assertEqual({7}, {r.actor_user_id for r in rows})
        self.assertEqual({"create", "update"}, {r.action for r in rows})

    async def test_attribution_is_stamped_from_the_token_owner(self) -> None:
        result = await self.call(mcp_actor(ADMIN, user_id=7), "qms_create",
                                 {"module": "no_conformidades", "data": SEEDS["no_conformidades"]})
        db.session.expire_all()
        row = db.session.get(NoConformidad, result.structured_content["id"])
        self.assertEqual((7, 7), (row.created_by_id, row.updated_by_id))


class AuthorizationTestCase(McpDbCase):
    async def test_a_read_only_token_cannot_write(self) -> None:
        result = await self.call(mcp_actor(ADMIN, scopes=("read",)), "qms_create",
                                 {"module": "no_conformidades", "data": SEEDS["no_conformidades"]})
        self.assertTrue(result.is_error)
        self.assertIn("No tienes permiso", error_text(result))
        self.assertEqual(([], 0), (audit_rows(), db.session.query(NoConformidad).count()))
        record_id = self.seed("no_conformidades")
        result = await self.call(mcp_actor(ADMIN, scopes=("read",)), "qms_update", {
            "module": "no_conformidades", "id": record_id, "data": {"responsable": "Eva"}})
        self.assertTrue(result.is_error)

    async def test_operativo_cannot_write_the_json_registers_but_can_write_open_ones(self) -> None:
        for slug in JSON_REGISTERS:
            with self.subTest(module=slug):
                result = await self.call(mcp_actor(OPERATIVO), "qms_create",
                                         {"module": slug, "data": SEEDS[slug]})
                self.assertTrue(result.is_error)
                self.assertIn("No tienes permiso", error_text(result))
        ok = await self.call(mcp_actor(OPERATIVO), "qms_create",
                             {"module": "no_conformidades", "data": SEEDS["no_conformidades"]})
        self.assertFalse(ok.is_error)
        auditor = await self.call(mcp_actor(AUDITOR), "qms_create",
                                  {"module": "procesos", "data": SEEDS["procesos"]})
        self.assertFalse(auditor.is_error)


class PeopleTestCase(McpDbCase):
    def user(self) -> User:
        user = User(username="ana.user", password="x", role=OPERATIVO)
        db.session.add(user)
        db.session.commit()
        return user

    async def test_a_person_takes_roles_and_a_user_link(self) -> None:
        from app.models import Person

        role_ids = [self.seed("roles_responsabilidades", rol=name)
                    for name in ("Calidad", "Compras")]
        user_id = self.user().id
        created = await self.call(mcp_actor(AUDITOR), "qms_create", {"module": "personas", "data": {
            "nombre": "Ana", "email": "Ana@Example.com", "user_id": user_id, "rol_ids": role_ids}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("ana@example.com", user_id, True, role_ids),
                         (record["email"], record["user_id"], record["activo"], record["rol_ids"]))
        got = await self.call(mcp_actor(AUDITOR), "qms_get",
                              {"module": "personas", "id": record["id"]})
        stamps = {"created_at", "updated_at"}  # SQLite reloads them without a time zone
        self.assertEqual({k: v for k, v in record.items() if k not in stamps},
                         {k: v for k, v in got.structured_content.items() if k not in stamps})
        updated = await self.call(mcp_actor(AUDITOR), "qms_update", {
            "module": "personas", "id": record["id"],
            "data": {"rol_ids": role_ids[1:], "activo": False}})
        self.assertFalse(updated.is_error, updated.content)
        self.assertEqual((False, role_ids[1:]), (updated.structured_content["activo"],
                                                 updated.structured_content["rol_ids"]))
        db.session.expire_all()
        self.assertEqual(role_ids[1:], db.session.get(Person, record["id"]).rol_ids)
        rows = audit_rows()
        self.assertEqual([role_ids, role_ids[1:]],
                         [rows[-2].after["rol_ids"], rows[-1].after["rol_ids"]])

    async def test_people_errors_are_clean(self) -> None:
        user_id = self.user().id
        self.seed("personas", user_id=user_id)
        cases = [
            (mcp_actor(OPERATIVO), SEEDS["personas"], "No tienes permiso"),
            (mcp_actor(ADMIN), {"nombre": "Eva", "rol_ids": [999]}, "Roles desconocidos: 999."),
            (mcp_actor(ADMIN), {"nombre": "Eva", "user_id": 999}, "El usuario indicado no existe."),
            (mcp_actor(ADMIN), {"nombre": "Eva", "user_id": user_id},
             "Ese usuario ya está vinculado a otra persona."),
        ]
        for who, data, expected in cases:
            with self.subTest(expected=expected):
                result = await self.call(who, "qms_create", {"module": "personas", "data": data})
                self.assertTrue(result.is_error)
                self.assertIn(expected, error_text(result))
                self.assertNotIn("Traceback", error_text(result))


class ErrorTestCase(McpDbCase):
    async def test_validation_and_conflict_errors_are_clean(self) -> None:
        self.seed("documentos")
        cases = [
            ("qms_create", {"module": "no_conformidades", "data": {"descripcion": "x"}},
             "Faltan campos obligatorios"),
            ("qms_create", {"module": "no_conformidades",
                            "data": SEEDS["no_conformidades"] | {"id": 1}}, "Campos no permitidos"),
            ("qms_create", {"module": "no_conformidades",
                            "data": SEEDS["no_conformidades"] | {"fecha_detectada": "ayer"}},
             "fecha_detectada"),
            ("qms_create", {"module": "documentos", "data": SEEDS["documentos"]},
             "Ya existe un documento con ese código."),
            ("qms_update", {"module": "no_conformidades", "id": 999, "data": {}},
             "No conformidad no encontrada."),
            ("qms_update", {"module": "no_conformidades", "id": self.seed("no_conformidades"),
                            "data": {"estado": "Inventado"}}, "El estado no es válido."),
        ]
        for tool, arguments, expected in cases:
            with self.subTest(tool=tool, expected=expected):
                result = await self.call(mcp_actor(ADMIN), tool, arguments)
                self.assertTrue(result.is_error)
                self.assertIn(expected, error_text(result))
                self.assertNotIn("Traceback", error_text(result))
        self.assertEqual(1, db.session.query(NoConformidad).count())  # only the seeded row

    async def test_a_failing_call_is_rolled_back_and_not_committed(self) -> None:
        commit = patch.object(Session, "commit", autospec=True, side_effect=Session.commit)
        with (
            patch.object(operations, "serialise", side_effect=RuntimeError("boom")),
            self.assertLogs("mcp.server.mcpserver.server", level="ERROR"),
            commit as committed,
        ):
            result = await self.call(mcp_actor(ADMIN), "qms_create", {
                "module": "no_conformidades", "data": SEEDS["no_conformidades"]})
        self.assertTrue(result.is_error)
        self.assertNotIn("boom", error_text(result))
        committed.assert_not_called()
        db.session.expire_all()
        self.assertEqual(([], 0), (audit_rows(), db.session.query(NoConformidad).count()))

    async def test_a_successful_write_commits_once(self) -> None:
        with patch.object(Session, "commit", autospec=True, side_effect=Session.commit) as committed:
            result = await self.call(mcp_actor(ADMIN), "qms_create", {
                "module": "no_conformidades", "data": SEEDS["no_conformidades"]})
        self.assertFalse(result.is_error)
        self.assertEqual(1, committed.call_count)


if __name__ == "__main__":
    unittest.main()
