"""Management reviews, their inputs and their actions (MR-1: decisions MR1 to MR6, ISO 9001 9.3).

A review has a meeting date, a period ending on or before it, attendees, a
comment per input and conclusions. It moves ``borrador`` -> ``celebrada``
(``hold``, which freezes the period's inputs) -> ``cerrada`` (``close``), both
for web administrators only. Its outputs are actions with an owner, a deadline
and follow-up; once the review is closed only their state, completion date and
result change.

The SQLite cases run with the audit flush guard (``ServiceBase``). The foreign
keys and CHECK constraints are exercised on SQLite with ``PRAGMA
foreign_keys=ON`` and on PostgreSQL when ``TEST_POSTGRES_URI`` is set.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    AccionCorrectiva, AlcanceSGC, Auditoria, AuditoriaIndicador, AuditLog, Capacitacion,
    CuestionContexto, EstadoAlcance, EstadoAuditoriaEnum, EstadoNoConformidad, EstadoObjetivo,
    EstadoProveedor, EvaluacionProveedor, FrecuenciaMedicion, GravedadNoConformidad,
    HallazgoAuditoria,
    MedicionObjetivo, Mejora, NoConformidad, ObjetivoCalidad, Oportunidad, OrigenNoConformidad,
    ParteInteresada, Person, Proveedor, ResultadoVerificacion, Riesgo, RoleEnum,
    SatisfaccionCliente, SentidoObjetivo, TipoHallazgo,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
# No user id: attribution and audit rows stay valid when foreign keys are enforced.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
TODAY = date(2026, 10, 12)
REVIEW = {"fecha": date(2026, 7, 10), "periodo_desde": date(2026, 4, 1),
          "periodo_hasta": date(2026, 6, 30)}
ACTION = {"tipo": "mejora", "descripcion": "Automatizar el seguimiento de pedidos",
          "fecha_limite": date(2026, 12, 31)}
UTC = timezone.utc


def mr():
    from app.services import management_reviews

    return management_reviews


def models():
    import app.models as module

    return module


def at(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class Helpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True,
               user_id: int | None = None) -> int:
        """Core insert: these tests must not depend on the people write API."""
        result = db.session.execute(Person.__table__.insert().values(
            nombre=nombre, activo=activo, user_id=user_id))
        db.session.commit()
        return result.inserted_primary_key[0]

    def insert(self, model, **values) -> int:
        """Core insert of a source record (no audit row, explicit timestamps)."""
        result = db.session.execute(model.__table__.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def review(self, who: Actor = CLI, **overrides):
        found = mr().reviews.create(db.session, who, REVIEW | overrides)
        db.session.commit()
        return found

    def action(self, review_id: int, who: Actor = CLI, **overrides):
        if "responsable_id" not in overrides:
            overrides["responsable_id"] = self.person("Eva Gil")
        found = mr().actions.create(db.session, who, ACTION | {"revision_id": review_id}
                                    | overrides, today=TODAY)
        db.session.commit()
        return found

    def hold(self, review_id: int, who: Actor = CLI, today: date = TODAY):
        found = mr().hold(db.session, who, review_id, today=today)
        db.session.commit()
        return found

    def close(self, review_id: int, who: Actor = CLI, today: date = TODAY):
        found = mr().close(db.session, who, review_id, today=today)
        db.session.commit()
        return found

    def edit(self, register: str, record_id: int, data: dict, who: Actor = CLI, **kwargs):
        found = getattr(mr(), register).update(db.session, who, record_id, data, **kwargs)
        db.session.commit()
        return found

    def held(self, **overrides):
        """A held review with one attendee."""
        found = self.review(asistente_ids=[self.person("Luis Mora")], **overrides)
        return self.hold(found.id)

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def count(self, model) -> int:
        return db.session.query(model).count()

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message


class ReviewWriteTestCase(Helpers, ServiceBase):
    def test_create_starts_a_draft_with_attendees_comments_stamps_and_audit(self) -> None:
        ana, luis = self.person("Ana"), self.person("Luis")
        created = self.review(actor(AUDITOR), asistente_ids=[luis, ana, ana],
                              comentario_recursos="Falta un técnico", conclusiones=" ")
        m = models()
        self.assertEqual((m.EstadoRevisionDireccion.borrador, None, None, None, [ana, luis]),
                         (created.estado, created.datos_entrada, created.celebrada_por_id,
                          created.fecha_cierre, created.asistente_ids))
        self.assertEqual(("Falta un técnico", None), (created.comentario_recursos,
                                                      created.conclusiones))
        self.assertEqual((7, 7), (created.created_by_id, created.updated_by_id))
        (row,) = self.rows("revisiones_direccion")
        self.assertEqual(("create", created.id, "Borrador", [ana, luis]),
                         (row.action, row.entity_id, row.after["estado"],
                          row.after["asistente_ids"]))
        self.assertEqual(["Borrador", "Celebrada", "Cerrada"],
                         [s.value for s in m.EstadoRevisionDireccion])

    def test_invalid_or_missing_values_are_refused_and_nothing_is_written(self) -> None:
        inactive = self.person("Eva", activo=False)
        create = mr().reviews.create
        for key in ("fecha", "periodo_desde", "periodo_hasta"):
            with self.subTest(missing=key):
                data = {k: v for k, v in REVIEW.items() if k != key}
                self.assertIn(key, self.refused(errors().ValidationError, create, CLI, data))
        cases = {
            "period backwards": ({"periodo_desde": date(2026, 7, 1)}, mr().PERIOD_ORDER),
            "period after the meeting": ({"periodo_hasta": date(2026, 7, 11)},
                                         mr().PERIOD_AFTER_MEETING),
            "state": ({"estado": "celebrada"}, "Campos no permitidos"),
            "frozen inputs": ({"datos_entrada": {}}, "Campos no permitidos"),
            "held by": ({"celebrada_por_id": inactive}, "Campos no permitidos"),
            "text date": ({"fecha": "2026-07-10"}, "fecha"),
            "unknown attendee": ({"asistente_ids": [999]}, "999"),
            "attendees not a list": ({"asistente_ids": "1"}, "asistente_ids"),
            "inactive attendee": ({"asistente_ids": [inactive]}, "Eva"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.assertIn(message, self.refused(errors().ValidationError, create, CLI,
                                                    REVIEW | change))
        self.assertEqual(0, self.count(models().RevisionDireccion))
        self.assertEqual([], self.rows("revisiones_direccion"))

    def test_update_writes_changed_fields_only_and_a_no_op_writes_nothing(self) -> None:
        ana, luis = self.person("Ana"), self.person("Luis")
        found = self.review(asistente_ids=[ana])
        self.edit("reviews", found.id, {"comentario_mejora": "Digitalizar", "asistente_ids": [luis],
                                        "fecha": date(2026, 7, 15)})
        row = self.rows("revisiones_direccion")[-1]
        self.assertEqual(("update", {"comentario_mejora", "asistente_ids", "fecha", "updated_at"}),
                         (row.action, set(row.after)))
        self.assertEqual([luis], row.after["asistente_ids"])
        self.edit("reviews", found.id, {"comentario_mejora": "Digitalizar", "asistente_ids": [luis]})
        self.assertEqual(2, len(self.rows("revisiones_direccion")))
        message = self.refused(errors().ValidationError, mr().reviews.update, CLI, found.id,
                               {"periodo_hasta": date(2026, 7, 20)})
        self.assertEqual(mr().PERIOD_AFTER_MEETING, message)
        self.refused(errors().NotFound, mr().reviews.update, CLI, 999, {"conclusiones": "X"})

    def test_a_held_review_takes_only_comments_and_conclusions_a_closed_one_nothing(self) -> None:
        found = self.held()
        self.edit("reviews", found.id, {"comentario_contexto": "Nuevo cliente clave",
                                        "conclusiones": "El SGC es adecuado"})
        for change in ({"fecha": date(2026, 7, 9)}, {"asistente_ids": []},
                       {"periodo_desde": date(2026, 4, 2)}):
            with self.subTest(change=change):
                self.assertEqual(mr().ONLY_COMMENTS, self.refused(
                    errors().ValidationError, mr().reviews.update, CLI, found.id, change))
        self.close(found.id)
        self.assertEqual(mr().REVIEW_CLOSED, self.refused(
            errors().ValidationError, mr().reviews.update, CLI, found.id,
            {"conclusiones": "Otra"}))
        before = len(self.rows("revisiones_direccion"))
        self.edit("reviews", found.id, {"conclusiones": "El SGC es adecuado"})  # no-op
        self.assertEqual(before, len(self.rows("revisiones_direccion")))

    def test_only_a_draft_without_actions_is_deleted(self) -> None:
        found = self.review(asistente_ids=[self.person()])
        with_action = self.review()
        self.action(with_action.id)
        message = self.refused(errors().Conflict, mr().reviews.delete, CLI, with_action.id)
        self.assertEqual(mr().HAS_ACTIONS, message)
        held = self.held()
        self.assertEqual(mr().DELETE_NOT_DRAFT, self.refused(
            errors().ValidationError, mr().reviews.delete, CLI, held.id))
        review_id = found.id
        mr().reviews.delete(db.session, actor(ADMIN), review_id)
        db.session.commit()
        self.assertIsNone(db.session.get(models().RevisionDireccion, review_id))
        row = self.rows("revisiones_direccion")[-1]
        self.assertEqual(("delete", review_id, 1), (row.action, row.entity_id,
                                                    len(row.before["asistente_ids"])))
        self.assertEqual(0, db.session.execute(text(
            "SELECT count(*) FROM revision_asistentes WHERE revision_id = :id"),
            {"id": review_id}).scalar_one())


class HoldAndCloseTestCase(Helpers, ServiceBase):
    def test_hold_freezes_the_inputs_and_later_records_leave_them_unchanged(self) -> None:
        held_by = self.person("Directora", user_id=7)
        found = self.review(asistente_ids=[self.person("Luis")])
        self.insert(SatisfaccionCliente, fecha_encuesta=date(2026, 5, 1), cliente="ACME",
                    puntuacion=8)
        live = mr().inputs(db.session, actor(AUDITOR), REVIEW["periodo_desde"],
                           REVIEW["periodo_hasta"], today=TODAY, review=found)
        self.assertEqual(live, mr().preview(db.session, actor(AUDITOR), found, today=TODAY))
        held = self.hold(found.id, actor(ADMIN))
        m = models()
        self.assertEqual((m.EstadoRevisionDireccion.celebrada, held_by, None, live),
                         (held.estado, held.celebrada_por_id, held.fecha_cierre,
                          held.datos_entrada))
        row = self.rows("revisiones_direccion")[-1]
        self.assertEqual(("update", "Borrador", "Celebrada", held_by),
                         (row.action, row.before["estado"], row.after["estado"],
                          row.after["celebrada_por_id"]))
        self.assertEqual(1, row.after["datos_entrada"]["satisfaccion"]["encuestas"])
        # Registers change afterwards: the frozen inputs do not.
        self.insert(SatisfaccionCliente, fecha_encuesta=date(2026, 6, 1), cliente="Beta",
                    puntuacion=4)
        self.insert(NoConformidad, descripcion="Tarde", fecha_detectada=date(2026, 6, 2))
        db.session.expire_all()
        stored = db.session.get(m.RevisionDireccion, found.id)
        self.assertEqual(live, stored.datos_entrada)
        self.assertEqual(live, mr().preview(db.session, actor(AUDITOR), stored, today=TODAY))
        now = mr().inputs(db.session, CLI, REVIEW["periodo_desde"], REVIEW["periodo_hasta"],
                          today=TODAY, review=stored)
        self.assertEqual((2, 1), (now["satisfaccion"]["encuestas"],
                                  now["no_conformidades"]["abiertas_en_periodo"]))
        self.assertEqual(1, stored.datos_entrada["satisfaccion"]["encuestas"])

    def test_hold_needs_a_draft_a_meeting_not_in_the_future_and_attendees(self) -> None:
        empty = self.review()
        self.assertEqual(mr().NO_ATTENDEES, self.refused(
            errors().ValidationError, mr().hold, CLI, empty.id, today=TODAY))
        future = self.review(fecha=date(2026, 10, 13), asistente_ids=[self.person()])
        self.assertEqual(mr().MEETING_IN_FUTURE, self.refused(
            errors().ValidationError, mr().hold, CLI, future.id, today=TODAY))
        self.hold(future.id, today=date(2026, 10, 13))
        self.assertEqual(mr().NOT_DRAFT, self.refused(
            errors().ValidationError, mr().hold, CLI, future.id, today=date(2026, 10, 13)))
        self.refused(errors().NotFound, mr().hold, CLI, 999, today=TODAY)
        with self.assertRaises(ValueError):
            mr().hold(db.session, CLI, empty.id, today=datetime(2026, 10, 12))
        db.session.rollback()
        self.assertIsNone(db.session.get(models().RevisionDireccion, empty.id).datos_entrada)

    def test_close_needs_a_held_review_with_conclusions_and_records_the_date(self) -> None:
        draft = self.review(conclusiones="Adecuado")
        self.assertEqual(mr().NOT_HELD, self.refused(
            errors().ValidationError, mr().close, CLI, draft.id, today=TODAY))
        held = self.held()
        self.assertEqual(mr().CONCLUSIONS_REQUIRED, self.refused(
            errors().ValidationError, mr().close, CLI, held.id, today=TODAY))
        self.edit("reviews", held.id, {"conclusiones": "El SGC es conveniente, adecuado y eficaz"})
        closed = self.close(held.id, actor(ADMIN))
        self.assertEqual((models().EstadoRevisionDireccion.cerrada, TODAY),
                         (closed.estado, closed.fecha_cierre))
        row = self.rows("revisiones_direccion")[-1]
        self.assertEqual(({"estado": "Celebrada", "fecha_cierre": None}, "Cerrada"),
                         ({k: row.before[k] for k in ("estado", "fecha_cierre")},
                          row.after["estado"]))
        self.assertEqual(mr().NOT_HELD, self.refused(
            errors().ValidationError, mr().close, CLI, held.id, today=TODAY))

    def test_only_administrators_hold_and_close_and_never_through_mcp(self) -> None:
        found = self.review(asistente_ids=[self.person()], conclusiones="Adecuado")
        for who in (actor(AUDITOR), actor(OPERATIVO), mcp_actor(ADMIN),
                    actor(ADMIN, scopes={"read"})):
            for transition in (mr().hold, mr().close):
                with self.subTest(role=who.role.name, channel=who.channel,
                                  transition=transition.__name__):
                    self.refused(errors().PermissionDenied, transition, who, found.id,
                                 today=TODAY)
        self.hold(found.id, actor(ADMIN))  # no person linked to user 7: nobody recorded
        self.assertIsNone(db.session.get(models().RevisionDireccion, found.id).celebrada_por_id)
        self.refused(errors().PermissionDenied, mr().close, mcp_actor(ADMIN), found.id,
                     today=TODAY)


class ActionTestCase(Helpers, ServiceBase):
    def test_create_starts_pending_with_stamps_and_audit(self) -> None:
        review = self.review()
        created = self.action(review.id, actor(AUDITOR), tipo="cambio_sgc")
        m = models()
        self.assertEqual((m.EstadoAccionRevision.pendiente, m.TipoAccionRevision.cambio_sgc,
                          None, None, 7),
                         (created.estado, created.tipo, created.fecha_completada,
                          created.resultado, created.created_by_id))
        (row,) = self.rows("acciones_revision")
        self.assertEqual(("create", "Pendiente", "Cambio en el SGC"),
                         (row.action, row.after["estado"], row.after["tipo"]))
        self.assertFalse(mr().is_overdue(created, today=TODAY))
        self.assertTrue(mr().is_overdue(created, today=date(2027, 1, 1)))

    def test_a_review_date_never_moves_past_its_completed_actions(self) -> None:
        review = self.review()  # held on 10/07/2026
        self.action(review.id, estado="completada", fecha_completada=date(2026, 7, 10))
        self.action(review.id)  # pending: no completion date to keep
        message = self.refused(errors().ValidationError, mr().reviews.update, CLI, review.id,
                               {"fecha": date(2026, 7, 15)})
        self.assertEqual(mr().ACTIONS_COMPLETED_BEFORE, message)
        self.assertEqual(date(2026, 7, 10), db.session.get(models().RevisionDireccion,
                                                            review.id).fecha)
        self.edit("reviews", review.id, {"fecha": date(2026, 7, 8)})  # earlier is fine
        self.assertEqual(date(2026, 7, 8), db.session.get(models().RevisionDireccion,
                                                           review.id).fecha)

    def test_the_completion_date_goes_with_the_completed_state(self) -> None:
        review = self.review()
        owner, inactive = self.person(), self.person("Eva", activo=False)
        create = mr().actions.create
        valid = ACTION | {"revision_id": review.id, "responsable_id": owner}
        cases = {
            "completed without a date": ({"estado": "completada"}, mr().COMPLETION_REQUIRED),
            "a date while pending": ({"fecha_completada": date(2026, 8, 1)},
                                     mr().COMPLETION_ONLY_WHEN_DONE),
            "in the future": ({"estado": "completada", "fecha_completada": date(2026, 10, 13)},
                              mr().COMPLETION_IN_FUTURE),
            "before the review": ({"estado": "completada",
                                   "fecha_completada": date(2026, 7, 9)},
                                  mr().COMPLETION_BEFORE_REVIEW),
            "unknown review": ({"revision_id": 999}, mr().UNKNOWN_REVIEW),
            "unknown owner": ({"responsable_id": 999}, "responsable_id"),
            "inactive owner": ({"responsable_id": inactive}, "desactivada"),
            "bad type": ({"tipo": "Mejora"}, "tipo"),
            "unknown key": ({"vencida": True}, "Campos no permitidos"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.assertIn(message, self.refused(errors().ValidationError, create, CLI,
                                                    valid | change, today=TODAY))
        self.assertEqual(0, self.count(models().AccionRevision))
        done = create(db.session, CLI, valid | {"estado": "completada",
                                                "fecha_completada": date(2026, 7, 10)},
                      today=TODAY)
        self.assertEqual(date(2026, 7, 10), done.fecha_completada)
        with self.assertRaises(ValueError):
            create(db.session, CLI, valid, today=None)

    def test_actions_are_added_until_closing_then_only_followed_up(self) -> None:
        review = self.review(asistente_ids=[self.person("Luis")], conclusiones="Adecuado")
        draft_action = self.action(review.id)
        self.hold(review.id)
        held_action = self.action(review.id, tipo="recursos")
        self.edit("actions", held_action.id, {"descripcion": "Contratar un técnico"},
                  today=TODAY)
        self.close(review.id)
        self.assertEqual(mr().REVIEW_CLOSED_FOR_ACTIONS, self.refused(
            errors().ValidationError, mr().actions.create, CLI,
            ACTION | {"revision_id": review.id, "responsable_id": draft_action.responsable_id},
            today=TODAY))
        for change in ({"descripcion": "Otra"}, {"fecha_limite": date(2027, 1, 31)},
                       {"tipo": "mejora"}):
            with self.subTest(change=change):
                self.assertEqual(mr().FOLLOW_UP_ONLY, self.refused(
                    errors().ValidationError, mr().actions.update, CLI, held_action.id,
                    change, today=TODAY))
        self.assertEqual(mr().REVIEW_FIXED, self.refused(
            errors().ValidationError, mr().actions.update, CLI, held_action.id,
            {"revision_id": self.review().id}, today=TODAY))
        self.edit("actions", held_action.id, {"estado": "en_curso"}, today=TODAY)
        done = self.edit("actions", held_action.id,
                         {"estado": "completada", "fecha_completada": date(2026, 10, 1),
                          "resultado": "Técnico incorporado"}, today=TODAY)
        self.assertEqual((models().EstadoAccionRevision.completada, "Técnico incorporado"),
                         (done.estado, done.resultado))
        self.assertEqual(mr().COMPLETION_ONLY_WHEN_DONE, self.refused(
            errors().ValidationError, mr().actions.update, CLI, held_action.id,
            {"estado": "en_curso"}, today=TODAY))
        before = len(self.rows("acciones_revision"))
        self.edit("actions", held_action.id, {"descripcion": "Contratar un técnico"},
                  today=TODAY)  # no-op on a closed review writes nothing
        self.assertEqual(before, len(self.rows("acciones_revision")))
        self.assertEqual(mr().REVIEW_CLOSED_FOR_ACTIONS, self.refused(
            errors().ValidationError, mr().actions.delete, CLI, draft_action.id))
        other = self.action(self.review().id)
        mr().actions.delete(db.session, actor(ADMIN), other.id)
        db.session.commit()
        self.assertEqual("delete", self.rows("acciones_revision")[-1].action)

    def test_lists_filter_and_the_dashboard_flags_overdue_actions(self) -> None:
        first, second = self.review(), self.review(fecha=date(2026, 8, 1))
        ana, eva = self.person("Ana"), self.person("Eva")
        late = self.action(first.id, responsable_id=ana, fecha_limite=date(2026, 9, 1))
        later = self.action(first.id, responsable_id=ana, fecha_limite=date(2026, 9, 30),
                            estado="en_curso")
        self.action(first.id, responsable_id=eva, fecha_limite=date(2026, 9, 1),
                    estado="completada", fecha_completada=date(2026, 9, 2))
        self.action(second.id, responsable_id=eva, tipo="recursos", estado="cancelada",
                    fecha_limite=date(2026, 9, 1))
        self.action(second.id, responsable_id=eva, fecha_limite=TODAY)  # due today: not late
        actions = mr().actions

        def ids(**filters):
            return [found.id for found in actions.list_(db.session, CLI, **filters)]

        self.assertEqual([late.id, later.id], ids(vencida=True, today=TODAY))
        self.assertEqual(3, len(ids(vencida=False, today=TODAY)))
        self.assertEqual(3, len(ids(revision_id=first.id)))
        self.assertEqual([late.id, later.id], ids(responsable_id=ana))
        self.assertEqual(1, len(ids(estado="cancelada", tipo="recursos")))
        self.assertEqual([], ids(revision_id="x"))
        items, total = actions.list_page(db.session, CLI, revision_id=second.id, per_page=1)
        self.assertEqual((1, 2), (len(items), total))
        for bad in ({"estado": "Pendiente"}, {"tipo": "otra"}, {"vencida": "si", "today": TODAY}):
            with self.subTest(bad=bad):
                self.refused(errors().ValidationError, actions.list_, CLI, **bad)
        with self.assertRaises(ValueError):
            actions.list_(db.session, CLI, vencida=True)
        overdue = mr().overdue_actions(db.session, actor(AUDITOR), today=TODAY)
        self.assertEqual([late.id, later.id], [found.id for found in overdue])
        self.refused(errors().PermissionDenied, mr().overdue_actions, actor(OPERATIVO),
                     today=TODAY)

    def test_reviews_filter_by_state_and_year(self) -> None:
        old = self.review(fecha=date(2025, 12, 1), periodo_desde=date(2025, 1, 1),
                          periodo_hasta=date(2025, 11, 30))
        new = self.held()
        reviews = mr().reviews

        def ids(**filters):
            return [found.id for found in reviews.list_(db.session, CLI, **filters)]

        self.assertEqual([new.id, old.id], ids())  # newest meeting first
        self.assertEqual([old.id], ids(anio=2025))
        self.assertEqual([new.id], ids(estado="celebrada", anio=2026))
        self.assertEqual([], ids(anio="2026"))
        self.assertEqual(([old.id], 2), (
            [found.id for found in reviews.list_page(db.session, CLI, page=2, per_page=1)[0]],
            reviews.list_page(db.session, CLI)[1]))
        self.refused(errors().ValidationError, reviews.list_, CLI, estado="Celebrada")


class InputsTestCase(Helpers, ServiceBase):
    """Each input section with records inside and outside 1 April to 30 June 2026."""

    def scenario(self):
        owner = self.person("Eva Gil")
        earlier = self.review(fecha=date(2026, 3, 15), periodo_desde=date(2025, 10, 1),
                              periodo_hasta=date(2026, 3, 1))
        this = self.review(asistente_ids=[self.person("Luis")])
        later = self.review(fecha=date(2026, 9, 1), periodo_desde=date(2026, 7, 1),
                            periodo_hasta=date(2026, 8, 31))
        made = {}
        for name, values in {
            "late": {"fecha_limite": date(2026, 5, 1)},
            "going": {"estado": "en_curso"},
            "done_in": {"estado": "completada", "fecha_completada": date(2026, 5, 10),
                        "fecha_limite": date(2026, 6, 1)},
            "done_after": {"estado": "completada", "fecha_completada": date(2026, 8, 1),
                           "fecha_limite": date(2026, 7, 31)},
            "cancelled": {"estado": "cancelada", "fecha_limite": date(2026, 4, 15)},
            "done_before": {"estado": "completada", "fecha_completada": date(2026, 3, 20),
                            "fecha_limite": date(2026, 3, 31)},
        }.items():
            made[name] = self.action(earlier.id, responsable_id=owner,
                                     descripcion=f"Acción {name}", **values)
        self.action(this.id, responsable_id=owner, fecha_limite=date(2026, 5, 1))
        self.action(later.id, responsable_id=owner, fecha_limite=date(2026, 5, 1))
        return this, later, made

    def inputs(self, review=None, hasta=date(2026, 6, 30)):
        return mr().inputs(db.session, actor(AUDITOR), date(2026, 4, 1), hasta, today=TODAY,
                           review=review)

    def test_earlier_reviews_actions_open_at_the_cut_off_and_completed_in_the_period(self) -> None:
        this, later, made = self.scenario()
        found = self.inputs(this)
        self.assertEqual({"desde": "2026-04-01", "hasta": "2026-06-30"}, found["periodo"])
        self.assertEqual("2026-06-30", found["fecha_corte"])
        section = found["acciones_previas"]
        self.assertEqual(
            (3, {"pendiente": 1, "en_curso": 1}, 1, 1, 1),
            (section["abiertas"], section["por_estado"], section["completadas_despues"],
             section["vencidas"], section["completadas_en_periodo"]))
        self.assertEqual([(made["late"].id, True, "Eva Gil"), (made["done_after"].id, False,
                                                               "Eva Gil"),
                          (made["going"].id, False, "Eva Gil")],
                         [(item["id"], item["vencida"], item["responsable"])
                          for item in section["lista"]])
        self.assertEqual(("2026-05-01", "pendiente", "mejora", "Acción late", "2026-03-15"),
                         tuple(section["lista"][0][k] for k in (
                             "fecha_limite", "estado", "tipo", "descripcion", "revision_fecha")))
        json.dumps(found)
        # A later review also sees this one's action; without a review, every review
        # dated up to the period's end counts (this one is dated after it).
        self.assertEqual(4, self.inputs(later)["acciones_previas"]["abiertas"])
        self.assertEqual(3, self.inputs()["acciones_previas"]["abiertas"])

    def test_stakeholders_risks_satisfaction_and_indicators(self) -> None:
        for nombre, created, updated in (
            ("Nuevo proveedor", at(2026, 5, 1), at(2026, 5, 1)),
            ("Clientes", at(2025, 12, 1), at(2026, 6, 30, 23, 59)),
            ("Plantilla", at(2025, 12, 1), at(2025, 12, 1)),
            ("Legado", None, None),
            ("Banco", at(2026, 7, 1), at(2026, 7, 1)),
        ):
            self.insert(ParteInteresada, nombre=nombre, created_at=created, updated_at=updated)
        for fecha, puntuacion in ((date(2026, 4, 1), 8), (date(2026, 6, 30), 9),
                                  (date(2026, 3, 31), 2), (date(2026, 7, 1), 1)):
            self.insert(SatisfaccionCliente, fecha_encuesta=fecha, cliente="ACME",
                        puntuacion=puntuacion)
        for fecha in (datetime(2026, 5, 5, 10), datetime(2026, 7, 1)):
            self.insert(AuditoriaIndicador, area_auditoria="Compras", fecha_auditoria=fecha,
                        indicador_desempeno="Plazo medio 3 días", resultado="Conforme")
        found = self.inputs()
        parties = found["contexto"]["partes_interesadas"]
        self.assertEqual((5, 1, 1, ["Clientes", "Nuevo proveedor"]),
                         (parties["total"], parties["nuevas"], parties["modificadas"],
                          parties["lista"]))
        self.assertEqual({"encuestas": 2, "media": 8.5}, found["satisfaccion"])
        self.assertEqual({"total": 1, "lista": [{
            "area": "Compras", "fecha": "2026-05-05", "indicador": "Plazo medio 3 días",
            "resultado": "Conforme"}]}, found["indicadores"])
        empty = mr().inputs(db.session, CLI, date(2020, 1, 1), date(2020, 1, 31), today=TODAY)
        self.assertEqual({"encuestas": 0, "media": None}, empty["satisfaccion"])

    def test_risks_by_level_and_opportunities_by_priority(self) -> None:
        """Every current record by band (RO7), those created or else updated in the
        period, and the open high or critical risks with their owners."""
        eva = self.person("Eva Gil")
        base = {"descripcion": "d", "origen": "interno", "fecha_identificacion": date(2026, 1, 1)}
        for titulo, values, created, updated in (
            ("Fallo de proveedor", {"probabilidad": 4, "impacto": 5, "responsable_id": eva},
             at(2026, 4, 1), at(2026, 4, 1)),
            ("Rotación", {"probabilidad": 3, "impacto": 4, "estado": "cerrado"},
             at(2026, 1, 1), at(2026, 4, 10)),
            ("Sin valorar", {}, at(2025, 12, 1), at(2025, 12, 1)),
            ("Legado", {"probabilidad": 1, "impacto": 2}, None, None),
            ("Alto controlado", {"probabilidad": 4, "impacto": 3, "estado": "controlado"},
             at(2025, 1, 1), at(2025, 1, 1)),
            ("Después del periodo", {"probabilidad": 2, "impacto": 3}, at(2026, 7, 1),
             at(2026, 7, 1)),
        ):
            self.insert(Riesgo, titulo=titulo, created_at=created, updated_at=updated,
                        **(base | values))
        for titulo, values, created, updated in (
            ("Nuevo mercado", {"beneficio": 5, "viabilidad": 4}, at(2026, 1, 1),
             at(2026, 4, 10)),
            ("Certificación", {"beneficio": 2, "viabilidad": 4}, at(2026, 5, 5), at(2026, 5, 5)),
            ("Pendiente", {}, None, None),
        ):
            self.insert(Oportunidad, titulo=titulo, created_at=created, updated_at=updated,
                        **(base | values))
        context = self.inputs()["contexto"]
        self.assertEqual({"cuestiones", "partes_interesadas", "alcance", "riesgos",
                          "oportunidades"}, set(context))
        self.assertEqual({
            "total": 6,
            "por_nivel": {"bajo": 1, "medio": 1, "alto": 2, "critico": 1, "sin_valorar": 1},
            "nuevos": 1, "modificados": 1,
            "lista": [{"id": 1, "titulo": "Fallo de proveedor", "nivel": "critico",
                       "estado": "identificado"},
                      {"id": 2, "titulo": "Rotación", "nivel": "alto", "estado": "cerrado"}],
            "altos_abiertos": 2,
            "altos_abiertos_lista": [
                {"id": 1, "titulo": "Fallo de proveedor", "nivel": "critico",
                 "estado": "identificado", "responsable": "Eva Gil"},
                {"id": 5, "titulo": "Alto controlado", "nivel": "alto", "estado": "controlado",
                 "responsable": None}],
        }, context["riesgos"])
        self.assertEqual({
            "total": 3,
            "por_prioridad": {"baja": 0, "media": 1, "alta": 1, "sin_valorar": 1},
            "nuevas": 1, "modificadas": 1,
            "lista": [{"id": 1, "titulo": "Nuevo mercado", "prioridad": "alta",
                       "estado": "identificada"},
                      {"id": 2, "titulo": "Certificación", "prioridad": "media",
                       "estado": "identificada"}],
        }, context["oportunidades"])
        empty = mr().inputs(db.session, CLI, date(2020, 1, 1), date(2020, 1, 31), today=TODAY)
        self.assertEqual((0, []), (empty["contexto"]["oportunidades"]["nuevas"],
                                   empty["contexto"]["riesgos"]["lista"]))

    def test_risk_bands_at_their_edges_and_a_closed_critical_risk(self) -> None:
        """A product of 9 is still medio and 10 already alto (RO1); a closed critical risk
        counts in its band but not among the open high or critical ones."""
        base = {"descripcion": "d", "origen": "interno", "fecha_identificacion": date(2026, 1, 1)}
        for titulo, values in (("Nueve", {"probabilidad": 3, "impacto": 3}),
                               ("Diez", {"probabilidad": 2, "impacto": 5}),
                               ("Crítico cerrado", {"probabilidad": 5, "impacto": 5,
                                                    "estado": "cerrado"})):
            self.insert(Riesgo, titulo=titulo, **(base | values))
        found = self.inputs()["contexto"]["riesgos"]
        self.assertEqual({"bajo": 0, "medio": 1, "alto": 1, "critico": 1, "sin_valorar": 0},
                         found["por_nivel"])
        self.assertEqual((1, [{"id": 2, "titulo": "Diez", "nivel": "alto",
                               "estado": "identificado", "responsable": None}]),
                         (found["altos_abiertos"], found["altos_abiertos_lista"]))

    def test_context_issues_changed_in_the_period_and_the_scope_in_force(self) -> None:
        """9.3.2 b (OC5 of ``organization-context``): the issues created, or else updated,
        in the period, those superseded in it, the register's counts now, and the number
        and approval date of the scope version in force."""
        base = {"tipo": "externa", "categoria": "ambiental", "descripcion": "d",
                "efecto": "negativo", "pertinente": True, "fecha_identificacion": date(2026, 1, 1)}
        made = {}
        for titulo, values, created, updated in (
            ("Sequías", {"cambio_climatico": True}, at(2026, 4, 1), at(2026, 4, 1)),
            ("Inflación", {"categoria": "economica"}, at(2025, 12, 1), at(2026, 6, 30, 23, 59)),
            ("Normativa derogada", {"categoria": "legal", "estado": "superada"},
             at(2025, 1, 1), at(2026, 5, 2)),
            ("Rotación", {"tipo": "interna", "categoria": "recursos", "pertinente": False,
                          "justificacion_pertinencia": "Plantilla estable",
                          "cambio_climatico": True}, at(2025, 1, 1), at(2025, 1, 1)),
            ("Legado", {"categoria": "social"}, None, None),
            ("Después del periodo", {}, at(2026, 7, 1), at(2026, 7, 1)),
        ):
            made[titulo] = self.insert(CuestionContexto, titulo=titulo, created_at=created,
                                       updated_at=updated, **(base | values))
        context = self.inputs()["contexto"]
        self.assertEqual({"version": None, "fecha_aprobacion": None}, context["alcance"])
        self.assertEqual({
            "nuevas": 1, "modificadas": 2, "superadas": 1, "vigentes": 5, "pertinentes": 4,
            "cambio_climatico": 2, "cambio_climatico_pertinentes": 1,
            "lista": [
                {"id": made["Sequías"], "titulo": "Sequías", "tipo": "externa",
                 "categoria": "ambiental", "pertinente": True, "cambio_climatico": True,
                 "estado": "vigente", "cambio": "nueva"},
                {"id": made["Inflación"], "titulo": "Inflación", "tipo": "externa",
                 "categoria": "economica", "pertinente": True, "cambio_climatico": False,
                 "estado": "vigente", "cambio": "modificada"},
                {"id": made["Normativa derogada"], "titulo": "Normativa derogada",
                 "tipo": "externa", "categoria": "legal", "pertinente": True,
                 "cambio_climatico": False, "estado": "superada", "cambio": "modificada"},
            ],
        }, context["cuestiones"])
        json.dumps(context)

        approver = self.person("Ana Pérez")
        approval = {"declaracion": "Envases", "aprobado_por_id": approver}
        self.insert(AlcanceSGC, version=1, estado=EstadoAlcance.obsoleto,
                    fecha_aprobacion=date(2025, 3, 1), fecha_obsolescencia=date(2026, 5, 4),
                    **approval)
        self.insert(AlcanceSGC, version=2, estado=EstadoAlcance.vigente,
                    fecha_aprobacion=date(2026, 5, 4), **approval)
        self.insert(AlcanceSGC, version=3, estado=EstadoAlcance.borrador, declaracion="Nueva")
        self.assertEqual({"version": 2, "fecha_aprobacion": "2026-05-04"},
                         self.inputs()["contexto"]["alcance"])
        empty = mr().inputs(db.session, CLI, date(2020, 1, 1), date(2020, 1, 31), today=TODAY)
        self.assertEqual((0, 0, 0, []), tuple(empty["contexto"]["cuestiones"][key] for key in (
            "nuevas", "modificadas", "superadas", "lista")))

    def test_objectives_are_judged_at_the_period_end_capped_at_today(self) -> None:
        owner = self.person()

        def objective(titulo, estado=EstadoObjetivo.activo, inicio=date(2026, 1, 1),
                      limite=date(2026, 12, 31)):
            return self.insert(ObjetivoCalidad, titulo=titulo, indicador="%", meta=Decimal(95),
                               sentido=SentidoObjetivo.mayor_igual, responsable_id=owner,
                               anio=2026, fecha_inicio=inicio, fecha_limite=limite,
                               frecuencia=FrecuenciaMedicion.mensual, estado=estado)

        met, missed = objective("En meta"), objective("Fuera de meta")
        objective("Alcanzado", EstadoObjetivo.alcanzado, date(2026, 2, 1), date(2026, 4, 30))
        objective("Cancelado", EstadoObjetivo.cancelado)
        objective("Posterior", inicio=date(2026, 7, 1))
        objective("Sin medir")
        for objetivo_id, fecha, valor in ((met, date(2026, 6, 30), 96), (met, date(2026, 7, 31), 80),
                                          (missed, date(2026, 5, 31), 90)):
            self.insert(MedicionObjetivo, objetivo_id=objetivo_id, fecha=fecha,
                        valor=Decimal(valor))
        section = self.inputs()["objetivos"]
        self.assertEqual(
            (4, 1, 1, 2, 1, {"activo": 3, "alcanzado": 1, "no_alcanzado": 0}),
            (section["total"], section["en_meta"], section["fuera_de_meta"],
             section["sin_medicion"], section["medicion_vencida"], section["por_estado"]))
        self.assertEqual([{"titulo": "Fuera de meta", "ultimo_valor": "90.0000",
                           "ultima_fecha": "2026-05-31", "meta": "95.0000", "sentido": "≥"}],
                         section["fuera_de_meta_lista"])
        # A period ending after today is judged on today: July's 80 now counts.
        later = self.inputs(hasta=date(2026, 12, 31))
        self.assertEqual("2026-10-12", later["fecha_corte"])
        self.assertEqual((5, 0, 2), (later["objetivos"]["total"], later["objetivos"]["en_meta"],
                                     later["objetivos"]["fuera_de_meta"]))

    def test_nonconformities_corrective_actions_audits_improvements_and_training(self) -> None:
        nc = self.insert(NoConformidad, descripcion="Retraso", fecha_detectada=date(2026, 4, 10),
                         gravedad=GravedadNoConformidad.mayor, origen=OrigenNoConformidad.cliente)
        for values in (
            {"fecha_detectada": date(2026, 6, 30), "gravedad": GravedadNoConformidad.menor,
             "origen": OrigenNoConformidad.auditoria, "estado": EstadoNoConformidad.cerrada,
             "fecha_cierre": date(2026, 6, 30)},
            {"fecha_detectada": date(2026, 5, 1)},
            {"fecha_detectada": date(2026, 3, 1), "gravedad": GravedadNoConformidad.mayor,
             "estado": EstadoNoConformidad.cerrada, "fecha_cierre": date(2026, 4, 20)},
            {"fecha_detectada": date(2026, 2, 1), "estado": EstadoNoConformidad.cancelada,
             "fecha_cierre": date(2026, 5, 1)},
        ):
            self.insert(NoConformidad, descripcion="Otra", **values)
        owner = self.person()
        for resultado, fecha in ((ResultadoVerificacion.eficaz, date(2026, 5, 15)),
                                 (ResultadoVerificacion.no_eficaz, date(2026, 6, 1)),
                                 (ResultadoVerificacion.eficaz, date(2026, 7, 15)), (None, None)):
            self.insert(AccionCorrectiva, no_conformidad_id=nc, descripcion="Reajustar",
                        responsable_id=owner, fecha_prevista=date(2026, 5, 1),
                        resultado_verificacion=resultado, fecha_verificacion=fecha)
        audits = {}
        for name, fecha, estado, tipos in (
            ("done", date(2026, 5, 10), EstadoAuditoriaEnum.COMPLETADA,
             (TipoHallazgo.nc_menor, TipoHallazgo.observacion)),
            ("pending", date(2026, 6, 1), EstadoAuditoriaEnum.PENDIENTE,
             (TipoHallazgo.oportunidad_mejora,)),
            ("before", date(2026, 3, 1), EstadoAuditoriaEnum.COMPLETADA, (TipoHallazgo.nc_mayor,)),
        ):
            audits[name] = self.insert(Auditoria, area_auditada=f"Área {name}", fecha=fecha,
                                       auditor="Luis", resultado="OK", estado=estado)
            for tipo in tipos:
                self.insert(HallazgoAuditoria, auditoria_id=audits[name], tipo=tipo,
                            descripcion="Hallazgo")
        for fecha in (datetime(2026, 4, 2, 8), datetime(2026, 3, 31, 23)):
            self.insert(Mejora, no_conformidad="Retrasos en compras", fecha_implementacion=fecha)
        for tema, fecha, horas in (("Seguridad", date(2026, 4, 15), 4),
                                   ("Calidad", date(2026, 6, 1), None),
                                   ("Idiomas", date(2026, 7, 1), 20)):
            self.insert(Capacitacion, tema=tema, fecha=fecha, personal="Ana",
                        duracion_horas=horas)
        found = self.inputs()
        ncs = found["no_conformidades"]
        self.assertEqual(
            (3, {"mayor": 1, "menor": 1, "observacion": 0, "sin_indicar": 1},
             {"auditoria": 1, "cliente": 1, "proceso": 0, "proveedor": 0, "otro": 0,
              "sin_indicar": 1}, 2, {"eficaces": 1, "no_eficaces": 1}),
            (ncs["abiertas_en_periodo"], ncs["por_gravedad"], ncs["por_origen"],
             ncs["cerradas_en_periodo"], ncs["acciones_correctivas"]))
        audited = found["auditorias"]
        self.assertEqual(
            (2, 1, {"nc_mayor": 0, "nc_menor": 1, "observacion": 1, "oportunidad_mejora": 1},
             [{"id": audits["done"], "area": "Área done", "fecha": "2026-05-10"}]),
            (audited["en_periodo"], audited["completadas"], audited["hallazgos_por_tipo"],
             audited["completadas_lista"]))
        self.assertEqual(1, found["mejoras"]["total"])
        self.assertEqual(("Retrasos en compras", "2026-04-02"),
                         (found["mejoras"]["lista"][0]["descripcion"],
                          found["mejoras"]["lista"][0]["fecha"]))
        self.assertEqual({"total": 2, "horas": 4, "temas": ["Calidad", "Seguridad"]},
                         found["capacitaciones"])

    def test_providers_by_state_evaluations_unsuitable_nonconformities_and_overdue(
            self) -> None:
        """9.3.2 c and 9.1.3 f: the providers' performance in the period (EP-3)."""
        decided = {"fecha_estado": date(2026, 1, 10)}
        ids = {}
        for nombre, estado, values in (
            ("Aceros", EstadoProveedor.aprobado,
             decided | {"fecha_proxima_evaluacion": date(2026, 6, 15)}),
            ("Bombas", EstadoProveedor.condicionado,
             decided | {"motivo_estado": "Plazos", "fecha_proxima_evaluacion": date(2026, 7, 15)}),
            ("Calibraciones", EstadoProveedor.bloqueado,
             decided | {"motivo_estado": "Lote", "fecha_proxima_evaluacion": date(2026, 5, 1)}),
            ("Docks", EstadoProveedor.pendiente, {}),
            ("Envases", EstadoProveedor.aprobado,
             decided | {"fecha_proxima_evaluacion": date(2026, 6, 30)}),
        ):
            ids[nombre] = self.insert(Proveedor, nombre=nombre, tipo_suministro="producto",
                                      estado=estado, **values)
        evaluator = self.person()
        for nombre, fecha, scores in (
            ("Aceros", date(2026, 4, 10), (4, 4, 4, 4)),  # 4.00 apto
            ("Aceros", date(2026, 6, 30), (2, 2, 2, 3)),  # 2.25 no apto, last day
            ("Bombas", date(2026, 5, 5), (3, 3, 3, 2)),  # 2.75 condicionado
            ("Calibraciones", date(2026, 5, 20), (2, 2, 2, 2)),  # 2.00 no apto
            ("Calibraciones", date(2026, 6, 1), (2, 2, 2, 3)),  # 2.25 no apto, the latest
            ("Envases", date(2026, 3, 31), (1, 1, 1, 1)),  # outside the period
            ("Envases", date(2026, 7, 1), (1, 1, 1, 1)),
        ):
            self.insert(EvaluacionProveedor, proveedor_id=ids[nombre], fecha=fecha,
                        evaluador_id=evaluator, **dict(zip(
                            ("calidad", "plazo", "servicio", "documentacion"), scores)))
        for nombre, fecha, origen in (
            ("Aceros", date(2026, 4, 15), OrigenNoConformidad.proveedor),
            ("Aceros", date(2026, 6, 30), OrigenNoConformidad.proveedor),
            ("Calibraciones", date(2026, 5, 1), OrigenNoConformidad.proveedor),
            (None, date(2026, 5, 2), OrigenNoConformidad.proveedor),
            (None, date(2026, 5, 3), OrigenNoConformidad.cliente),
            ("Aceros", date(2026, 3, 31), OrigenNoConformidad.proveedor),
            ("Bombas", date(2026, 7, 1), OrigenNoConformidad.proveedor),
        ):
            self.insert(NoConformidad, descripcion="Lote defectuoso", fecha_detectada=fecha,
                        origen=origen, proveedor_id=ids.get(nombre))
        found = self.inputs()["proveedores"]
        json.dumps(found)  # JSON-safe, as frozen into ``datos_entrada``
        self.assertEqual({
            "por_estado": {"pendiente": 1, "aprobado": 2, "condicionado": 1, "bloqueado": 1},
            # Not blocked and due before the cut-off (30 June): Aceros only.
            "evaluaciones_vencidas": 1,
            # (16 + 9 + 11 + 8 + 9) / 20 = 2.65
            "evaluaciones": {"total": 5, "media": "2.65",
                             "por_resultado": {"apto": 1, "condicionado": 1, "no_apto": 3}},
            "no_aptos": 2,
            "no_aptos_lista": [
                {"id": ids["Aceros"], "nombre": "Aceros", "fecha": "2026-06-30",
                 "puntuacion": "2.25", "estado": "aprobado"},
                {"id": ids["Calibraciones"], "nombre": "Calibraciones", "fecha": "2026-06-01",
                 "puntuacion": "2.25", "estado": "bloqueado"},
            ],
            "no_conformidades": {
                "total": 4, "sin_proveedor": 1,
                "por_proveedor": [{"id": ids["Aceros"], "nombre": "Aceros", "total": 2},
                                  {"id": ids["Calibraciones"], "nombre": "Calibraciones",
                                   "total": 1}],
            },
        }, found)
        # Cut off at today (12 October) when the period ends later: Bombas and Envases too.
        later = mr().inputs(db.session, CLI, date(2026, 4, 1), date(2026, 12, 31),
                            today=TODAY)["proveedores"]
        self.assertEqual((3, 6), (later["evaluaciones_vencidas"],
                                  later["evaluaciones"]["total"]))
        empty = mr().inputs(db.session, CLI, date(2020, 1, 1), date(2020, 1, 31),
                            today=TODAY)["proveedores"]
        self.assertEqual(({"total": 0, "media": None,
                           "por_resultado": {"apto": 0, "condicionado": 0, "no_apto": 0}},
                          0, [], {"total": 0, "sin_proveedor": 0, "por_proveedor": []}),
                         (empty["evaluaciones"], empty["no_aptos"], empty["no_aptos_lista"],
                          empty["no_conformidades"]))

    def test_the_providers_nonconformities_list_the_ten_most_cited(self) -> None:
        for number in range(mr().LIST_LIMIT + 1):
            provider = self.insert(Proveedor, nombre=f"Proveedor {number:02d}",
                                   tipo_suministro="producto")
            for _ in range(1 + (number == 3)):
                self.insert(NoConformidad, descripcion="Lote", fecha_detectada=date(2026, 5, 1),
                            origen=OrigenNoConformidad.proveedor, proveedor_id=provider)
        cited = self.inputs()["proveedores"]["no_conformidades"]
        self.assertEqual(mr().LIST_LIMIT + 2, cited["total"])
        self.assertEqual(mr().LIST_LIMIT, len(cited["por_proveedor"]))
        self.assertEqual(("Proveedor 03", 2), (cited["por_proveedor"][0]["nombre"],
                                              cited["por_proveedor"][0]["total"]))
        self.assertEqual(["Proveedor 00", "Proveedor 01", "Proveedor 02", "Proveedor 04"],
                         [row["nombre"] for row in cited["por_proveedor"][1:5]])

    def test_the_period_and_the_caller_are_checked_and_long_texts_are_cut(self) -> None:
        self.insert(Mejora, no_conformidad="x" * 300, fecha_implementacion=datetime(2026, 5, 1))
        text_ = self.inputs()["mejoras"]["lista"][0]["descripcion"]
        self.assertEqual((mr().SHORT_TEXT, "…"), (len(text_), text_[-1]))
        self.assertEqual(mr().PERIOD_ORDER, self.refused(
            errors().ValidationError, mr().inputs, CLI, date(2026, 7, 1), date(2026, 6, 30),
            today=TODAY))
        self.assertIn("periodo_desde", self.refused(
            errors().ValidationError, mr().inputs, CLI, "2026-04-01", date(2026, 6, 30),
            today=TODAY))
        self.refused(errors().PermissionDenied, mr().inputs, actor(OPERATIVO), date(2026, 4, 1),
                     date(2026, 6, 30), today=TODAY)
        with self.assertRaises(ValueError):
            mr().inputs(db.session, CLI, date(2026, 4, 1), date(2026, 6, 30), today="hoy")


