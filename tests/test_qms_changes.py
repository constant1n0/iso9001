"""Planned changes to the QMS: the change register and its transitions (CP-2).

ISO 9001 clause 6.3, decisions CP1 to CP6 of ``change-planning``. A change is
proposed with its origin and, optionally, the record behind it (only the link
of that origin: a review action of type ``cambio_sgc``, a nonconformity, an
audit, a risk, an opportunity or a ``vigente`` context issue), its purpose, the
6.3 considerations, an owner, its dates and the processes and documents it
affects, whose ids travel in its audit rows. Only the transitions move it:
``approve`` (administrators; the considerations, a planned date and a live
task), ``implement`` (administrators and auditors; every task closed, one
completed), ``review_effectiveness`` (administrators and auditors; never by
the owner) and ``cancel`` (administrators; with a reason), all web only. What
an update may change follows the state, and only a proposed change is deleted,
with its tasks. ``attention``, ``in_period`` and ``for_origin`` read what the
dashboard, the management review and the origin pages show. The SQLite cases
run with the audit flush guard; the database cases also run on PostgreSQL.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text

from mcp_support import ADMIN, AUDITOR, OPERATIVO, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    AccionRevision, Auditoria, AuditLog, CambioSGC, CuestionContexto, Document,
    DocumentCategory, EficaciaCambio, EstadoCambio, EstadoTareaCambio, NoConformidad,
    Oportunidad, OrigenCambio, Person, ProcesoOperacion, RevisionDireccion, Riesgo,
    TareaCambio, User,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
TODAY = date(2026, 10, 21)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
CHANGE = {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos por un ERP",
          "origen": "otro", "proposito": "Trazabilidad de los pedidos",
          "fecha_propuesta": date(2026, 10, 1)}
# What approval needs besides a task: the four considerations and a planned date.
PLAN = {"consecuencias": "Formación del equipo comercial",
        "integridad": "Ambos sistemas conviven un mes",
        "recursos": "Licencias y una consultora", "responsabilidades": "Dirección comercial",
        "fecha_prevista": date(2026, 12, 31)}
TASK = {"descripcion": "Migrar los pedidos abiertos", "fecha_limite": date(2026, 11, 30)}
# The state and the columns of each stage: never part of a write (CP2).
STATE_FIELDS = ("estado", "aprobado_por_id", "fecha_aprobacion", "fecha_implantacion",
                "eficacia", "fecha_eficacia", "revisor_eficacia_id", "comentario_eficacia",
                "motivo_cancelacion", "fecha_cancelacion")
# Each link to the record behind a change and the origin it needs.
LINKS = {"accion_revision_id": "revision_direccion", "no_conformidad_id": "no_conformidad",
         "auditoria_id": "auditoria", "riesgo_id": "riesgo", "oportunidad_id": "oportunidad",
         "cuestion_id": "cuestion_contexto"}


def qc():
    from app.services import qms_changes

    return qms_changes


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", **values) -> int:
        return self.insert(Person.__table__, nombre=nombre, **({"activo": True} | values))

    def web(self, role=ADMIN, nombre: str = "Dirección") -> tuple[Actor, int]:
        """A web user of ``role`` linked to a person; returns the actor and the person."""
        user_id = self.insert(User.__table__, username=f"user {nombre}", password="x",
                              role=role)
        return (Actor(user_id=user_id, label=nombre, role=role, channel="web"),
                self.person(nombre, user_id=user_id))

    def admin(self) -> Actor:
        """One web administrator linked to a person, made on first use."""
        if not hasattr(self, "_admin"):
            self._admin = self.web(ADMIN, "Dirección")[0]
        return self._admin

    def change(self, who: Actor = CLI, **overrides) -> CambioSGC:
        data = CHANGE | overrides
        if "responsable_id" not in data:
            data["responsable_id"] = self.person("Responsable del cambio")
        created = qc().changes.create(db.session, who, data, today=TODAY)
        db.session.commit()
        return created

    def edit(self, change_id: int, data: dict, who: Actor = CLI) -> CambioSGC:
        found = qc().changes.update(db.session, who, change_id, data, today=TODAY)
        db.session.commit()
        return found

    def task(self, change_id: int, who: Actor = CLI, **overrides) -> TareaCambio:
        data = TASK | {"cambio_id": change_id} | overrides
        if "responsable_id" not in data:
            data["responsable_id"] = self.person("Responsable de la tarea")
        created = qc().tasks.create(db.session, who, data, today=TODAY)
        db.session.commit()
        return created

    def complete(self, task_id: int, fecha: date = TODAY) -> TareaCambio:
        found = qc().tasks.update(db.session, CLI, task_id, {
            "estado": "completada", "fecha_completada": fecha}, today=TODAY)
        db.session.commit()
        return found

    def approved(self, today: date = TODAY, **overrides) -> CambioSGC:
        """A change with its plan and one pending task, approved on ``today``."""
        change = self.change(**(PLAN | overrides))
        self.task(change.id)
        qc().approve(db.session, self.admin(), change.id, today=today)
        db.session.commit()
        return change

    def implemented(self, fecha: date = TODAY, approved_on: date = TODAY,
                    **overrides) -> CambioSGC:
        """An approved change whose task is completed, implemented on ``fecha``."""
        change = self.approved(today=approved_on, **overrides)
        for task in change.tareas:
            self.complete(task.id)
        qc().implement(db.session, self.admin(), change.id, fecha=fecha, today=TODAY)
        db.session.commit()
        return change

    def reviewed(self, **overrides) -> CambioSGC:
        change = self.implemented(**overrides)
        reviewer = self.web(AUDITOR, f"Auditora {change.id}")[0]
        qc().review_effectiveness(db.session, reviewer, change.id, eficacia="eficaz",
                                  fecha=TODAY, today=TODAY)
        db.session.commit()
        return change

    def cancelled(self, **overrides) -> CambioSGC:
        change = self.change(**overrides)
        qc().cancel(db.session, self.admin(), change.id, motivo="Ya no hace falta",
                    today=TODAY)
        db.session.commit()
        return change

    def process(self, nombre: str = "Ventas") -> int:
        return self.insert(ProcesoOperacion.__table__, proceso=nombre)

    def document(self, code: str = "PR-07", **values) -> int:
        return self.insert(Document.__table__, title=f"Documento {code}", code=code,
                           category=DocumentCategory.OTRO, **values)

    def origins(self, owner: int) -> dict[str, int]:
        """One record of each kind a change may name, keyed by the change's column."""
        review = self.insert(RevisionDireccion.__table__, fecha=date(2026, 7, 10),
                             periodo_desde=date(2026, 4, 1), periodo_hasta=date(2026, 6, 30))
        return {
            "accion_revision_id": self.insert(
                AccionRevision.__table__, revision_id=review, tipo="cambio_sgc",
                descripcion="Implantar un ERP", responsable_id=owner,
                fecha_limite=date(2026, 12, 31)),
            "no_conformidad_id": self.insert(NoConformidad.__table__,
                                             descripcion="Pedido perdido",
                                             fecha_detectada=date(2026, 9, 1)),
            "auditoria_id": self.insert(Auditoria.__table__, area_auditada="Ventas",
                                        fecha=date(2026, 9, 2), auditor="Luis",
                                        resultado="Sin trazabilidad"),
            "riesgo_id": self.insert(Riesgo.__table__, titulo="Pérdida de pedidos",
                                     descripcion="Hoja compartida", origen="interno",
                                     fecha_identificacion=date(2026, 9, 3)),
            "oportunidad_id": self.insert(Oportunidad.__table__, titulo="Venta en línea",
                                          descripcion="Pedidos web", origen="externo",
                                          fecha_identificacion=date(2026, 9, 4)),
            "cuestion_id": self.insert(CuestionContexto.__table__, tipo="interna",
                                       categoria="conocimiento", titulo="Saber disperso",
                                       descripcion="Cada comercial lleva su hoja",
                                       efecto="negativo", pertinente=True,
                                       fecha_identificacion=date(2026, 9, 5)),
        }

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    @staticmethod
    def diff(row: AuditLog) -> tuple[dict, dict]:
        """An update row's two sides without the attribution stamps, which always change."""
        stamps = {"updated_at", "updated_by_id"}
        return ({k: v for k, v in row.before.items() if k not in stamps},
                {k: v for k, v in row.after.items() if k not in stamps})

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message

    @staticmethod
    def ids(records) -> list[int]:
        return [record.id for record in records]


