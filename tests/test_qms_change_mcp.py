"""The MCP modules of the changes to the QMS: ``cambios_sgc`` and ``tareas_cambio`` (CP-2).

Decision CP5 of ``change-planning``: an agent creates, updates, lists and gets
changes and their tasks, whose writes take the adapter's date; it never
approves, implements, reviews the effectiveness of, cancels or deletes a
change, so neither the state nor its columns are writable fields and the
modules offer no such step.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from mcp_support import ADMIN, AUDITOR, OPERATIVO, SEEDS, McpDbCase, mcp_actor
from test_qms_changes import STATE_FIELDS, TODAY

from app.extensions import db
from app.models import CHANGE_LINKS, EstadoCambio, EstadoTareaCambio, OrigenCambio, Person, User
from app.services.actor import Actor
from app.services.policy import Resource

SLUGS = ("cambios_sgc", "tareas_cambio")
WEB_ONLY = ("delete", "approve", "implement", "review_effectiveness", "cancel")


def registry():
    from app.mcp_server import registry as module

    return module


def qc():
    from app.services import qms_changes

    return qms_changes


def error_text(result) -> str:
    return result.content[0].text


class RegistryTestCase(unittest.TestCase):
    def test_both_modules_share_the_changes_resource_and_offer_no_web_only_step(self) -> None:
        modules = registry().MODULES
        for slug in SLUGS:
            with self.subTest(module=slug):
                module = modules[slug]
                self.assertEqual(Resource.QMS_CHANGES, module.resource)
                for name in WEB_ONLY:
                    self.assertFalse(hasattr(module.service, name), name)
                # A task's own ``estado`` is writable; a change's never is (below).
                self.assertFalse((set(STATE_FIELDS) - {"estado"})
                                 & {f.name for f in module.fields})

    def test_the_change_fields_are_its_writable_columns_and_lists(self) -> None:
        module = registry().MODULES["cambios_sgc"]
        described = {f.name: f for f in module.fields}
        self.assertEqual(qc().CHANGE_WRITABLE, set(described))
        self.assertEqual(qc().CHANGE_REQUIRED, {f.name for f in module.fields if f.required})
        self.assertNotIn("estado", described)
        self.assertEqual(("enum", tuple(OrigenCambio.__members__), OrigenCambio),
                         (described["origen"].type, described["origen"].allowed,
                          described["origen"].members))
        for name in ("procesos", "documentos"):
            self.assertEqual("integer_list", described[name].type)
        for name in ("fecha_propuesta", "fecha_prevista"):
            self.assertEqual("date", described[name].type)
        for column in ("accion_revision_id", "no_conformidad_id", "auditoria_id", "riesgo_id",
                       "oportunidad_id", "cuestion_id"):
            with self.subTest(link=column):
                self.assertEqual("integer", described[column].type)
                self.assertIn(f"origen {CHANGE_LINKS[column][0].name}", described[column].note)
        self.assertIn("aprobado", described["titulo"].note)
        self.assertIn("cambio_sgc", described["accion_revision_id"].note)
        self.assertIn("vigente", described["cuestion_id"].note)

    def test_the_task_fields_and_both_filter_sets(self) -> None:
        modules = registry().MODULES
        tasks = {f.name: f for f in modules["tareas_cambio"].fields}
        self.assertEqual(qc().TASK_SPEC.writable, set(tasks))
        self.assertEqual(("enum", tuple(EstadoTareaCambio.__members__)),
                         (tasks["estado"].type, tasks["estado"].allowed))
        self.assertEqual(
            {"estado": "enum", "origen": "enum", "responsable_id": "integer",
             "proceso_id": "integer", "documento_id": "integer", "fuera_de_plazo": "boolean",
             "texto": "string"},
            {f.name: f.type for f in modules["cambios_sgc"].filters})
        self.assertEqual(
            {"cambio_id": "integer", "estado": "enum", "responsable_id": "integer",
             "vencida": "boolean"},
            {f.name: f.type for f in modules["tareas_cambio"].filters})
        state = {f.name: f for f in modules["cambios_sgc"].filters}["estado"]
        self.assertEqual((tuple(EstadoCambio.__members__), EstadoCambio),
                         (state.allowed, state.members))
        for word in ("web", "aprobado", "implantado", "eficacia_revisada", "cancelado"):
            self.assertIn(word, state.note)


class ToolsTestCase(McpDbCase):
    def change_data(self, owner: int, **overrides) -> dict:
        return {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos",
                "origen": "otro", "proposito": "Trazabilidad", "responsable_id": owner,
                "fecha_propuesta": "2026-10-01"} | overrides

    def web_admin(self) -> Actor:
        user = User(username="direccion", password="x", role=ADMIN)
        db.session.add(user)
        db.session.flush()
        db.session.add(Person(nombre="Dirección", user_id=user.id))
        db.session.commit()
        return Actor(user_id=user.id, label="direccion", role=ADMIN, channel="web")

    async def test_changes_and_tasks_round_trip(self) -> None:
        owner = self.seed("personas")
        sales = self.seed("procesos")
        manual = self.seed("documentos")
        who = mcp_actor(AUDITOR)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "cambios_sgc", "data": (
                self.change_data(owner, procesos=[sales], documentos=[manual],
                                 fecha_prevista="2026-10-20", consecuencias="Formación"))})
            self.assertFalse(created.is_error, created.content)
            record = created.structured_content
            self.assertEqual(("Propuesto", "Otro", [sales], [manual], False, "2026-10-20"),
                             tuple(record[k] for k in ("estado", "origen", "procesos",
                                                       "documentos", "fuera_de_plazo",
                                                       "fecha_prevista")))
            updated = await self.call(who, "qms_update", {
                "module": "cambios_sgc", "id": record["id"],
                "data": {"titulo": "ERP", "procesos": [], "fecha_prevista": "2026-11-30"}})
            self.assertFalse(updated.is_error, updated.content)
            self.assertEqual(("ERP", [], "2026-11-30"), tuple(
                updated.structured_content[k] for k in ("titulo", "procesos", "fecha_prevista")))
            task = await self.call(who, "qms_create", {"module": "tareas_cambio", "data": {
                "cambio_id": record["id"], "descripcion": "Migrar los pedidos",
                "responsable_id": owner, "fecha_limite": "2026-10-20"}})
            self.assertFalse(task.is_error, task.content)
            self.assertEqual(("Pendiente", True), (task.structured_content["estado"],
                                                    task.structured_content["vencida"]))
            done = await self.call(who, "qms_update", {
                "module": "tareas_cambio", "id": task.structured_content["id"],
                "data": {"estado": "completada", "fecha_completada": "2026-10-21"}})
            self.assertFalse(done.is_error, done.content)
            self.assertEqual(("Completada", False), (done.structured_content["estado"],
                                                      done.structured_content["vencida"]))
            future = await self.call(who, "qms_create", {"module": "cambios_sgc", "data": (
                self.change_data(owner, fecha_propuesta="2026-10-22"))})
            self.assertTrue(future.is_error)
            self.assertIn("futura", error_text(future))
            for slug, filters, total in (
                    ("cambios_sgc", {"estado": "propuesto", "origen": "otro",
                                     "responsable_id": owner, "documento_id": manual,
                                     "fuera_de_plazo": False, "texto": "erp"}, 1),
                    ("cambios_sgc", {"proceso_id": sales}, 0),
                    ("cambios_sgc", {"fuera_de_plazo": True}, 0),
                    ("tareas_cambio", {"cambio_id": record["id"], "estado": "completada",
                                       "responsable_id": owner, "vencida": False}, 1),
                    ("tareas_cambio", {"vencida": True}, 0)):
                with self.subTest(module=slug, filters=filters):
                    listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                             {"module": slug, "filters": filters})
                    self.assertFalse(listed.is_error, listed.content)
                    self.assertEqual(total, listed.structured_content["total"])
            got = await self.call(mcp_actor(OPERATIVO), "qms_get",
                                  {"module": "cambios_sgc", "id": record["id"]})
            self.assertEqual(updated.structured_content["titulo"],
                             got.structured_content["titulo"])

    async def test_the_state_and_the_web_only_steps_stay_out_of_reach(self) -> None:
        owner = self.seed("personas")
        admin = mcp_actor(ADMIN)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(admin, "qms_create", {"module": "cambios_sgc",
                                                            "data": self.change_data(owner)})
            change_id = created.structured_content["id"]
            for key, value in (("estado", "aprobado"), ("fecha_aprobacion", "2026-10-21"),
                               ("aprobado_por_id", owner), ("eficacia", "eficaz"),
                               ("motivo_cancelacion", "No")):
                with self.subTest(field=key):
                    refused = await self.call(admin, "qms_create", {
                        "module": "cambios_sgc", "data": self.change_data(owner, **{key: value})})
                    self.assertTrue(refused.is_error)
                    self.assertIn("Campos no permitidos", error_text(refused))
                    refused = await self.call(admin, "qms_update", {
                        "module": "cambios_sgc", "id": change_id, "data": {key: value}})
                    self.assertTrue(refused.is_error)
                    self.assertIn("Campos no permitidos", error_text(refused))
            tools = {tool.name for tool in await self.list_tools()}
            self.assertFalse({name for name in tools if "delete" in name or "approve" in name})
            # Approved on the web, its plan is fixed for the agent too.
            data = {"consecuencias": "Formación", "integridad": "Sin pausa",
                    "recursos": "Licencias", "responsabilidades": "Dirección",
                    "fecha_prevista": date(2026, 12, 31)}
            qc().changes.update(db.session, Actor(user_id=None, label="cli", role=ADMIN,
                                                  channel="cli"), change_id, data, today=TODAY)
            qc().tasks.create(db.session, Actor(user_id=None, label="cli", role=ADMIN,
                                                channel="cli"),
                              {"cambio_id": change_id, "descripcion": "Migrar",
                               "responsable_id": owner, "fecha_limite": date(2026, 11, 30)},
                              today=TODAY)
            qc().approve(db.session, self.web_admin(), change_id, today=TODAY)
            db.session.commit()
            fixed = await self.call(admin, "qms_update", {
                "module": "cambios_sgc", "id": change_id, "data": {"titulo": "Otro"}})
            self.assertTrue(fixed.is_error)
            self.assertIn("aprobado", error_text(fixed))
            moved = await self.call(admin, "qms_update", {
                "module": "cambios_sgc", "id": change_id,
                "data": {"comunicacion": "Correo a la plantilla"}})
            self.assertFalse(moved.is_error, moved.content)
            self.assertEqual("Aprobado", moved.structured_content["estado"])

    async def test_operativo_reads_but_never_writes(self) -> None:
        self.seed("personas")
        change_id = self.seed("cambios_sgc")
        for slug, data in (("cambios_sgc", SEEDS["cambios_sgc"]),
                           ("tareas_cambio", SEEDS["tareas_cambio"] | {"cambio_id": change_id})):
            with self.subTest(module=slug):
                refused = await self.call(mcp_actor(OPERATIVO), "qms_create",
                                          {"module": slug, "data": data})
                self.assertTrue(refused.is_error)
                self.assertIn("No tienes permiso", error_text(refused))
                listed = await self.call(mcp_actor(OPERATIVO), "qms_list", {"module": slug})
                self.assertFalse(listed.is_error, listed.content)


if __name__ == "__main__":
    unittest.main()
