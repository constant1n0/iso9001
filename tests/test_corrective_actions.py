"""Corrective actions and their effectiveness (task NC-2 of ``nc-capa-loop``).

Decisions N3-N5: a nonconformity has corrective actions with an owner and
dates; administrators and auditors verify a done action, and the verifier is
never its owner; the nonconformity's state follows its actions, and closing
needs every action verified effective. An action verified not effective asks
for a later action; once one exists, the ineffective action stays as evidence
and no longer blocks closing. Runs with the audit flush guard installed, so a
write without its audit row fails.
"""

from __future__ import annotations

import os
import threading
import unittest
from datetime import date
from functools import partial
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from mcp_support import ADMIN as MCP_ADMIN, OPERATIVO as MCP_OPERATIVO, SEEDS, McpDbCase, mcp_actor
from sqlalchemy import create_engine, text, update
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ADMIN, AUDITOR, OPERATIVO, ServiceBase, actor, errors

from app.extensions import db
from app.models import EstadoNoConformidad, NoConformidad, Person, RoleEnum
from app.services.actor import Actor

E = EstadoNoConformidad
TERMINAL = (E.cerrada, E.cancelada)
DETECTED = date(2026, 10, 1)  # ``fecha_detectada`` of the seeded nonconformity
PLANNED, DONE, CHECKED, TODAY = (date(2026, 10, 20), date(2026, 10, 12),
                                 date(2026, 10, 15), date(2026, 10, 30))
DONE_BEFORE = "La fecha de realización no puede ser anterior a la fecha en que se detectó"
NC_NOT_OPEN = "cerrada o cancelada"
# No user id: attribution and audit rows stay valid when foreign keys are enforced.
NO_USER = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")


def actions():
    from app.services import corrective_actions

    return corrective_actions


def ncs():
    from app.services import nonconformities

    return nonconformities


def models():
    import app.models as module

    return module


class ModelTestCase(unittest.TestCase):
    def test_enums_are_stored_by_name_and_shown_in_spanish(self) -> None:
        m = models()
        self.assertEqual([("eficaz", "Eficaz"), ("no_eficaz", "No eficaz")],
                         [(x.name, x.value) for x in m.ResultadoVerificacion])
        self.assertEqual([("planificada", "Planificada"), ("realizada", "Realizada"),
                          ("verificada_eficaz", "Verificada eficaz"),
                          ("verificada_no_eficaz", "Verificada no eficaz")],
                         [(x.name, x.value) for x in m.EstadoAccionCorrectiva])

    def test_the_status_is_derived_and_never_stored(self) -> None:
        m = models()
        status, result = m.EstadoAccionCorrectiva, m.ResultadoVerificacion
        self.assertNotIn("estado", m.AccionCorrectiva.__table__.c)
        action = m.AccionCorrectiva()
        self.assertIs(status.planificada, action.estado)
        action.fecha_realizada = DONE
        self.assertIs(status.realizada, action.estado)
        for value, expected in ((result.eficaz, status.verificada_eficaz),
                                (result.no_eficaz, status.verificada_no_eficaz)):
            action.resultado_verificacion = value
            self.assertIs(expected, action.estado)