class CreateTestCase(Helpers, ServiceBase):
    def test_a_change_starts_proposed_with_stamps_and_its_lists_in_the_audit_row(self) -> None:
        owner = self.person()
        sales, buying = self.process("Ventas"), self.process("Compras")
        procedure = self.document("PR-07")
        withdrawn = self.document("PR-01", withdrawn_at=date(2026, 5, 1))
        created = qc().changes.create(db.session, actor(AUDITOR), CHANGE | PLAN | {
            "titulo": "  Nuevo ERP ", "responsable_id": owner, "comunicacion": "Reunión",
            "procesos": [sales, buying, sales], "documentos": [withdrawn, procedure]},
            today=TODAY)
        db.session.commit()
        self.assertEqual(
            (EstadoCambio.propuesto, OrigenCambio.otro, "Nuevo ERP", owner, "Reunión",
             date(2026, 12, 31), None, 7),
            (created.estado, created.origen, created.titulo, created.responsable_id,
             created.comunicacion, created.fecha_prevista, created.aprobado_por_id,
             created.created_by_id))
        self.assertEqual(sorted([sales, buying]), created.proceso_ids)
        self.assertEqual(sorted([procedure, withdrawn]), created.documento_ids)
        (row,) = self.rows("cambios_sgc")
        self.assertEqual(("create", "Propuesto", sorted([sales, buying]),
                          sorted([procedure, withdrawn])),
                         (row.action, row.after["estado"], row.after["procesos"],
                          row.after["documentos"]))
        self.assertIs(created, qc().changes.get(db.session, actor(OPERATIVO), created.id))

    def test_required_fields_and_bad_values_are_refused(self) -> None:
        owner = self.person()
        valid = CHANGE | {"responsable_id": owner}
        for missing in ("titulo", "descripcion", "origen", "proposito", "responsable_id",
                        "fecha_propuesta"):
            with self.subTest(missing=missing):
                data = {key: value for key, value in valid.items() if key != missing}
                message = self.refused(errors().ValidationError, qc().changes.create, CLI,
                                       data, today=TODAY)
                self.assertIn(missing, message)
        for data in ({"origen": "Otro"}, {"origen": "cliente"}, {"titulo": " "},
                     {"titulo": "x" * 201}, {"descripcion": ""}, {"proposito": " "},
                     {"fecha_propuesta": "2026-10-01"}, {"fecha_prevista": "2026-12-31"},
                     {"responsable_id": "1"}, {"responsable_id": None}, {"riesgo_id": "1"},
                     {"id": 5}, {"created_at": None}, {"tareas": []}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, qc().changes.create, CLI, valid | data,
                             today=TODAY)
        self.assertEqual(0, db.session.query(CambioSGC).count())

    def test_the_proposal_is_never_future_and_the_plan_never_before_it(self) -> None:
        owner = self.person()
        valid = CHANGE | {"responsable_id": owner}
        self.assertEqual(qc().PROPOSED_IN_FUTURE, self.refused(
            errors().ValidationError, qc().changes.create, CLI,
            valid | {"fecha_propuesta": TOMORROW}, today=TODAY))
        self.assertEqual(qc().PLANNED_BEFORE_PROPOSAL, self.refused(
            errors().ValidationError, qc().changes.create, CLI,
            valid | {"fecha_prevista": date(2026, 9, 30)}, today=TODAY))
        same_day = self.change(responsable_id=owner, fecha_propuesta=TODAY,
                               fecha_prevista=TODAY)
        self.assertEqual((TODAY, TODAY), (same_day.fecha_propuesta, same_day.fecha_prevista))
        self.assertEqual(qc().PLANNED_BEFORE_PROPOSAL, self.refused(
            errors().ValidationError, qc().changes.update, CLI, same_day.id,
            {"fecha_prevista": YESTERDAY}, today=TODAY))
        self.assertEqual(qc().PROPOSED_IN_FUTURE, self.refused(
            errors().ValidationError, qc().changes.update, CLI, same_day.id,
            {"fecha_propuesta": TOMORROW, "fecha_prevista": TOMORROW}, today=TODAY))
        with self.assertRaises(ValueError):
            qc().changes.create(db.session, CLI, valid, today="2026-10-21")

    def test_the_state_and_its_columns_are_never_written(self) -> None:
        owner = self.person()
        change = self.change(responsable_id=owner)
        for key in STATE_FIELDS:
            with self.subTest(field=key):
                for call, args in ((qc().changes.create, (CHANGE | {"responsable_id": owner,
                                                                    key: None},)),
                                   (qc().changes.update, (change.id, {key: None}))):
                    message = self.refused(errors().ValidationError, call, CLI, *args,
                                           today=TODAY)
                    self.assertIn(f"Campos no permitidos: {key}", message)
        self.assertEqual(1, db.session.query(CambioSGC).count())

    def test_unknown_keys_are_refused_before_any_lookup(self) -> None:
        message = self.refused(errors().ValidationError, qc().changes.update, CLI, 999,
                               {"estado": "aprobado"}, today=TODAY)
        self.assertIn("Campos no permitidos", message)
        self.refused(errors().NotFound, qc().changes.update, CLI, 999, {"titulo": "Otro"},
                     today=TODAY)
        self.refused(errors().NotFound, qc().changes.get, actor(), 999)

    def test_the_owner_is_a_person_active_when_newly_chosen(self) -> None:
        inactive = self.person("Baja", activo=False)
        for owner, word in ((999, "no corresponde a ninguna persona"), (inactive, "")):
            with self.subTest(owner=owner):
                message = self.refused(errors().ValidationError, qc().changes.create, CLI,
                                       CHANGE | {"responsable_id": owner}, today=TODAY)
                self.assertIn("responsable_id", message)
                self.assertIn(word, message)