class PolicyTestCase(Helpers, ServiceBase):
    def test_admins_and_auditors_read_and_write_admins_delete_operativo_nothing(self) -> None:
        review = self.review()
        action = self.action(review.id)
        cases = {
            "reviews": (review.id, {"conclusiones": "X"}, {}),
            "actions": (action.id, {"resultado": "X"}, {"today": TODAY}),
        }
        for register, (record_id, change, extra) in cases.items():
            service = getattr(mr(), register)
            for role in (ADMIN, AUDITOR):
                with self.subTest(register=register, read=role.name):
                    self.assertEqual(record_id, service.get(db.session, actor(role), record_id).id)
                    self.assertEqual(1, len(service.list_(db.session, actor(role))))
            denied = [(actor(OPERATIVO), "get"), (actor(OPERATIVO), "create"),
                      (actor(OPERATIVO), "update"), (actor(AUDITOR), "delete"),
                      (mcp_actor(ADMIN), "delete"), (actor(AUDITOR, scopes={"read"}), "update"),
                      (actor(AUDITOR, scopes={"write"}), "get")]
            for who, operation in denied:
                with self.subTest(register=register, denied=operation, role=who.role.name):
                    args = {"create": ({},), "update": (record_id, change),
                            "delete": (record_id,), "get": (record_id,)}[operation]
                    kwargs = extra if operation in ("create", "update") else {}
                    self.refused(errors().PermissionDenied, getattr(service, operation), who,
                                 *args, **kwargs)
            self.edit(register, record_id, change, mcp_actor(AUDITOR), **extra)
        self.refused(errors().PermissionDenied, mr().preview, actor(OPERATIVO), review,
                     today=TODAY)
        mr().actions.delete(db.session, actor(ADMIN), action.id)
        mr().reviews.delete(db.session, actor(ADMIN), review.id)
        db.session.commit()
        self.assertEqual(0, self.count(models().RevisionDireccion))

    def test_people_cited_by_a_review_or_an_action_cannot_be_deleted(self) -> None:
        from app.services import people

        attendee, chair = self.person("Luis"), self.person("Directora", user_id=7)
        review = self.review(asistente_ids=[attendee])
        self.hold(review.id, actor(ADMIN))
        action = self.action(review.id)
        for person_id in (attendee, chair, action.responsable_id):
            with self.subTest(person=person_id):
                self.assertEqual(people.STILL_REFERENCED, self.refused(
                    errors().Conflict, people.delete, CLI, person_id))
        self.assertEqual(3, self.count(Person))