class ActionBase(ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.ana, self.eva = self.person("Ana"), self.person("Eva")
        self.nc = self.seed().id  # detected on DETECTED, starts ``abierta``

    def person(self, nombre: str, **values) -> int:
        result = db.session.execute(Person.__table__.insert().values(nombre=nombre, **values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def data(self, **values) -> dict:
        return {"no_conformidad_id": self.nc, "descripcion": "Reajustar el molde",
                "responsable_id": self.ana, "fecha_prevista": PLANNED} | values

    def add(self, who=None, **values):
        found = actions().create(db.session, who or actor(), self.data(**values))
        db.session.commit()
        return found

    def edit(self, action_id: int, who=None, **values):
        found = actions().update(db.session, who or actor(), action_id, values)
        db.session.commit()
        return found

    def check(self, action_id: int, resultado="eficaz", who=None, **values):
        arguments = {"resultado": resultado, "fecha": CHECKED, "verificador_id": self.eva,
                     "evidencia": "Tres lotes sin defectos"} | values
        found = actions().verify(db.session, who or actor(AUDITOR), action_id, **arguments)
        db.session.commit()
        return found

    def drop(self, action_id: int, who=None) -> None:
        actions().delete(db.session, who or actor(ADMIN), action_id)
        db.session.commit()

    def close(self, who=None, today=TODAY):
        closed = ncs().close(db.session, who or actor(AUDITOR), self.nc, today=today)
        db.session.commit()
        return closed

    def state(self) -> EstadoNoConformidad:
        db.session.expire_all()
        return db.session.get(NoConformidad, self.nc).estado

    def force(self, estado: EstadoNoConformidad) -> None:
        """Put the nonconformity in a state directly (core update, no audit row)."""
        db.session.execute(update(NoConformidad).where(NoConformidad.id == self.nc)
                           .values(estado=estado))
        db.session.commit()

    def rows(self, entity_type: str) -> list:
        return [row for row in self.audit_rows() if row.entity_type == entity_type]

    def refused(self, error: type, message: str, call, *args, **kwargs) -> None:
        """``call`` raises ``error`` with ``message`` in its text and writes nothing."""
        before = len(self.audit_rows())
        with self.assertRaises(error) as caught:
            call(*args, **kwargs)
        db.session.rollback()
        self.assertIn(message, caught.exception.message)
        self.assertEqual(before, len(self.audit_rows()))


class CreateTestCase(ActionBase):
    def test_every_role_adds_actions_and_each_one_is_audited(self) -> None:
        for role in RoleEnum:
            with self.subTest(role=role.name):
                added = self.add(actor(role, user_id=8))
                row = self.rows("acciones_correctivas")[-1]
                self.assertEqual((added.id, "create", None), (row.entity_id, row.action, row.before))
                self.assertEqual(
                    ("Reajustar el molde", self.ana, "2026-10-20", None, None),
                    tuple(row.after[key] for key in ("descripcion", "responsable_id",
                                                     "fecha_prevista", "fecha_realizada",
                                                     "resultado_verificacion")))
                self.assertEqual((8, 8), (added.created_by_id, added.updated_by_id))
        self.assertEqual(3, len(actions().list_(db.session, actor(), no_conformidad_id=self.nc)))

    def test_the_first_action_moves_an_open_nonconformity_to_planned(self) -> None:
        self.assertIs(E.abierta, self.state())
        self.add(actor(user_id=8))
        self.assertIs(E.accion_planificada, self.state())
        (row,) = self.rows("no_conformidades")
        self.assertEqual(("update", "Abierta", "Acción planificada", 8),
                         (row.action, row.before["estado"], row.after["estado"], row.actor_user_id))

    def test_input_is_refused_with_spanish_messages(self) -> None:
        inactive = self.person("Luis", activo=False)
        cases = [
            ({"descripcion": "x"},
             "Faltan campos obligatorios: fecha_prevista, no_conformidad_id, responsable_id."),
            (self.data(resultado_verificacion="eficaz"),
             "Campos no permitidos: resultado_verificacion."),
            (self.data(descripcion=" "), "El campo «descripcion» es obligatorio."),
            (self.data(descripcion="x" * 1001), "admite como máximo 1000 caracteres"),
            (self.data(responsable_id=None), "«responsable_id» debe ser un número entero"),
            (self.data(responsable_id=999), "no corresponde a ninguna persona"),
            (self.data(responsable_id=inactive), "está desactivada"),
            (self.data(no_conformidad_id=999),
             "El campo «no_conformidad_id» no corresponde a ninguna no conformidad."),
            (self.data(no_conformidad_id="1"), "«no_conformidad_id» debe ser un número entero"),
            (self.data(fecha_prevista="2026-10-20"),
             "«fecha_prevista» es obligatorio y debe ser una fecha"),
            (self.data(fecha_realizada=date(2026, 9, 30)), DONE_BEFORE),
            (["no", "es", "un", "objeto"], "Los datos deben ser un objeto con campos."),
        ]
        create = partial(actions().create, db.session, actor())
        for data, message in cases:
            with self.subTest(message=message):
                self.refused(errors().ValidationError, message, create, data)
        self.assertIs(E.abierta, self.state())

    def test_an_action_done_on_the_detection_day_goes_straight_to_verification(self) -> None:
        self.add(fecha_realizada=DETECTED)
        self.assertIs(E.en_verificacion, self.state())

    def test_closed_or_cancelled_nonconformities_take_no_actions(self) -> None:
        for estado in TERMINAL:
            with self.subTest(estado=estado.name):
                self.force(estado)
                self.refused(errors().ValidationError, NC_NOT_OPEN, actions().create,
                             db.session, actor(ADMIN), self.data())
                self.assertIs(estado, self.state())

    def test_scopes_limit_reads_and_writes(self) -> None:
        denied = errors().PermissionDenied
        self.refused(denied, "No tienes permiso", actions().create, db.session,
                     actor(ADMIN, scopes={"read"}), self.data())
        added = self.add(actor(channel="mcp"))
        self.refused(denied, "No tienes permiso", actions().get, db.session,
                     actor(scopes={"write"}), added.id)


class UpdateTestCase(ActionBase):
    def test_fields_change_with_an_audit_row_and_a_no_op_writes_nothing(self) -> None:
        added = self.add()
        self.edit(added.id, actor(user_id=9), descripcion="  Cambiar el molde ",
                  responsable_id=self.eva, fecha_prevista=date(2026, 10, 25))
        row = self.rows("acciones_correctivas")[-1]
        self.assertEqual(("update", "Cambiar el molde", self.eva, "2026-10-25", 9),
                         (row.action, row.after["descripcion"], row.after["responsable_id"],
                          row.after["fecha_prevista"], row.actor_user_id))
        count = len(self.audit_rows())
        self.edit(added.id, descripcion="Cambiar el molde", no_conformidad_id=self.nc)
        self.assertEqual(count, len(self.audit_rows()))

    def test_an_owner_deactivated_later_stays_but_cannot_be_newly_chosen(self) -> None:
        added = self.add()
        db.session.execute(update(Person).where(Person.id.in_([self.ana, self.eva]))
                           .values(activo=False))
        db.session.commit()
        self.edit(added.id, descripcion="Otra", responsable_id=self.ana)
        self.refused(errors().ValidationError, "está desactivada", actions().update,
                     db.session, actor(), added.id, {"responsable_id": self.eva})

    def test_an_action_never_moves_to_another_nonconformity(self) -> None:
        added = self.add()
        other = self.seed(descripcion="Otra").id
        self.refused(errors().ValidationError,
                     "Una acción correctiva no puede pasar a otra no conformidad.",
                     actions().update, db.session, actor(), added.id, {"no_conformidad_id": other})

    def test_the_done_date_cannot_precede_the_detection(self) -> None:
        added = self.add()
        self.refused(errors().ValidationError, DONE_BEFORE, actions().update, db.session,
                     actor(), added.id, {"fecha_realizada": date(2026, 9, 30)})
        self.edit(added.id, fecha_realizada=DETECTED)

    def test_a_verified_action_is_read_only(self) -> None:
        added = self.add(fecha_realizada=DONE)
        self.check(added.id)
        for values in ({"descripcion": "Otra"}, {"fecha_realizada": None}, {}):
            with self.subTest(values=values):
                self.refused(errors().ValidationError,
                             "Una acción correctiva verificada no se puede modificar.",
                             actions().update, db.session, actor(ADMIN), added.id, values)

    def test_edits_are_refused_once_the_nonconformity_is_closed_or_cancelled(self) -> None:
        added = self.add()
        for estado in TERMINAL:
            with self.subTest(estado=estado.name):
                self.force(estado)
                self.refused(errors().ValidationError, NC_NOT_OPEN, actions().update,
                             db.session, actor(ADMIN), added.id, {"descripcion": "Otra"})

    def test_a_missing_action_is_not_found(self) -> None:
        verify = partial(actions().verify, resultado="eficaz", fecha=CHECKED,
                         verificador_id=self.eva, evidencia="x")
        for call, args in ((actions().get, ()), (actions().update, ({},)),
                           (verify, ()), (actions().delete, ())):
            with self.subTest(call=call):
                self.refused(errors().NotFound, "Acción correctiva no encontrada.", call,
                             db.session, actor(ADMIN), 999, *args)


class VerifyTestCase(ActionBase):
    def test_administrators_and_auditors_verify_a_done_action(self) -> None:
        status, result = models().EstadoAccionCorrectiva, models().ResultadoVerificacion
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                added = self.add(fecha_realizada=DONE)
                verified = self.check(added.id, "eficaz", actor(role, user_id=8),
                                      evidencia="  Tres lotes sin defectos ")
                self.assertEqual(
                    (result.eficaz, CHECKED, self.eva, "Tres lotes sin defectos",
                     status.verificada_eficaz, 8),
                    (verified.resultado_verificacion, verified.fecha_verificacion,
                     verified.verificador_id, verified.evidencia_verificacion,
                     verified.estado, verified.updated_by_id))
                row = self.rows("acciones_correctivas")[-1]
                self.assertEqual(("update", None, "Eficaz", self.eva),
                                 (row.action, row.before["resultado_verificacion"],
                                  row.after["resultado_verificacion"], row.after["verificador_id"]))

    def test_operativo_and_restricted_tokens_cannot_verify(self) -> None:
        added = self.add(fecha_realizada=DONE)
        for who in (actor(OPERATIVO), actor(ADMIN, scopes={"read"})):
            with self.subTest(who=who):
                self.refused(errors().PermissionDenied, "No tienes permiso", self.check,
                             added.id, who=who)

    def test_only_a_done_action_not_yet_verified_can_be_verified(self) -> None:
        planned = self.add()
        self.refused(errors().ValidationError,
                     "Solo se puede verificar una acción correctiva realizada", self.check,
                     planned.id)
        done = self.add(fecha_realizada=DONE)
        self.check(done.id)
        self.refused(errors().ValidationError, "Esta acción correctiva ya está verificada.",
                     self.check, done.id, "no_eficaz")

    def test_the_owner_never_verifies_their_own_action(self) -> None:
        added = self.add(fecha_realizada=DONE)
        self.refused(errors().ValidationError,
                     "La persona que verifica la eficacia no puede ser la responsable de la acción.",
                     self.check, added.id, verificador_id=self.ana)
        # The rule is on people: the verifier may be the acting user's own person.
        db.session.execute(update(Person).where(Person.id == self.eva).values(user_id=7))
        db.session.commit()
        self.check(added.id, who=actor(AUDITOR, user_id=7))

    def test_verification_input_is_refused_with_spanish_messages(self) -> None:
        added = self.add(fecha_realizada=DONE)
        inactive = self.person("Luis", activo=False)
        cases = [
            ({"resultado": "Eficaz"}, "El valor del campo «resultado_verificacion» no es válido."),
            ({"resultado": None}, "«resultado_verificacion» no es válido"),
            ({"fecha": "2026-10-15"}, "«fecha_verificacion» es obligatorio y debe ser una fecha"),
            ({"fecha": date(2026, 10, 11)},
             "La fecha de verificación no puede ser anterior a la fecha de realización."),
            ({"verificador_id": None}, "«verificador_id» debe ser un número entero"),
            ({"verificador_id": 999}, "no corresponde a ninguna persona"),
            ({"verificador_id": inactive}, "está desactivada"),
            ({"evidencia": "  "}, "El campo «evidencia_verificacion» es obligatorio."),
        ]
        for values, message in cases:
            with self.subTest(values=values):
                self.refused(errors().ValidationError, message, self.check, added.id, **values)
        self.assertIs(E.en_verificacion, self.state())

    def test_verification_is_refused_once_the_nonconformity_is_closed_or_cancelled(self) -> None:
        added = self.add(fecha_realizada=DONE)
        for estado in TERMINAL:
            with self.subTest(estado=estado.name):
                self.force(estado)
                self.refused(errors().ValidationError, NC_NOT_OPEN, self.check, added.id)


class DeleteTestCase(ActionBase):
    def test_only_administrators_delete_and_never_through_the_agent_channel(self) -> None:
        added = self.add()
        for who in (actor(OPERATIVO), actor(AUDITOR), actor(ADMIN, channel="mcp")):
            with self.subTest(who=who):
                self.refused(errors().PermissionDenied, "No tienes permiso", actions().delete,
                             db.session, who, added.id)
        self.drop(added.id, actor(ADMIN, user_id=9))
        row = self.rows("acciones_correctivas")[-1]
        self.assertEqual(("delete", "Reajustar el molde", None, 9),
                         (row.action, row.before["descripcion"], row.after, row.actor_user_id))
        self.assertIsNone(db.session.get(models().AccionCorrectiva, added.id))

    def test_a_verified_action_is_deleted_only_while_the_nonconformity_is_open(self) -> None:
        first, second = self.add(fecha_realizada=DONE), self.add(fecha_realizada=DONE)
        self.check(first.id)
        self.check(second.id)
        self.drop(first.id)
        self.force(E.cerrada)
        self.refused(errors().ValidationError, NC_NOT_OPEN, actions().delete, db.session,
                     actor(ADMIN), second.id)

    def test_deleting_a_nonconformity_deletes_its_actions_with_audit_rows(self) -> None:
        first, second = self.add(), self.add(descripcion="Formar al operario")
        ncs().delete(db.session, actor(ADMIN), self.nc)
        db.session.commit()
        deleted = [(r.entity_type, r.entity_id) for r in self.audit_rows() if r.action == "delete"]
        self.assertEqual([("acciones_correctivas", first.id), ("acciones_correctivas", second.id),
                          ("no_conformidades", self.nc)], deleted)
        self.assertEqual(0, db.session.query(models().AccionCorrectiva).count())


class StateTestCase(ActionBase):
    def test_the_loop_from_detection_to_closing(self) -> None:
        """open -> first action -> all done -> not effective -> new action -> effective -> close."""
        first = self.add()
        self.assertIs(E.accion_planificada, self.state())
        self.edit(first.id, fecha_realizada=DONE)
        self.assertIs(E.en_verificacion, self.state())
        self.check(first.id, "no_eficaz")
        self.assertIs(E.accion_planificada, self.state())
        self.refused(errors().ValidationError, "registra una nueva acción correctiva", self.close)
        second = self.add(descripcion="Sustituir el molde")
        self.assertIs(E.accion_planificada, self.state())
        self.edit(second.id, fecha_realizada=date(2026, 10, 20))
        self.assertIs(E.en_verificacion, self.state())
        self.check(second.id, "eficaz", fecha=date(2026, 10, 25))
        self.assertIs(E.en_verificacion, self.state())  # closing is an explicit act
        closed = self.close(actor(AUDITOR, user_id=8))
        self.assertEqual((E.cerrada, TODAY, 8), (closed.estado, closed.fecha_cierre,
                                                 closed.updated_by_id))
        self.assertEqual(
            [("Abierta", "Acción planificada"), ("Acción planificada", "En verificación"),
             ("En verificación", "Acción planificada"), ("Acción planificada", "En verificación"),
             ("En verificación", "Cerrada")],
            [(row.before["estado"], row.after["estado"]) for row in self.rows("no_conformidades")])
        self.assertEqual("2026-10-30", self.rows("no_conformidades")[-1].after["fecha_cierre"])

    def test_every_action_must_be_done_and_clearing_a_date_moves_back(self) -> None:
        first, second = self.add(), self.add()
        self.edit(first.id, fecha_realizada=DONE)
        self.assertIs(E.accion_planificada, self.state())
        self.edit(second.id, fecha_realizada=DONE)
        self.assertIs(E.en_verificacion, self.state())
        self.edit(second.id, fecha_realizada=None)
        self.assertIs(E.accion_planificada, self.state())

    def test_a_new_action_takes_a_nonconformity_in_verification_back_to_planned(self) -> None:
        self.add(fecha_realizada=DONE)
        self.assertIs(E.en_verificacion, self.state())
        self.add()
        self.assertIs(E.accion_planificada, self.state())

    def test_deleting_actions_recomputes_the_state(self) -> None:
        done, pending = self.add(fecha_realizada=DONE), self.add()
        self.assertIs(E.accion_planificada, self.state())
        self.drop(pending.id)
        self.assertIs(E.en_verificacion, self.state())
        self.drop(done.id)
        self.assertIs(E.abierta, self.state())

    def test_an_ineffective_latest_action_asks_for_a_new_one(self) -> None:
        first, second = self.add(fecha_realizada=DONE), self.add(fecha_realizada=DONE)
        self.check(second.id, "no_eficaz")
        self.assertIs(E.accion_planificada, self.state())
        self.check(first.id, "eficaz")
        self.assertIs(E.accion_planificada, self.state())
        third = self.add(fecha_realizada=DONE)
        self.assertIs(E.en_verificacion, self.state())
        self.check(third.id, "eficaz")
        self.assertIs(E.cerrada, self.close().estado)

    def test_an_ineffective_action_followed_by_a_later_one_stays_as_evidence(self) -> None:
        first, second = self.add(fecha_realizada=DONE), self.add(fecha_realizada=DONE)
        self.check(first.id, "no_eficaz")
        self.assertIs(E.en_verificacion, self.state())  # the later action answers it
        self.check(second.id, "eficaz")
        self.assertIs(E.cerrada, self.close().estado)

    def test_the_state_never_leaves_a_terminal_state(self) -> None:
        self.add()
        for estado in TERMINAL:
            with self.subTest(estado=estado.name):
                self.force(estado)
                ncs().sync_state(db.session, actor(), db.session.get(NoConformidad, self.nc))
                db.session.commit()
                self.assertIs(estado, self.state())

    def test_reopening_lands_on_the_state_the_actions_call_for(self) -> None:
        added = self.add(fecha_realizada=DONE)
        self.check(added.id)
        self.close()
        reopened = ncs().reopen(db.session, actor(ADMIN), self.nc)
        db.session.commit()
        self.assertEqual((E.en_verificacion, None), (reopened.estado, reopened.fecha_cierre))


class CloseTestCase(ActionBase):
    def test_only_administrators_and_auditors_close(self) -> None:
        added = self.add(fecha_realizada=DONE)
        self.check(added.id)
        for who in (actor(OPERATIVO), actor(AUDITOR, scopes={"read"})):
            with self.subTest(who=who):
                self.refused(errors().PermissionDenied, "No tienes permiso", self.close, who)
        nc = db.session.get(NoConformidad, self.nc)
        self.assertEqual([True, True, False],
                         [ncs().may_close(actor(role), nc) for role in (ADMIN, AUDITOR, OPERATIVO)])
        self.close(actor(ADMIN))
        self.assertFalse(ncs().may_close(actor(ADMIN), nc))
        self.refused(errors().ValidationError, "La no conformidad ya está cerrada o cancelada.",
                     self.close)

    def test_closing_says_what_is_missing(self) -> None:
        prefix = "No se puede cerrar la no conformidad: "
        self.refused(errors().ValidationError,
                     prefix + "no tiene ninguna acción correctiva.", self.close)
        planned, done = self.add(), self.add(fecha_realizada=DONE)
        self.refused(errors().ValidationError,
                     prefix + "1 acción sin realizar; 1 acción pendiente de verificar.", self.close)
        self.edit(planned.id, fecha_realizada=DONE)
        self.refused(errors().ValidationError, "2 acciones pendientes de verificar", self.close)
        self.check(planned.id, "eficaz")
        self.check(done.id, "no_eficaz")
        self.refused(errors().ValidationError,
                     "la última acción correctiva no fue eficaz; registra una nueva acción "
                     "correctiva", self.close)

    def test_closing_and_cancelling_take_the_date_from_the_caller(self) -> None:
        with self.assertRaises(TypeError):
            ncs().close(db.session, actor(ADMIN), self.nc)
        with self.assertRaises(TypeError):
            ncs().cancel(db.session, actor(ADMIN), self.nc, "Duplicada")


class PeopleTestCase(ActionBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())

    def test_an_owner_or_a_verifier_cannot_be_deleted(self) -> None:
        from app.services import people

        self.enforce_foreign_keys()
        added = self.add(NO_USER, fecha_realizada=DONE)
        self.check(added.id, who=NO_USER)
        for person in (self.ana, self.eva):
            with self.subTest(person=person):
                self.refused(errors().Conflict, people.STILL_REFERENCED, people.delete,
                             db.session, NO_USER, person)
                # As if the reference were committed after the explicit check ran.
                with patch.object(people, "_is_referenced", return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        people.delete(db.session, NO_USER, person)
                self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                db.session.rollback()
        self.drop(added.id, NO_USER)
        for person in (self.ana, self.eva):
            people.delete(db.session, NO_USER, person)
        db.session.commit()
        self.assertEqual(0, db.session.query(Person).count())


class ListTestCase(ActionBase):
    def test_lists_filter_by_nonconformity_owner_and_status(self) -> None:
        other = self.seed(descripcion="Otra").id
        planned = self.add()
        done = self.add(responsable_id=self.eva, fecha_realizada=DONE)
        effective = self.add(fecha_realizada=DONE)
        self.check(effective.id)
        ineffective = self.add(no_conformidad_id=other, fecha_realizada=DONE)
        self.check(ineffective.id, "no_eficaz")
        listed = partial(actions().list_, db.session, actor())
        self.assertEqual([planned, done, effective, ineffective], listed())
        self.assertEqual([ineffective], listed(no_conformidad_id=other))
        self.assertEqual([done], listed(responsable_id=self.eva))
        status = models().EstadoAccionCorrectiva
        for estado, expected in (("planificada", planned), (status.realizada, done),
                                 ("verificada_eficaz", effective),
                                 ("verificada_no_eficaz", ineffective)):
            with self.subTest(estado=estado):
                self.assertEqual([expected], listed(estado=estado))
        self.assertEqual(([done], 1), actions().list_page(db.session, actor(), estado="realizada",
                                                          page=1, per_page=1))
        self.assertEqual([], listed(no_conformidad_id=2**40))
        self.refused(errors().ValidationError, "El estado no es válido.", listed,
                     estado="Planificada")


class McpTestCase(McpDbCase):
    async def test_actions_round_trip_with_their_derived_status(self) -> None:
        nc_id, owner = self.seed("no_conformidades"), self.seed("personas")
        verifier = self.seed("personas", nombre="Eva")
        agent = mcp_actor(MCP_OPERATIVO)
        created = await self.call(agent, "qms_create", {"module": "acciones_correctivas", "data":
                                  SEEDS["acciones_correctivas"] | {"no_conformidad_id": nc_id,
                                                                   "responsable_id": owner}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("Planificada", None, "2026-10-15"), (
            record["estado"], record["resultado_verificacion"], record["fecha_prevista"]))
        nc = await self.call(agent, "qms_get", {"module": "no_conformidades", "id": nc_id})
        self.assertEqual("Acción planificada", nc.structured_content["estado"])
        done = await self.call(agent, "qms_update", {"module": "acciones_correctivas",
                               "id": record["id"], "data": {"fecha_realizada": "2026-10-12"}})
        self.assertEqual("Realizada", done.structured_content["estado"])
        refused = await self.call(mcp_actor(MCP_ADMIN), "qms_update", {
            "module": "acciones_correctivas", "id": record["id"],
            "data": {"resultado_verificacion": "eficaz", "verificador_id": verifier}})
        self.assertTrue(refused.is_error)
        self.assertIn("Campos no permitidos", refused.content[0].text)
        actions().verify(db.session, self.cli(), record["id"], resultado="eficaz",
                         fecha=date(2026, 10, 15), verificador_id=verifier, evidencia="Sin fallos")
        db.session.commit()
        listed = await self.call(agent, "qms_list", {"module": "acciones_correctivas", "filters": {
            "estado": "verificada_eficaz", "no_conformidad_id": nc_id}})
        self.assertFalse(listed.is_error, listed.content)
        self.assertEqual(["Verificada eficaz"],
                         [item["estado"] for item in listed.structured_content["items"]])


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class ConcurrentCloseTestCase(unittest.TestCase):
    """Closing and adding an action race on PostgreSQL; the nonconformity's row lock decides."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        with self.app.app_context():
            db.create_all()
            ana, eva = Person(nombre="Ana"), Person(nombre="Eva")
            db.session.add_all([ana, eva])
            db.session.flush()
            nc = ncs().create(db.session, NO_USER, {"descripcion": "Fallo",
                                                    "fecha_detectada": DETECTED})
            self.data = {"no_conformidad_id": nc.id, "descripcion": "Reajustar",
                         "responsable_id": ana.id, "fecha_prevista": PLANNED}
            added = actions().create(db.session, NO_USER, self.data | {"fecha_realizada": DONE})
            actions().verify(db.session, NO_USER, added.id, resultado="eficaz", fecha=CHECKED,
                             verificador_id=eva.id, evidencia="Sin fallos")
            db.session.commit()
            self.nc_id = nc.id

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()

    def test_an_action_added_while_closing_waits_and_is_refused(self) -> None:
        flushed, release, outcomes = threading.Event(), threading.Event(), {}

        def run(name: str, write, hold: bool) -> None:
            with self.app.app_context():
                try:
                    write()
                    if hold:
                        flushed.set()
                        release.wait(10)
                    db.session.commit()
                    outcomes[name] = "done"
                except errors().ValidationError as error:
                    outcomes[name] = error.message
                except Exception as error:  # surfaced by the assertion below
                    outcomes[name] = repr(error)
                finally:
                    flushed.set()
                    db.session.rollback()
                    db.session.remove()

        def close() -> None:
            ncs().close(db.session, NO_USER, self.nc_id, today=TODAY)

        def add() -> None:
            actions().create(db.session, NO_USER, self.data)

        first = threading.Thread(target=run, args=("close", close, True))
        second = threading.Thread(target=run, args=("add", add, False))
        try:
            first.start()
            self.assertTrue(flushed.wait(10))
            second.start()
            second.join(0.5)
            waited = second.is_alive()
        finally:
            release.set()
            first.join(10)
            if second.ident is not None:
                second.join(10)
        self.assertEqual("done", outcomes["close"])
        self.assertIn(NC_NOT_OPEN, outcomes["add"])
        self.assertTrue(waited, "adding an action must wait for the closing row lock")
        with self.app.app_context():
            self.assertEqual(1, db.session.query(models().AccionCorrectiva).count())
            self.assertIs(E.cerrada, db.session.get(NoConformidad, self.nc_id).estado)


if __name__ == "__main__":
    unittest.main()