class OriginLinkTestCase(Helpers, ServiceBase):
    def test_each_origin_takes_its_own_link_and_lists_its_changes(self) -> None:
        owner = self.person()
        records = self.origins(owner)
        for column, origin in LINKS.items():
            with self.subTest(column=column):
                created = self.change(responsable_id=owner, origen=origin,
                                      **{column: records[column]})
                self.assertEqual(records[column], getattr(created, column))
                self.assertEqual([created.id], self.ids(qc().for_origin(
                    db.session, actor(OPERATIVO), origin, records[column])))
                self.assertEqual([created.id], self.ids(qc().for_origin(
                    db.session, actor(OPERATIVO), OrigenCambio[origin], records[column])))
                self.assertEqual([], qc().for_origin(db.session, actor(), origin, 999))
        self.assertEqual(len(LINKS), db.session.query(CambioSGC).count())
        unlinked = self.change(responsable_id=owner, origen="riesgo")
        self.assertIsNone(unlinked.riesgo_id)

    def test_a_link_with_another_origin_is_refused(self) -> None:
        owner = self.person()
        records = self.origins(owner)
        for column, origin in LINKS.items():
            other = "auditoria" if origin != "auditoria" else "riesgo"
            for origen in ("otro", other):
                with self.subTest(column=column, origen=origen):
                    message = self.refused(errors().ValidationError, qc().changes.create, CLI,
                                           CHANGE | {"responsable_id": owner, "origen": origen,
                                                     column: records[column]}, today=TODAY)
                    self.assertEqual(qc().LINK_NEEDS_ORIGIN.format(
                        key=column, origen=OrigenCambio[origin].value), message)
        self.assertEqual(0, db.session.query(CambioSGC).count())

    def test_a_missing_record_is_refused(self) -> None:
        owner = self.person()
        for column, origin in LINKS.items():
            for missing in (999, 0, -1):
                with self.subTest(column=column, missing=missing):
                    message = self.refused(errors().ValidationError, qc().changes.create, CLI,
                                           CHANGE | {"responsable_id": owner, "origen": origin,
                                                     column: missing}, today=TODAY)
                    self.assertIn(f"«{column}» no corresponde a", message)

    def test_a_review_action_must_propose_a_change_to_the_qms(self) -> None:
        owner = self.person()
        records = self.origins(owner)
        action = records["accion_revision_id"]
        db.session.execute(AccionRevision.__table__.update().where(AccionRevision.id == action)
                           .values(tipo="mejora"))
        db.session.commit()
        self.assertEqual(qc().REVIEW_ACTION_NOT_A_CHANGE, self.refused(
            errors().ValidationError, qc().changes.create, CLI, CHANGE | {
                "responsable_id": owner, "origen": "revision_direccion",
                "accion_revision_id": action}, today=TODAY))

    def test_a_context_issue_must_be_current_when_newly_linked(self) -> None:
        owner = self.person()
        issue = self.origins(owner)["cuestion_id"]
        linked = self.change(responsable_id=owner, origen="cuestion_contexto",
                             cuestion_id=issue)
        db.session.execute(CuestionContexto.__table__.update()
                           .values(estado="superada"))
        db.session.commit()
        self.assertEqual(qc().ISSUE_SUPERSEDED, self.refused(
            errors().ValidationError, qc().changes.create, CLI, CHANGE | {
                "responsable_id": owner, "origen": "cuestion_contexto", "cuestion_id": issue},
            today=TODAY))
        # A link made while the issue was current stays, and the change stays editable.
        edited = self.edit(linked.id, {"titulo": "ERP con trazabilidad", "cuestion_id": issue})
        self.assertEqual((issue, "ERP con trazabilidad"), (edited.cuestion_id, edited.titulo))

    def test_the_review_action_and_the_issue_are_read_for_share(self) -> None:
        owner = self.person()
        records = self.origins(owner)
        for column, origin in LINKS.items():
            with self.subTest(column=column), patch.object(qc(), "_lock",
                                                           wraps=qc()._lock) as lock:
                self.change(responsable_id=owner, origen=origin, **{column: records[column]})
                shared = [call.args[1] for call in lock.call_args_list
                          if call.kwargs.get("read") is True]
                expected = {"accion_revision_id": [AccionRevision],
                            "cuestion_id": [CuestionContexto]}.get(column, [])
                self.assertEqual(expected, shared)

    def test_changing_the_origin_while_its_link_stays_is_refused(self) -> None:
        owner = self.person()
        records = self.origins(owner)
        change = self.change(responsable_id=owner, origen="riesgo",
                             riesgo_id=records["riesgo_id"])
        expected = qc().CLEAR_LINK_FIRST.format(key="riesgo_id", origen="Riesgo")
        for data in ({"origen": "auditoria"}, {"origen": "otro"},
                     {"origen": "otro", "riesgo_id": records["riesgo_id"]}):
            with self.subTest(data=data):
                self.assertEqual(expected, self.refused(
                    errors().ValidationError, qc().changes.update, CLI, change.id, data,
                    today=TODAY))
        moved = self.edit(change.id, {"origen": "auditoria", "riesgo_id": None,
                                      "auditoria_id": records["auditoria_id"]})
        self.assertEqual((OrigenCambio.auditoria, None, records["auditoria_id"]),
                         (moved.origen, moved.riesgo_id, moved.auditoria_id))
        self.assertEqual(qc().LINK_NEEDS_ORIGIN.format(key="riesgo_id", origen="Riesgo"),
                         self.refused(errors().ValidationError, qc().changes.update, CLI,
                                      change.id, {"riesgo_id": records["riesgo_id"]},
                                      today=TODAY))


class ProcessesAndDocumentsTestCase(Helpers, ServiceBase):
    def test_the_lists_replace_the_links_and_travel_in_the_audit_rows(self) -> None:
        sales, buying, store = self.process("Ventas"), self.process("Compras"), self.process(
            "Almacen")
        manual = self.document("MC-01")
        change = self.change(procesos=[sales], documentos=[manual])
        edited = self.edit(change.id, {"procesos": [buying, store], "documentos": [manual]})
        self.assertEqual(sorted([buying, store]), edited.proceso_ids)
        self.assertEqual(["Almacen", "Compras"], [p.proceso for p in edited.procesos])
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(("update", ({"procesos": [sales]}, {"procesos": sorted([buying, store])})),
                         (row.action, self.diff(row)))
        cleared = self.edit(change.id, {"procesos": None, "documentos": []})
        self.assertEqual(([], []), (cleared.proceso_ids, cleared.documento_ids))
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(({"procesos": sorted([buying, store]), "documentos": [manual]},
                          {"procesos": [], "documentos": []}), self.diff(row))

    def test_an_unchanged_list_writes_nothing(self) -> None:
        sales, manual = self.process(), self.document()
        change = self.change(procesos=[sales], documentos=[manual])
        self.edit(change.id, {"procesos": [sales, sales], "documentos": [manual],
                              "titulo": CHANGE["titulo"]})
        self.assertEqual(["create"], [row.action for row in self.rows("cambios_sgc")])

    def test_unknown_and_malformed_ids_are_refused(self) -> None:
        sales, manual = self.process(), self.document()
        change = self.change()
        for key, value, expected in (
                ("procesos", [sales, 999, 998], "Procesos desconocidos en «procesos»: 998, 999."),
                ("procesos", [0], "Procesos desconocidos en «procesos»: 0."),
                ("procesos", "1", qc().PROCESS_IDS), ("procesos", [True], qc().PROCESS_IDS),
                ("procesos", [str(sales)], qc().PROCESS_IDS), ("procesos", sales, qc().PROCESS_IDS),
                ("documentos", [manual, 999], "Documentos desconocidos en «documentos»: 999."),
                ("documentos", {manual}, qc().DOCUMENT_IDS),
                ("documentos", [1.0], qc().DOCUMENT_IDS)):
            with self.subTest(key=key, value=value):
                for call, args in ((qc().changes.create, (CHANGE | {
                        "responsable_id": change.responsable_id, key: value},)),
                                   (qc().changes.update, (change.id, {key: value}))):
                    self.assertEqual(expected, self.refused(errors().ValidationError, call, CLI,
                                                            *args, today=TODAY))
        self.assertEqual(1, db.session.query(CambioSGC).count())