class ForeignKeyCases:
    """The schema backs the service; shared by the SQLite and PostgreSQL cases."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_actions_key_refuses_a_delete_the_check_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        review = self.review()
        self.action(review.id)
        with patch.object(mr(), "_has_actions", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                mr().reviews.delete(db.session, CLI, review.id)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        expected = mr().HAS_ACTIONS if self.names_violated_constraints else mr().DELETE_CONFLICT
        self.assertEqual(expected, caught.exception.message)
        db.session.rollback()
        self.assertEqual(1, self.count(models().RevisionDireccion))

    def test_links_and_checks_are_enforced_by_the_database(self) -> None:
        self.enforce_foreign_keys()
        review = self.review(asistente_ids=[self.person("Luis")])
        held = self.hold(review.id)
        self.assertEqual(held.datos_entrada["periodo"],
                         {"desde": "2026-04-01", "hasta": "2026-06-30"})
        draft = self.review()
        action = self.action(draft.id)
        m = models()
        reviews, actions = m.RevisionDireccion.__table__, m.AccionRevision.__table__
        attendees = m.revision_asistentes
        for statement in (
            actions.insert().values(revision_id=999, tipo="mejora", descripcion="X",
                                    responsable_id=action.responsable_id,
                                    fecha_limite=date(2026, 12, 31)),
            actions.insert().values(revision_id=draft.id, tipo="mejora", descripcion="X",
                                    responsable_id=999, fecha_limite=date(2026, 12, 31)),
            attendees.insert().values(revision_id=draft.id, persona_id=999),
            Person.__table__.delete(),
            reviews.update().where(reviews.c.id == held.id).values(datos_entrada=None),
            reviews.update().where(reviews.c.id == draft.id).values(datos_entrada={"x": 1}),
            reviews.update().where(reviews.c.id == held.id).values(fecha_cierre=TODAY),
            reviews.update().where(reviews.c.id == draft.id).values(
                periodo_hasta=date(2026, 7, 11)),
            actions.update().where(actions.c.id == action.id).values(estado="completada"),
            actions.update().where(actions.c.id == action.id).values(
                fecha_completada=TODAY),
            actions.update().where(actions.c.id == action.id).values(estado="Completada"),
        ):
            with self.subTest(statement=str(statement)):
                with self.assertRaises(IntegrityError):
                    db.session.execute(statement)
                    db.session.commit()
                db.session.rollback()


class SqliteForeignKeyTestCase(ForeignKeyCases, Helpers, ServiceBase):
    names_violated_constraints = False

    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresForeignKeyTestCase(ForeignKeyCases, Helpers, unittest.TestCase):
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


class McpTestCase(McpDbCase):
    async def test_reviews_and_actions_round_trip_without_transitions(self) -> None:
        ana = self.seed("personas")
        who = mcp_actor(AUDITOR)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "revisiones_direccion", "data": {
                "fecha": "2026-10-05", "periodo_desde": "2026-01-01",
                "periodo_hasta": "2026-09-30", "asistente_ids": [ana],
                "comentario_contexto": "Sin cambios relevantes"}})
            self.assertFalse(created.is_error, created.content)
            record = created.structured_content
            self.assertEqual(("Borrador", [ana], None, "2026-10-05"),
                             tuple(record[k] for k in ("estado", "asistente_ids",
                                                       "datos_entrada", "fecha")))
            self.assertEqual({"desde": "2026-01-01", "hasta": "2026-09-30"},
                             record["preview"]["periodo"])
            self.assertEqual("2026-09-30", record["preview"]["fecha_corte"])
            for data in ({"estado": "celebrada"}, {"datos_entrada": {}},
                         {"fecha_cierre": "2026-10-12"}):
                with self.subTest(data=data):
                    refused = await self.call(who, "qms_update", {
                        "module": "revisiones_direccion", "id": record["id"], "data": data})
                    self.assertTrue(refused.is_error)
                    self.assertIn("Campos no permitidos", refused.content[0].text)
            action = {"module": "acciones_revision", "data": {
                "revision_id": record["id"], "tipo": "recursos", "descripcion": "Contratar",
                "responsable_id": ana, "fecha_limite": "2026-10-01"}}
            added = await self.call(who, "qms_create", action)
            self.assertFalse(added.is_error, added.content)
            self.assertEqual(("Pendiente", "Necesidad de recursos", True),
                             tuple(added.structured_content[k]
                                   for k in ("estado", "tipo", "vencida")))
            action_id = added.structured_content["id"]
            future = await self.call(who, "qms_update", {
                "module": "acciones_revision", "id": action_id,
                "data": {"estado": "completada", "fecha_completada": "2026-10-13"}})
            self.assertTrue(future.is_error)
            self.assertIn("futura", future.content[0].text)
            done = await self.call(who, "qms_update", {
                "module": "acciones_revision", "id": action_id,
                "data": {"estado": "completada", "fecha_completada": "2026-10-11"}})
            self.assertFalse(done.is_error, done.content)
            self.assertEqual(("Completada", "2026-10-11", False),
                             tuple(done.structured_content[k]
                                   for k in ("estado", "fecha_completada", "vencida")))
            for slug, filters, total in (
                ("revisiones_direccion", {"estado": "borrador", "anio": 2026}, 1),
                ("acciones_revision", {"revision_id": record["id"], "estado": "completada",
                                       "responsable_id": ana, "tipo": "recursos",
                                       "vencida": False}, 1),
                ("acciones_revision", {"vencida": True}, 0),
            ):
                with self.subTest(module=slug, filters=filters):
                    listed = await self.call(who, "qms_list", {"module": slug, "filters": filters})
                    self.assertFalse(listed.is_error, listed.content)
                    self.assertEqual(total, listed.structured_content["total"])
            denied = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                     {"module": "revisiones_direccion"})
            self.assertTrue(denied.is_error)
        self.assertEqual({"qms_modules", "qms_list", "qms_get", "qms_create", "qms_update"},
                         {tool.name for tool in await self.list_tools()})
        modules = (await self.call(mcp_actor(OPERATIVO), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        reviews, actions = described["revisiones_direccion"], described["acciones_revision"]
        self.assertFalse({"estado", "datos_entrada", "celebrada_por_id", "fecha_cierre"}
                         & {f["name"] for f in reviews["fields"]})
        self.assertEqual({"estado", "anio"}, {f["name"] for f in reviews["filters"]})
        self.assertEqual({"revision_id", "estado", "responsable_id", "tipo", "vencida"},
                         {f["name"] for f in actions["filters"]})
        self.assertEqual({"read": False, "create": False, "update": False},
                         reviews["permissions"])


if __name__ == "__main__":
    unittest.main()
