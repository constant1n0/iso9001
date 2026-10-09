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
from app.models import AuditLog, EstadoNoConformidad, NoConformidad, User
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
        # Plus the nonconformity's move to "Acción planificada" on its first action.
        self.assertEqual(2 * len(MODULES) + 1, len(rows))
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


PERSON_LINKS = (("capacitaciones", "persona_id"), ("no_conformidades", "responsable_id"),
                ("auditorias", "auditor_id"))


class PersonLinksTestCase(McpDbCase):
    async def test_records_round_trip_the_person_they_cite(self) -> None:
        person_id = self.seed("personas")
        actor = mcp_actor(ADMIN)
        for slug, key in PERSON_LINKS:
            with self.subTest(module=slug):
                created = await self.call(actor, "qms_create", {
                    "module": slug, "data": SEEDS[slug] | {key: person_id}})
                self.assertFalse(created.is_error, created.content)
                record_id = created.structured_content["id"]
                self.assertEqual(person_id, created.structured_content[key])
                fetched = await self.call(actor, "qms_get", {"module": slug, "id": record_id})
                self.assertEqual(person_id, fetched.structured_content[key])
                cleared = await self.call(actor, "qms_update", {
                    "module": slug, "id": record_id, "data": {key: None}})
                self.assertFalse(cleared.is_error, cleared.content)
                self.assertIsNone(cleared.structured_content[key])
                refused = await self.call(actor, "qms_create", {
                    "module": slug, "data": SEEDS[slug] | {key: 999}})
                self.assertTrue(refused.is_error)
                self.assertIn("no corresponde a ninguna persona", error_text(refused))

    async def test_qms_modules_describes_each_link_as_an_optional_integer(self) -> None:
        result = await self.call(mcp_actor(ADMIN), "qms_modules")
        modules = {m["slug"]: m for m in result.structured_content["modules"]}
        for slug, key in PERSON_LINKS:
            with self.subTest(module=slug):
                described = {f["name"]: f for f in modules[slug]["fields"]}
                self.assertEqual({"name": key, "type": "integer", "required": False},
                                 described[key])