class EditingByStateTestCase(Helpers, ServiceBase):
    def test_a_proposed_change_takes_every_field(self) -> None:
        owner, other = self.person("Ana"), self.person("Eva")
        records = self.origins(owner)
        change = self.change(responsable_id=owner)
        data = PLAN | {"titulo": "ERP", "descripcion": "Otro", "origen": "riesgo",
                       "riesgo_id": records["riesgo_id"], "proposito": "Otro",
                       "comunicacion": "Correo", "responsable_id": other,
                       "fecha_propuesta": date(2026, 10, 2), "procesos": [self.process()],
                       "documentos": [self.document()]}
        edited = self.edit(change.id, data, actor(AUDITOR))
        self.assertEqual(data["titulo"], edited.titulo)
        self.assertEqual((OrigenCambio.riesgo, other, date(2026, 10, 2)),
                         (edited.origen, edited.responsable_id, edited.fecha_propuesta))
        self.assertEqual(["create", "update"], [r.action for r in self.rows("cambios_sgc")])

    def test_an_approved_change_keeps_its_plan(self) -> None:
        change = self.approved()
        for key, value in (("titulo", "Otro"), ("descripcion", "Otra"), ("origen", "riesgo"),
                           ("proposito", "Otro"), ("consecuencias", "Otras"),
                           ("integridad", "Otra"), ("recursos", "Otros"),
                           ("responsabilidades", "Otras"),
                           ("fecha_propuesta", date(2026, 10, 2))):
            with self.subTest(field=key):
                message = self.refused(errors().ValidationError, qc().changes.update, CLI,
                                       change.id, {key: value, "comunicacion": "Correo"},
                                       today=TODAY)
                self.assertEqual(qc().PLAN_FIXED.format(campos=f"«{key}»"), message)
        message = self.refused(errors().ValidationError, qc().changes.update, CLI, change.id,
                               {"titulo": "Otro", "recursos": "Otros"}, today=TODAY)
        self.assertEqual(qc().PLAN_FIXED.format(campos="«titulo», «recursos»"), message)
        records = self.origins(change.responsable_id)
        self.assertEqual(qc().PLAN_FIXED.format(campos="«origen», «riesgo_id»"), self.refused(
            errors().ValidationError, qc().changes.update, CLI, change.id,
            {"origen": "riesgo", "riesgo_id": records["riesgo_id"]}, today=TODAY))
        other, sales, manual = self.person("Eva"), self.process(), self.document()
        # The fixed fields posted unchanged with the editable ones, as a form does.
        edited = self.edit(change.id, {"titulo": change.titulo, "consecuencias":
                                       PLAN["consecuencias"], "responsable_id": other,
                                       "fecha_prevista": date(2027, 1, 31),
                                       "comunicacion": "Correo", "procesos": [sales],
                                       "documentos": [manual]}, actor(AUDITOR))
        self.assertEqual((other, date(2027, 1, 31), "Correo", [sales], [manual]),
                         (edited.responsable_id, edited.fecha_prevista, edited.comunicacion,
                          edited.proceso_ids, edited.documento_ids))

    def test_an_approved_change_keeps_a_planned_date(self) -> None:
        """Clearing it would take the change out of every overdue check (CP-2 review)."""
        change = self.approved()
        message = self.refused(errors().ValidationError, qc().changes.update, CLI, change.id,
                               {"fecha_prevista": None, "comunicacion": "Correo"}, today=TODAY)
        self.assertEqual(qc().PLANNED_DATE_KEPT, message)
        self.assertEqual(date(2027, 2, 1),
                         self.edit(change.id, {"fecha_prevista": date(2027, 2, 1)}).fecha_prevista)
        proposed = self.change(fecha_prevista=date(2026, 12, 1))
        self.assertIsNone(self.edit(proposed.id, {"fecha_prevista": None}).fecha_prevista)

    def test_implemented_reviewed_and_cancelled_changes_change_nothing(self) -> None:
        for make, state in ((self.implemented, EstadoCambio.implantado),
                            (self.reviewed, EstadoCambio.eficacia_revisada),
                            (self.cancelled, EstadoCambio.cancelado)):
            change = make()
            for data in ({"comunicacion": "Correo"}, {"titulo": "Otro"},
                         {"procesos": [self.process(f"P {state.name}")]},
                         {"fecha_prevista": date(2027, 3, 1)}):
                with self.subTest(state=state.name, data=data):
                    message = self.refused(errors().ValidationError, qc().changes.update, CLI,
                                           change.id, data, today=TODAY)
                    self.assertEqual(qc().READ_ONLY[state], message)
            # Re-posting what it holds still writes nothing.
            rows = len(self.rows("cambios_sgc"))
            self.edit(change.id, {"titulo": change.titulo, "procesos": change.proceso_ids})
            self.assertEqual(rows, len(self.rows("cambios_sgc")))


class DeleteTestCase(Helpers, ServiceBase):
    def test_a_proposed_change_goes_with_its_tasks_and_links_audited(self) -> None:
        sales, manual = self.process(), self.document()
        change = self.change(procesos=[sales], documentos=[manual])
        first, second = self.task(change.id), self.task(change.id, descripcion="Formar")
        kept = self.change(titulo="Otro")
        self.task(kept.id)
        qc().changes.delete(db.session, actor(ADMIN), change.id)
        db.session.commit()
        self.assertEqual(([kept.id], 1, 0, 0), (
            self.ids(db.session.query(CambioSGC).all()), db.session.query(TareaCambio).count(),
            db.session.execute(text("SELECT count(*) FROM cambio_procesos")).scalar_one(),
            db.session.execute(text("SELECT count(*) FROM cambio_documentos")).scalar_one()))
        deleted = [row for row in self.rows("tareas_cambio") if row.action == "delete"]
        self.assertEqual(sorted([first.id, second.id]), sorted(r.entity_id for r in deleted))
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(("delete", change.id, [sales], [manual], "Propuesto"),
                         (row.action, row.entity_id, row.before["procesos"],
                          row.before["documentos"], row.before["estado"]))
        self.assertLess(max(r.id for r in deleted), row.id)

    def test_only_a_proposed_change_is_deleted(self) -> None:
        for make in (self.approved, self.implemented, self.reviewed, self.cancelled):
            change = make()
            with self.subTest(state=change.estado.name):
                self.assertEqual(qc().ONLY_PROPOSED_DELETED, self.refused(
                    errors().ValidationError, qc().changes.delete, actor(ADMIN), change.id))
        self.refused(errors().NotFound, qc().changes.delete, actor(ADMIN), 999)

    def test_only_web_administrators_delete(self) -> None:
        change = self.change()
        for who in (actor(AUDITOR), actor(OPERATIVO), mcp_actor(ADMIN)):
            with self.subTest(role=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, qc().changes.delete, who, change.id)
        self.assertEqual(1, db.session.query(CambioSGC).count())


class GuardTestCase(Helpers, ServiceBase):
    GUARDS = {"may_approve": {ADMIN}, "may_implement": {ADMIN, AUDITOR},
              "may_review_effectiveness": {ADMIN, AUDITOR}, "may_cancel": {ADMIN}}

    def test_each_guard_checks_the_role_the_scopes_and_the_channel(self) -> None:
        for name, roles in self.GUARDS.items():
            guard = getattr(qc(), name)
            for role in (ADMIN, AUDITOR, OPERATIVO):
                with self.subTest(guard=name, role=role.name):
                    self.assertEqual(role in roles, guard(actor(role)))
                    self.assertFalse(guard(mcp_actor(role)))
                    self.assertFalse(guard(actor(role, scopes={"read"})))
                    self.assertEqual(role in roles, guard(actor(role, scopes={"read", "write"})))

    def test_with_a_change_each_guard_also_checks_its_state(self) -> None:
        changes = {"propuesto": self.change(), "aprobado": self.approved(),
                   "implantado": self.implemented(), "eficacia_revisada": self.reviewed(),
                   "cancelado": self.cancelled()}
        allowed = {"may_approve": {"propuesto"}, "may_implement": {"aprobado"},
                   "may_review_effectiveness": {"implantado"},
                   "may_cancel": {"propuesto", "aprobado"}}
        for name, states in allowed.items():
            for state, change in changes.items():
                with self.subTest(guard=name, state=state):
                    self.assertEqual(state in states, getattr(qc(), name)(actor(ADMIN), change))
                    self.assertFalse(getattr(qc(), name)(mcp_actor(ADMIN), change))

    def test_a_failing_guard_refuses_with_a_bare_permission_denied(self) -> None:
        proposed, approved = self.change(**PLAN), self.approved()
        implemented = self.implemented()
        calls = {
            "approve": (qc().approve, proposed.id, {}),
            "implement": (qc().implement, approved.id, {"fecha": TODAY}),
            "review_effectiveness": (qc().review_effectiveness, implemented.id,
                                     {"eficacia": "eficaz", "fecha": TODAY}),
            "cancel": (qc().cancel, proposed.id, {"motivo": "No"}),
        }
        denied = {"approve": (AUDITOR, OPERATIVO), "implement": (OPERATIVO,),
                  "review_effectiveness": (OPERATIVO,), "cancel": (AUDITOR, OPERATIVO)}
        for name, (call, change_id, kwargs) in calls.items():
            for who in (*(self.web(role, f"{name} {role.name}")[0] for role in denied[name]),
                        mcp_actor(ADMIN), mcp_actor(AUDITOR),
                        Actor(user_id=None, label="x", role=ADMIN, channel="web",
                              scopes=frozenset({"read"}))):
                with self.subTest(transition=name, role=who.role.name, channel=who.channel):
                    message = self.refused(errors().PermissionDenied, call, who, change_id,
                                           today=TODAY, **kwargs)
                    self.assertEqual(errors().PermissionDenied.default_message, message)
        self.assertEqual(
            [EstadoCambio.propuesto, EstadoCambio.aprobado, EstadoCambio.implantado],
            [db.session.get(CambioSGC, c.id).estado for c in (proposed, approved, implemented)])


class ApproveTestCase(Helpers, ServiceBase):
    def test_approval_lists_everything_it_still_needs(self) -> None:
        change = self.change()
        message = self.refused(errors().ValidationError, qc().approve, self.admin(), change.id,
                               today=TODAY)
        self.assertEqual(
            "Para aprobar el cambio falta: las consecuencias potenciales («consecuencias»), "
            "la integridad del SGC («integridad»), los recursos («recursos»), las "
            "responsabilidades y autoridades («responsabilidades»), la fecha prevista de "
            "implantación («fecha_prevista») y al menos una tarea que no esté cancelada.",
            message)
        self.edit(change.id, {"consecuencias": "Formación", "recursos": "Licencias"})
        self.task(change.id, estado="cancelada")
        message = self.refused(errors().ValidationError, qc().approve, self.admin(), change.id,
                               today=TODAY)
        self.assertNotIn("«consecuencias»", message)
        self.assertIn("«integridad»", message)
        self.assertIn(qc().TASK_NEEDED, message)  # a cancelled task does not count
        # Blank text stored by an older write counts as missing too.
        db.session.execute(CambioSGC.__table__.update().values(
            integridad="  ", responsabilidades="Dirección", fecha_prevista=date(2026, 12, 1)))
        db.session.commit()
        self.assertEqual(
            "Para aprobar el cambio falta: la integridad del SGC («integridad») y al menos una "
            "tarea que no esté cancelada.",
            self.refused(errors().ValidationError, qc().approve, self.admin(), change.id,
                         today=TODAY))
        self.assertEqual(EstadoCambio.propuesto, db.session.get(CambioSGC, change.id).estado)

    def test_approval_records_the_approver_and_the_day(self) -> None:
        admin, person = self.web(ADMIN, "Gerencia")
        change = self.change(**PLAN)
        self.task(change.id)
        approved = qc().approve(db.session, admin, change.id, today=TODAY)
        db.session.commit()
        self.assertEqual((EstadoCambio.aprobado, person, TODAY, admin.user_id),
                         (approved.estado, approved.aprobado_por_id, approved.fecha_aprobacion,
                          approved.updated_by_id))
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(
            ("update", {"estado": "Propuesto", "aprobado_por_id": None, "fecha_aprobacion": None},
             "Aprobado", person, TODAY.isoformat()),
            (row.action, {key: row.before[key] for key in ("estado", "aprobado_por_id",
                                                           "fecha_aprobacion")},
             row.after["estado"], row.after["aprobado_por_id"], row.after["fecha_aprobacion"]))

    def test_only_a_proposed_change_is_approved(self) -> None:
        for make in (self.approved, self.implemented, self.cancelled):
            change = make()
            with self.subTest(state=change.estado.name):
                self.assertEqual(qc().NOT_PROPOSED, self.refused(
                    errors().ValidationError, qc().approve, self.admin(), change.id,
                    today=TODAY))
        self.refused(errors().NotFound, qc().approve, self.admin(), 999, today=TODAY)
        with self.assertRaises(ValueError):
            qc().approve(db.session, self.admin(), self.change().id, today=None)

    def test_the_approver_needs_a_linked_person(self) -> None:
        change = self.change(**PLAN)
        self.task(change.id)
        self.assertEqual(qc().APPROVER_REQUIRED, self.refused(
            errors().ValidationError, qc().approve, actor(ADMIN, user_id=99), change.id,
            today=TODAY))


class ImplementTestCase(Helpers, ServiceBase):
    def test_every_task_must_be_closed_and_one_completed(self) -> None:
        change = self.approved()
        (first,) = change.tareas
        second = self.task(change.id, estado="en_curso")
        self.assertEqual(qc().TASKS_OPEN, self.refused(
            errors().ValidationError, qc().implement, self.admin(), change.id, fecha=TODAY,
            today=TODAY))
        for task in (first, second):
            qc().tasks.update(db.session, CLI, task.id, {"estado": "cancelada"}, today=TODAY)
        db.session.commit()
        self.assertEqual(qc().NO_COMPLETED_TASK, self.refused(
            errors().ValidationError, qc().implement, self.admin(), change.id, fecha=TODAY,
            today=TODAY))
        self.complete(second.id)
        auditor = self.web(AUDITOR, "Auditora")[0]
        done = qc().implement(db.session, auditor, change.id, fecha=TODAY, today=TODAY)
        db.session.commit()
        self.assertEqual((EstadoCambio.implantado, TODAY), (done.estado, done.fecha_implantacion))
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(({"estado": "Aprobado", "fecha_implantacion": None},
                          {"estado": "Implantado", "fecha_implantacion": TODAY.isoformat()}),
                         self.diff(row))

    def test_the_implementation_date_is_between_the_approval_and_today(self) -> None:
        change = self.approved(today=date(2026, 10, 10))
        for task in change.tareas:
            self.complete(task.id)
        for fecha, expected in ((TOMORROW, qc().IMPLEMENTED_IN_FUTURE),
                                (date(2026, 10, 9), qc().IMPLEMENTED_BEFORE_APPROVAL),
                                ("2026-10-15", "fecha_implantacion"), (None, "fecha_implantacion")):
            with self.subTest(fecha=fecha):
                message = self.refused(errors().ValidationError, qc().implement, self.admin(),
                                       change.id, fecha=fecha, today=TODAY)
                self.assertIn(expected, message)
        done = qc().implement(db.session, self.admin(), change.id, fecha=date(2026, 10, 10),
                              today=TODAY)
        self.assertEqual(date(2026, 10, 10), done.fecha_implantacion)

    def test_only_an_approved_change_is_implemented(self) -> None:
        for make in (self.change, self.implemented, self.cancelled):
            change = make()
            with self.subTest(state=change.estado.name):
                self.assertEqual(qc().NOT_APPROVED, self.refused(
                    errors().ValidationError, qc().implement, self.admin(), change.id,
                    fecha=TODAY, today=TODAY))