class CompetenceTestCase(McpDbCase):
    async def test_requirements_and_records_round_trip(self) -> None:
        rol_id, training_id = self.seed("roles_responsabilidades"), self.seed("capacitaciones")
        ana, eva = self.seed("personas"), self.seed("personas", nombre="Eva")
        actor = mcp_actor(AUDITOR)
        created = await self.call(actor, "qms_create", {"module": "competencias_requeridas", "data": {
            "rol_id": rol_id, "tipo": "habilidad", "descripcion": "Manejo de carretilla",
            "criterio": "Carné vigente"}})
        self.assertFalse(created.is_error, created.content)
        requirement = created.structured_content
        self.assertEqual((rol_id, "Habilidad", "Carné vigente"),
                         (requirement["rol_id"], requirement["tipo"], requirement["criterio"]))
        created = await self.call(actor, "qms_create", {"module": "competencias_acreditadas", "data": {
            "persona_id": ana, "requisito_id": requirement["id"], "evidencia": "Carné 42",
            "capacitacion_id": training_id, "fecha_obtencion": "2026-03-10",
            "fecha_caducidad": "2031-03-10"}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("2026-03-10", "2031-03-10", "Pendiente", training_id),
                         (record["fecha_obtencion"], record["fecha_caducidad"],
                          record["evaluacion_eficacia"], record["capacitacion_id"]))
        evaluated = await self.call(actor, "qms_update", {
            "module": "competencias_acreditadas", "id": record["id"], "data": {
                "evaluacion_eficacia": "eficaz", "fecha_evaluacion": "2026-06-01",
                "evaluador_id": eva}})
        self.assertFalse(evaluated.is_error, evaluated.content)
        record = evaluated.structured_content
        self.assertEqual(("Eficaz", "2026-06-01", eva), (
            record["evaluacion_eficacia"], record["fecha_evaluacion"], record["evaluador_id"]))
        fetched = await self.call(actor, "qms_get",
                                  {"module": "competencias_acreditadas", "id": record["id"]})
        stamps = {"created_at", "updated_at"}  # SQLite reloads them without a time zone
        self.assertEqual({k: v for k, v in record.items() if k not in stamps},
                         {k: v for k, v in fetched.structured_content.items() if k not in stamps})
        for slug, filters in (
            ("competencias_requeridas", {"rol_id": rol_id, "tipo": "habilidad"}),
            ("competencias_acreditadas", {"persona_id": ana, "requisito_id": requirement["id"],
                                          "evaluacion_eficacia": "eficaz"}),
        ):
            with self.subTest(module=slug):
                listed = await self.call(actor, "qms_list", {"module": slug, "filters": filters})
                self.assertFalse(listed.is_error, listed.content)
                self.assertEqual(1, listed.structured_content["total"])
        self.assertEqual(["create", "create", "update"], [row.action for row in audit_rows()
                                                         if row.channel == "mcp"])

    async def test_competence_errors_are_clean(self) -> None:
        ana = self.seed("personas")
        record = {"persona_id": ana, "evidencia": "Certificado", "fecha_obtencion": "2026-03-10"}
        cases = [
            (mcp_actor(OPERATIVO), "competencias_requeridas", SEEDS["competencias_requeridas"],
             "No tienes permiso"),
            (mcp_actor(ADMIN), "competencias_requeridas",
             {"rol_id": 999, "tipo": "formacion", "descripcion": "Curso"},
             "El campo «rol_id» no corresponde a ningún rol."),
            (mcp_actor(ADMIN), "competencias_acreditadas", record | {"fecha_caducidad": "2026-03-01"},
             "La fecha de caducidad no puede ser anterior a la fecha de obtención."),
            (mcp_actor(ADMIN), "competencias_acreditadas", record | {"evaluacion_eficacia": "eficaz"},
             "necesita la fecha de evaluación y el evaluador"),
            (mcp_actor(ADMIN), "competencias_acreditadas", record | {"capacitacion_id": 999},
             "El campo «capacitacion_id» no corresponde a ninguna capacitación."),
        ]
        for who, slug, data, expected in cases:
            with self.subTest(expected=expected):
                result = await self.call(who, "qms_create", {"module": slug, "data": data})
                self.assertTrue(result.is_error)
                self.assertIn(expected, error_text(result))
                self.assertNotIn("Traceback", error_text(result))
        self.assertEqual([], [row for row in audit_rows() if row.channel == "mcp"])

class NonconformityTestCase(McpDbCase):
    async def test_new_fields_round_trip_and_the_state_starts_open(self) -> None:
        data = SEEDS["no_conformidades"] | {
            "origen": "proveedor", "gravedad": "observacion",
            "contencion": "Material apartado", "causa_raiz": "Proveedor sin control"}
        created = await self.call(mcp_actor(OPERATIVO), "qms_create",
                                  {"module": "no_conformidades", "data": data})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(
            ("Abierta", "Proveedor", "Observación", "Material apartado",
             "Proveedor sin control", None),
            tuple(record[k] for k in ("estado", "origen", "gravedad", "contencion",
                                      "causa_raiz", "motivo_cancelacion")),
        )
        updated = await self.call(mcp_actor(OPERATIVO), "qms_update", {
            "module": "no_conformidades", "id": record["id"], "data": {"gravedad": "mayor"}})
        self.assertEqual("Mayor", updated.structured_content["gravedad"])

    async def test_a_cancelled_record_is_read_only(self) -> None:
        record_id = self.seed("no_conformidades")
        self.force_nc_state(record_id, EstadoNoConformidad.cancelada)
        result = await self.call(mcp_actor(ADMIN), "qms_update", {
            "module": "no_conformidades", "id": record_id, "data": {"responsable": "Eva"}})
        self.assertTrue(result.is_error)
        self.assertIn("un administrador puede reabrirla", error_text(result))
        self.assertEqual([], [row for row in audit_rows() if row.channel == "mcp"])


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
            ("qms_create", {"module": "no_conformidades",
                            "data": SEEDS["no_conformidades"] | {"estado": "cerrada"}},
             "Campos no permitidos: estado."),
            ("qms_create", {"module": "no_conformidades",
                            "data": SEEDS["no_conformidades"] | {"gravedad": "Mayor"}},
             "gravedad"),
            ("qms_update", {"module": "no_conformidades", "id": self.seed("no_conformidades"),
                            "data": {"estado": "cerrada"}}, "Campos no permitidos: estado."),
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