class ReviewTestCase(Helpers, ServiceBase):
    def test_the_review_records_its_result_date_and_reviewer(self) -> None:
        change = self.implemented()
        reviewer, person = self.web(AUDITOR, "Auditora")
        done = qc().review_effectiveness(db.session, reviewer, change.id, eficacia="no_eficaz",
                                         fecha=TODAY, comentario="  Siguen los errores ",
                                         today=TODAY)
        db.session.commit()
        self.assertEqual((EstadoCambio.eficacia_revisada, EficaciaCambio.no_eficaz, TODAY,
                          person, "Siguen los errores"),
                         (done.estado, done.eficacia, done.fecha_eficacia,
                          done.revisor_eficacia_id, done.comentario_eficacia))
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(("Eficacia revisada", "No eficaz", person),
                         (row.after["estado"], row.after["eficacia"],
                          row.after["revisor_eficacia_id"]))
        other = self.implemented(titulo="Otro")
        effective = qc().review_effectiveness(db.session, self.admin(), other.id,
                                              eficacia=EficaciaCambio.eficaz, fecha=TODAY,
                                              comentario=" ", today=TODAY)
        self.assertEqual((EficaciaCambio.eficaz, None),
                         (effective.eficacia, effective.comentario_eficacia))

    def test_the_owner_never_reviews_the_change(self) -> None:
        reviewer, person = self.web(AUDITOR, "Auditora")
        change = self.implemented(responsable_id=person)
        self.assertEqual(qc().REVIEWER_IS_OWNER, self.refused(
            errors().ValidationError, qc().review_effectiveness, reviewer, change.id,
            eficacia="eficaz", fecha=TODAY, today=TODAY))
        self.assertEqual(qc().REVIEWER_REQUIRED, self.refused(
            errors().ValidationError, qc().review_effectiveness, actor(AUDITOR, user_id=99),
            change.id, eficacia="eficaz", fecha=TODAY, today=TODAY))

    def test_an_ineffective_change_needs_a_comment_and_the_result_is_a_choice(self) -> None:
        change = self.implemented()
        for comentario in (None, "", "   "):
            with self.subTest(comentario=comentario):
                self.assertEqual(qc().COMMENT_REQUIRED, self.refused(
                    errors().ValidationError, qc().review_effectiveness, self.admin(), change.id,
                    eficacia="no_eficaz", fecha=TODAY, comentario=comentario, today=TODAY))
        for eficacia in ("Eficaz", "parcial", None, 1):
            with self.subTest(eficacia=eficacia):
                self.assertIn("eficacia", self.refused(
                    errors().ValidationError, qc().review_effectiveness, self.admin(), change.id,
                    eficacia=eficacia, fecha=TODAY, today=TODAY))
        self.assertIn("comentario_eficacia", self.refused(
            errors().ValidationError, qc().review_effectiveness, self.admin(), change.id,
            eficacia="eficaz", fecha=TODAY, comentario=5, today=TODAY))

    def test_the_review_date_is_between_the_implementation_and_today(self) -> None:
        change = self.implemented()
        for fecha, expected in ((TOMORROW, qc().REVIEWED_IN_FUTURE),
                                (YESTERDAY, qc().REVIEWED_BEFORE_IMPLEMENTATION),
                                ("2026-10-21", "fecha_eficacia")):
            with self.subTest(fecha=fecha):
                self.assertIn(expected, self.refused(
                    errors().ValidationError, qc().review_effectiveness, self.admin(), change.id,
                    eficacia="eficaz", fecha=fecha, today=TODAY))

    def test_only_an_implemented_change_is_reviewed(self) -> None:
        for make in (self.change, self.approved, self.reviewed, self.cancelled):
            change = make()
            with self.subTest(state=change.estado.name):
                self.assertEqual(qc().NOT_IMPLEMENTED, self.refused(
                    errors().ValidationError, qc().review_effectiveness, self.admin(), change.id,
                    eficacia="eficaz", fecha=TODAY, today=TODAY))


class CancelTestCase(Helpers, ServiceBase):
    def test_a_proposed_or_approved_change_is_cancelled_and_its_tasks_stay(self) -> None:
        proposed, approved = self.change(), self.approved()
        pending = self.task(proposed.id)
        for change in (proposed, approved):
            with self.subTest(state=change.estado.name):
                was_approved = change.fecha_aprobacion
                done = qc().cancel(db.session, self.admin(), change.id,
                                   motivo="  Lo cubre otro proyecto ", today=TODAY)
                db.session.commit()
                self.assertEqual((EstadoCambio.cancelado, "Lo cubre otro proyecto", TODAY,
                                  was_approved),
                                 (done.estado, done.motivo_cancelacion, done.fecha_cancelacion,
                                  done.fecha_aprobacion))
        self.assertEqual(EstadoTareaCambio.pendiente,
                         db.session.get(TareaCambio, pending.id).estado)
        row = self.rows("cambios_sgc")[-1]
        self.assertEqual(("Aprobado", "Cancelado"), (row.before["estado"], row.after["estado"]))

    def test_a_reason_is_required_and_closed_changes_stay(self) -> None:
        change = self.change()
        for motivo in (None, "", "  "):
            with self.subTest(motivo=motivo):
                self.assertEqual(qc().REASON_REQUIRED, self.refused(
                    errors().ValidationError, qc().cancel, self.admin(), change.id,
                    motivo=motivo, today=TODAY))
        for make in (self.implemented, self.reviewed, self.cancelled):
            closed = make()
            with self.subTest(state=closed.estado.name):
                self.assertEqual(qc().NOT_CANCELLABLE, self.refused(
                    errors().ValidationError, qc().cancel, self.admin(), closed.id,
                    motivo="Otro", today=TODAY))


class FilterTestCase(Helpers, ServiceBase):
    def test_filters_combine_and_take_members_or_names(self) -> None:
        ana, eva = self.person("Ana"), self.person("Eva")
        sales, manual = self.process(), self.document()
        records = self.origins(ana)
        late = self.approved(responsable_id=ana, origen="riesgo",
                             riesgo_id=records["riesgo_id"], fecha_prevista=YESTERDAY,
                             procesos=[sales])
        on_time = self.approved(titulo="Portal de clientes", descripcion="Pedidos en línea",
                                responsable_id=eva, fecha_prevista=TODAY, documentos=[manual])
        proposed = self.change(titulo="Firma digital", descripcion="Firmar los ERP de compras",
                               responsable_id=eva, fecha_prevista=date(2026, 10, 2))
        who = actor(OPERATIVO)

        def found(**filters):
            return self.ids(qc().changes.list_(db.session, who, today=TODAY, **filters))

        self.assertEqual([proposed.id, on_time.id, late.id], found())
        self.assertEqual([on_time.id, late.id], found(estado="aprobado"))
        self.assertEqual([proposed.id], found(estado=EstadoCambio.propuesto))
        self.assertEqual([late.id], found(origen="riesgo"))
        self.assertEqual([proposed.id, on_time.id], found(origen=OrigenCambio.otro))
        self.assertEqual([proposed.id, on_time.id], found(responsable_id=eva))
        self.assertEqual([late.id], found(proceso_id=sales))
        self.assertEqual([on_time.id], found(documento_id=manual))
        self.assertEqual([late.id], found(fuera_de_plazo=True))
        self.assertEqual([proposed.id, on_time.id], found(fuera_de_plazo=False))
        self.assertEqual([proposed.id, late.id], found(texto="erp"))
        self.assertEqual([proposed.id], found(texto="  compras "))
        self.assertEqual([proposed.id, on_time.id, late.id], found(texto=" "))
        self.assertEqual([], found(estado="aprobado", responsable_id=ana, fuera_de_plazo=False))
        for key in ("responsable_id", "proceso_id", "documento_id"):
            self.assertEqual([], found(**{key: 999}))
            self.assertEqual([], found(**{key: "1"}))
        page, total = qc().changes.list_page(db.session, who, page=1, per_page=2, today=TODAY)
        self.assertEqual(([proposed.id, on_time.id], 3), (self.ids(page), total))

    def test_the_changes_with_an_overdue_task_while_still_open(self) -> None:
        """``tareas_vencidas``: what the dashboard's overdue-task alert links (CP-3)."""
        late = self.change(titulo="Con una tarea vencida")
        self.task(late.id, fecha_limite=YESTERDAY)
        self.task(late.id, fecha_limite=TOMORROW)
        on_time = self.change(titulo="Al día")
        self.task(on_time.id, fecha_limite=TOMORROW)
        done = self.change(titulo="Tarea hecha")
        self.complete(self.task(done.id, fecha_limite=YESTERDAY).id)
        stopped = self.change(titulo="Cancelado")
        self.task(stopped.id, fecha_limite=YESTERDAY)
        qc().cancel(db.session, self.admin(), stopped.id, motivo="Ya no hace falta", today=TODAY)
        db.session.commit()
        bare = self.change(titulo="Sin tareas")

        def found(**filters):
            return self.ids(qc().changes.list_(db.session, actor(OPERATIVO), today=TODAY,
                                               **filters))

        self.assertEqual([late.id], found(tareas_vencidas=True))
        self.assertEqual([bare.id, stopped.id, done.id, on_time.id], found(tareas_vencidas=False))
        self.assertEqual([], found(tareas_vencidas=True, estado="aprobado"))
        self.refused(errors().ValidationError, qc().changes.list_, actor(), today=TODAY,
                     tareas_vencidas="1")
        with self.assertRaises(ValueError):
            qc().changes.list_(db.session, actor(), tareas_vencidas=True, today=None)

    def test_bad_filter_values_are_refused(self) -> None:
        for filters in ({"estado": "Aprobado"}, {"origen": "cliente"},
                        {"fuera_de_plazo": "si"}, {"fuera_de_plazo": 1}, {"texto": 5}):
            with self.subTest(filters=filters):
                self.refused(errors().ValidationError, qc().changes.list_, actor(), today=TODAY,
                             **filters)
        with self.assertRaises(ValueError):
            qc().changes.list_(db.session, actor(), fuera_de_plazo=True, today=None)


class ReadersTestCase(Helpers, ServiceBase):
    def test_attention_flags_what_the_dashboard_shows(self) -> None:
        older = self.change(titulo="Más antiguo", fecha_propuesta=date(2026, 9, 1))
        proposed = self.change()
        late = self.approved(fecha_prevista=YESTERDAY)
        self.approved(titulo="A tiempo", fecha_prevista=TODAY)
        overdue = self.task(proposed.id, fecha_limite=YESTERDAY)
        self.task(proposed.id, fecha_limite=TODAY)
        self.task(older.id, fecha_limite=date(2026, 9, 30), estado="completada",
                  fecha_completada=date(2026, 9, 30))
        in_progress = self.task(late.id, fecha_limite=date(2026, 10, 1), estado="en_curso")
        gone = self.change(titulo="Cancelado")
        self.task(gone.id, fecha_limite=YESTERDAY)
        qc().cancel(db.session, self.admin(), gone.id, motivo="No", today=TODAY)
        db.session.commit()
        implemented = self.implemented(titulo="Implantado")
        self.reviewed(titulo="Revisado")
        flagged = qc().attention(db.session, actor(OPERATIVO), today=TODAY)
        self.assertEqual(
            {"pendientes_aprobacion": [older.id, proposed.id], "fuera_de_plazo": [late.id],
             "tareas_vencidas": [in_progress.id, overdue.id],
             "eficacia_pendiente": [implemented.id]},
            {key: self.ids(records) for key, records in flagged.items()})
        with self.assertRaises(ValueError):
            qc().attention(db.session, actor(), today=None)
        self.refused(errors().PermissionDenied, qc().attention, actor(ADMIN, scopes={"write"}),
                     today=TODAY)

    def test_the_overdue_predicates_match_the_filters(self) -> None:
        late = self.approved(fecha_prevista=YESTERDAY)
        on_time = self.approved(fecha_prevista=TODAY)
        proposed = self.change(fecha_prevista=YESTERDAY)
        self.assertEqual((True, False, False), tuple(
            qc().fuera_de_plazo(change, today=TODAY) for change in (late, on_time, proposed)))
        task = self.task(proposed.id, fecha_limite=YESTERDAY)
        self.assertTrue(qc().is_overdue(task, proposed, today=TODAY))
        self.assertFalse(qc().is_overdue(task, proposed, today=YESTERDAY))
        qc().cancel(db.session, self.admin(), proposed.id, motivo="No", today=TODAY)
        self.assertFalse(qc().is_overdue(task, proposed, today=TODAY))

    def test_in_period_counts_each_stage_by_its_date(self) -> None:
        desde, hasta = date(2026, 10, 1), date(2026, 10, 31)
        before = self.change(titulo="Antes", fecha_propuesta=date(2026, 9, 1))
        proposed = self.change(titulo="Propuesto")
        approved = self.approved(titulo="Aprobado", fecha_prevista=date(2026, 10, 15))
        not_late = self.approved(titulo="En plazo", fecha_prevista=date(2026, 11, 30))
        reviewed = self.implemented(titulo="Revisado")
        reviewer = self.web(AUDITOR, "Auditora")[0]
        qc().review_effectiveness(db.session, reviewer, reviewed.id, eficacia="no_eficaz",
                                  fecha=TODAY, comentario="Sin mejora", today=TODAY)
        implemented = self.implemented(titulo="Implantado")
        cancelled = self.cancelled(titulo="Cancelado")
        db.session.commit()
        found = qc().in_period(db.session, desde, hasta)
        # ``before`` was proposed earlier; every other change was proposed on 1 October.
        self.assertEqual((6, 4, 2, 1, 0, 1, 1), (
            found.propuestos, found.aprobados, found.implantados, found.revisados,
            found.eficaces, found.no_eficaces, found.cancelados))
        self.assertEqual([approved.id], self.ids(found.fuera_de_plazo))
        self.assertEqual(sorted([reviewed.id, implemented.id]),
                         sorted(self.ids(found.implantados_o_revisados)))
        # At an earlier cut-off nothing was approved yet, so nothing was late.
        early = qc().in_period(db.session, date(2026, 9, 1), date(2026, 10, 20))
        self.assertEqual((7, 0, [], []), (early.propuestos, early.aprobados,
                                          early.fuera_de_plazo, early.implantados_o_revisados))
        # By the end of the year the change planned for November is late too.
        later = qc().in_period(db.session, date(2026, 11, 1), date(2026, 12, 31))
        self.assertEqual((0, [approved.id, not_late.id]),
                         (later.aprobados, self.ids(later.fuera_de_plazo)))
        self.assertNotIn(not_late.id, self.ids(found.fuera_de_plazo))
        self.assertNotIn(before.id, self.ids(found.implantados_o_revisados))
        self.assertNotIn(proposed.id, self.ids(found.fuera_de_plazo))
        self.assertNotIn(cancelled.id, self.ids(found.fuera_de_plazo))
        for desde_, hasta_ in ((hasta, desde), ("2026-10-01", hasta)):
            with self.subTest(desde=desde_, hasta=hasta_):
                with self.assertRaises(ValueError):
                    qc().in_period(db.session, desde_, hasta_)

    def test_a_change_implemented_or_cancelled_after_the_cut_off_was_late_then(self) -> None:
        late_then = self.implemented(approved_on=date(2026, 10, 10), titulo="Tarde",
                                     fecha_prevista=date(2026, 10, 5))
        cancelled_later = self.approved(today=date(2026, 10, 10), titulo="Cancelado",
                                        fecha_prevista=date(2026, 10, 5))
        qc().cancel(db.session, self.admin(), cancelled_later.id, motivo="No", today=TODAY)
        db.session.commit()
        cut_off = qc().in_period(db.session, date(2026, 10, 1), date(2026, 10, 15))
        self.assertEqual([late_then.id, cancelled_later.id], self.ids(cut_off.fuera_de_plazo))
        self.assertEqual([], cut_off.implantados_o_revisados)
        now = qc().in_period(db.session, date(2026, 10, 1), TODAY)
        self.assertEqual([], now.fuera_de_plazo)  # implemented and cancelled by then
        self.assertEqual([late_then.id], self.ids(now.implantados_o_revisados))

    def test_for_origin_needs_an_origin_with_a_link(self) -> None:
        for origen in ("otro", OrigenCambio.otro, "cliente", None):
            with self.subTest(origen=origen):
                with self.assertRaises(ValueError):
                    qc().for_origin(db.session, actor(), origen, 1)
        self.refused(errors().PermissionDenied, qc().for_origin, actor(scopes={"write"}),
                     "riesgo", 1)
        self.assertEqual([], qc().for_origin(db.session, actor(), "riesgo", "1"))


class WriteConflictTestCase(Helpers, ServiceBase):
    def test_an_integrity_failure_while_auditing_is_a_write_conflict(self) -> None:
        """An update and a transition translate it as a creation does (CP-2 review)."""
        from sqlalchemy.exc import IntegrityError

        admin = self.admin()
        change = self.change(**PLAN)
        self.task(change.id)
        race = IntegrityError("INSERT INTO audit_log", {}, Exception("unique"))
        writes = {
            "create": lambda: qc().changes.create(db.session, CLI, CHANGE | {
                "responsable_id": change.responsable_id}, today=TODAY),
            "update": lambda: qc().changes.update(db.session, CLI, change.id,
                                                  {"comunicacion": "Correo"}, today=TODAY),
            "approve": lambda: qc().approve(db.session, admin, change.id, today=TODAY),
            "cancel": lambda: qc().cancel(db.session, admin, change.id, motivo="Sin fondos",
                                          today=TODAY),
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                with patch.object(qc().audit, "record", side_effect=race):
                    with self.assertRaises(errors().Conflict) as caught:
                        write()
                db.session.rollback()
                self.assertEqual(qc().WRITE_CONFLICT, caught.exception.message)
                self.assertIs(race, caught.exception.__cause__)
        self.assertEqual(EstadoCambio.propuesto,
                         qc().changes.get(db.session, CLI, change.id).estado)


class PolicyTestCase(Helpers, ServiceBase):
    def test_every_role_reads_admins_and_auditors_write_admins_delete(self) -> None:
        change = self.change()
        for role in (ADMIN, AUDITOR, OPERATIVO):
            with self.subTest(read=role.name):
                self.assertEqual([change.id], self.ids(qc().changes.list_(
                    db.session, actor(role), today=TODAY)))
        edit = {"comunicacion": "Correo"}
        denied = ((actor(OPERATIVO), qc().changes.create, (CHANGE | {
                      "responsable_id": change.responsable_id},), {"today": TODAY}),
                  (actor(OPERATIVO), qc().changes.update, (change.id, edit), {"today": TODAY}),
                  (actor(AUDITOR, scopes={"read"}), qc().changes.update, (change.id, edit),
                   {"today": TODAY}),
                  (actor(AUDITOR, scopes={"write"}), qc().changes.get, (change.id,), {}))
        for who, call, args, kwargs in denied:
            with self.subTest(denied=call.__name__, role=who.role.name):
                self.refused(errors().PermissionDenied, call, who, *args, **kwargs)
        self.edit(change.id, edit, actor(AUDITOR))
        self.edit(change.id, {"comunicacion": "Por MCP"}, mcp_actor(AUDITOR))
        self.assertEqual(["create", "update", "update"],
                         [r.action for r in self.rows("cambios_sgc")])
        self.assertEqual({"web", "mcp", "cli"}, {r.channel for r in self.rows("cambios_sgc")})


class DatabaseCases:
    """The writes and reads against each engine's schema, foreign keys enforced."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_a_change_runs_its_whole_life_against_the_schema(self) -> None:
        self.enforce_foreign_keys()
        admin, approver = self.web(ADMIN, "Gerencia")
        reviewer, inspector = self.web(AUDITOR, "Inspección")
        owner = self.person("Ana")
        records = self.origins(owner)
        sales, manual = self.process(), self.document()
        change = self.change(admin, **PLAN, responsable_id=owner, origen="riesgo",
                             riesgo_id=records["riesgo_id"], procesos=[sales],
                             documentos=[manual])
        task = self.task(change.id, admin, responsable_id=owner)
        for step in (
                lambda: qc().approve(db.session, admin, change.id, today=TODAY),
                lambda: qc().changes.update(db.session, admin, change.id,
                                            {"procesos": [], "responsable_id": approver},
                                            today=TODAY),
                lambda: qc().tasks.update(db.session, admin, task.id, {
                    "estado": "completada", "fecha_completada": TODAY}, today=TODAY),
                lambda: qc().implement(db.session, reviewer, change.id, fecha=TODAY,
                                       today=TODAY),
                lambda: qc().review_effectiveness(db.session, reviewer, change.id,
                                                  eficacia="eficaz", fecha=TODAY,
                                                  today=TODAY)):
            step()
            db.session.commit()
        db.session.expire_all()
        found = db.session.get(CambioSGC, change.id)
        self.assertEqual((EstadoCambio.eficacia_revisada, approver, approver, inspector, [],
                          [manual]),
                         (found.estado, found.aprobado_por_id, found.responsable_id,
                          found.revisor_eficacia_id, found.proceso_ids, found.documento_ids))
        cancelled = self.approved(responsable_id=owner)
        qc().cancel(db.session, admin, cancelled.id, motivo="No", today=TODAY)
        db.session.commit()
        self.assertEqual(EstadoCambio.cancelado, db.session.get(CambioSGC, cancelled.id).estado)

    def test_a_proposed_change_is_deleted_before_its_foreign_keys_refuse(self) -> None:
        self.enforce_foreign_keys()
        change = self.change(procesos=[self.process()], documentos=[self.document()])
        self.task(change.id)
        self.task(change.id, descripcion="Formar al equipo")
        qc().changes.delete(db.session, CLI, change.id)
        db.session.commit()
        self.assertEqual([(0, 0, 0, 0)], [tuple(row) for row in db.session.execute(text(
            "SELECT (SELECT count(*) FROM cambios_sgc), (SELECT count(*) FROM tareas_cambio), "
            "(SELECT count(*) FROM cambio_procesos), (SELECT count(*) FROM cambio_documentos)"
        ))])

    def test_the_filters_and_readers_run_in_the_database(self) -> None:
        self.enforce_foreign_keys()
        sales = self.process()
        late = self.approved(fecha_prevista=YESTERDAY, procesos=[sales])
        overdue = self.task(late.id, fecha_limite=YESTERDAY)
        who = actor(OPERATIVO, user_id=None)
        self.assertEqual([late.id], self.ids(qc().changes.list_(
            db.session, who, today=TODAY, fuera_de_plazo=True, proceso_id=sales, texto="ERP",
            estado="aprobado", origen="otro")))
        self.assertEqual([overdue.id], self.ids(qc().tasks.list_(
            db.session, who, today=TODAY, vencida=True, cambio_id=late.id)))
        flagged = qc().attention(db.session, who, today=TODAY)
        self.assertEqual(([late.id], [overdue.id]), (self.ids(flagged["fuera_de_plazo"]),
                                                     self.ids(flagged["tareas_vencidas"])))
        found = qc().in_period(db.session, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual((1, 1, [late.id]), (found.propuestos, found.aprobados,
                                             self.ids(found.fuera_de_plazo)))


class SqliteDatabaseTestCase(DatabaseCases, Helpers, ServiceBase):
    def tearDown(self) -> None:
        # Dropping a table deletes its rows, which the restricted links refuse.
        db.session.rollback()
        db.session.execute(text("PRAGMA foreign_keys=OFF"))
        db.session.commit()
        super().tearDown()

    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDatabaseTestCase(DatabaseCases, Helpers, unittest.TestCase):
    """The same cases against PostgreSQL, whose foreign keys are always enforced."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
