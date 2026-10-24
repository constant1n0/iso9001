"""The tasks that carry out a change to the QMS (CP-2, decision CP4 of ``change-planning``).

A task belongs to one change, which it never leaves, and is created, changed
and deleted only while the change is ``propuesto`` or ``aprobado`` (the change
read ``FOR SHARE``). It has a description, an owner, a deadline, a state, and a
completion date exactly when it is completed, never in the future. A task is
overdue when pending or in progress after its deadline while its change is
still open. Runs with the audit flush guard.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from mcp_support import ADMIN, AUDITOR, OPERATIVO, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors
from test_qms_changes import CLI, TASK, TODAY, TOMORROW, YESTERDAY, Helpers, qc

from app.extensions import db
from app.models import CambioSGC, EstadoTareaCambio, TareaCambio


class TaskWriteTestCase(Helpers, ServiceBase):
    def test_a_task_starts_pending_with_stamps_and_audit_and_leaves_the_change_alone(
            self) -> None:
        change = self.change()
        owner = self.person("Eva")
        created = qc().tasks.create(db.session, actor(AUDITOR), TASK | {
            "cambio_id": change.id, "responsable_id": owner}, today=TODAY)
        db.session.commit()
        self.assertEqual((change.id, owner, EstadoTareaCambio.pendiente, None, 7),
                         (created.cambio_id, created.responsable_id, created.estado,
                          created.fecha_completada, created.created_by_id))
        (row,) = self.rows("tareas_cambio")
        self.assertEqual(("create", "Pendiente", change.id),
                         (row.action, row.after["estado"], row.after["cambio_id"]))
        # Written on its own row: the change gets no audit row of its own.
        self.assertEqual(["create"], [r.action for r in self.rows("cambios_sgc")])
        self.assertEqual([created.id], self.ids(db.session.get(CambioSGC, change.id).tareas))
        self.assertIs(created, qc().tasks.get(db.session, actor(OPERATIVO), created.id))

    def test_required_fields_and_bad_values_are_refused(self) -> None:
        change = self.change()
        valid = TASK | {"cambio_id": change.id, "responsable_id": self.person()}
        for missing in ("cambio_id", "descripcion", "responsable_id", "fecha_limite"):
            with self.subTest(missing=missing):
                data = {key: value for key, value in valid.items() if key != missing}
                self.assertIn(missing, self.refused(errors().ValidationError, qc().tasks.create,
                                                    CLI, data, today=TODAY))
        for data in ({"cambio_id": "1"}, {"descripcion": " "}, {"fecha_limite": "2026-11-30"},
                     {"estado": "Pendiente"}, {"estado": "abierta"}, {"responsable_id": 999},
                     {"fecha_completada": "2026-10-01"}, {"id": 3}, {"cambio": change.id}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, qc().tasks.create, CLI, valid | data,
                             today=TODAY)
        self.assertEqual(qc().UNKNOWN_CHANGE, self.refused(
            errors().ValidationError, qc().tasks.create, CLI, valid | {"cambio_id": 999},
            today=TODAY))
        self.assertEqual(0, db.session.query(TareaCambio).count())
        with self.assertRaises(ValueError):
            qc().tasks.create(db.session, CLI, valid, today=None)

    def test_a_task_never_moves_to_another_change(self) -> None:
        task = self.task(self.change().id)
        other = self.change(titulo="Otro")
        self.assertEqual(qc().TASK_CHANGE_FIXED, self.refused(
            errors().ValidationError, qc().tasks.update, CLI, task.id,
            {"cambio_id": other.id}, today=TODAY))
        kept = qc().tasks.update(db.session, CLI, task.id, {"cambio_id": task.cambio_id,
                                                            "resultado": "Hecho"}, today=TODAY)
        self.assertEqual("Hecho", kept.resultado)
        self.refused(errors().NotFound, qc().tasks.update, CLI, 999, {"resultado": "x"},
                     today=TODAY)
        self.assertIn("Campos no permitidos", self.refused(
            errors().ValidationError, qc().tasks.update, CLI, 999, {"cambio": 1}, today=TODAY))

    def test_a_completed_task_and_only_one_has_its_completion_date(self) -> None:
        change = self.change()
        task = self.task(change.id)
        for data, expected in (
                ({"estado": "completada"}, qc().COMPLETION_REQUIRED),
                ({"fecha_completada": TODAY}, qc().COMPLETION_ONLY_WHEN_DONE),
                ({"estado": "en_curso", "fecha_completada": TODAY},
                 qc().COMPLETION_ONLY_WHEN_DONE),
                ({"estado": "completada", "fecha_completada": TOMORROW},
                 qc().COMPLETION_IN_FUTURE)):
            with self.subTest(data=data):
                self.assertEqual(expected, self.refused(
                    errors().ValidationError, qc().tasks.update, CLI, task.id, data,
                    today=TODAY))
                self.assertEqual(expected, self.refused(
                    errors().ValidationError, qc().tasks.create, CLI, TASK | data | {
                        "cambio_id": change.id, "responsable_id": task.responsable_id},
                    today=TODAY))
        done = self.complete(task.id, YESTERDAY)
        self.assertEqual((EstadoTareaCambio.completada, YESTERDAY),
                         (done.estado, done.fecha_completada))
        self.assertEqual(qc().COMPLETION_ONLY_WHEN_DONE, self.refused(
            errors().ValidationError, qc().tasks.update, CLI, task.id, {"estado": "en_curso"},
            today=TODAY))
        reopened = qc().tasks.update(db.session, CLI, task.id, {
            "estado": "en_curso", "fecha_completada": None}, today=TODAY)
        self.assertEqual((EstadoTareaCambio.en_curso, None),
                         (reopened.estado, reopened.fecha_completada))

    def test_tasks_are_written_only_while_the_change_is_open(self) -> None:
        for make in (self.change, self.approved):
            change = make()
            with self.subTest(open=change.estado.name):
                task = self.task(change.id)
                qc().tasks.update(db.session, CLI, task.id, {"resultado": "Sí"}, today=TODAY)
                qc().tasks.delete(db.session, actor(ADMIN), task.id)
                db.session.commit()
        def cancelled_with_a_task() -> CambioSGC:
            change = self.change(titulo="Cancelado")
            self.task(change.id)
            qc().cancel(db.session, self.admin(), change.id, motivo="No", today=TODAY)
            db.session.commit()
            return change

        for make in (self.implemented, self.reviewed, cancelled_with_a_task):
            change = make()
            (task,) = change.tareas
            expected = qc().TASKS_CLOSED.format(estado=change.estado.value)
            with self.subTest(closed=change.estado.name):
                for call, args in (
                        (qc().tasks.create, (TASK | {"cambio_id": change.id,
                                                     "responsable_id": task.responsable_id},)),
                        (qc().tasks.update, (task.id, {"resultado": "Otro"}))):
                    self.assertEqual(expected, self.refused(errors().ValidationError, call, CLI,
                                                            *args, today=TODAY))
                self.assertEqual(expected, self.refused(errors().ValidationError,
                                                        qc().tasks.delete, actor(ADMIN),
                                                        task.id))
                # Re-posting what it holds writes nothing and is no error.
                rows = len(self.rows("tareas_cambio"))
                qc().tasks.update(db.session, CLI, task.id,
                                  {"descripcion": task.descripcion}, today=TODAY)
                self.assertEqual(rows, len(self.rows("tareas_cambio")))

    def test_the_change_is_read_for_share_and_the_task_locked(self) -> None:
        change = self.change()
        with patch.object(qc(), "_lock", wraps=qc()._lock) as lock:
            task = self.task(change.id)
            qc().tasks.update(db.session, CLI, task.id, {"resultado": "Hecho"}, today=TODAY)
            qc().tasks.delete(db.session, actor(ADMIN), task.id)
        self.assertEqual(
            [(CambioSGC, change.id, True), (CambioSGC, change.id, True),
             (TareaCambio, task.id, False), (CambioSGC, change.id, True)],
            [(call.args[1], call.args[2], call.kwargs["read"]) for call in lock.call_args_list])

    def test_every_role_reads_admins_and_auditors_write_admins_delete(self) -> None:
        change = self.change()
        task = self.task(change.id)
        for role in (ADMIN, AUDITOR, OPERATIVO):
            with self.subTest(read=role.name):
                self.assertEqual([task.id], self.ids(qc().tasks.list_(db.session, actor(role),
                                                                      today=TODAY)))
        data = TASK | {"cambio_id": change.id, "responsable_id": task.responsable_id}
        for who, call, args, kwargs in (
                (actor(OPERATIVO), qc().tasks.create, (data,), {"today": TODAY}),
                (actor(OPERATIVO), qc().tasks.update, (task.id, {"resultado": "x"}),
                 {"today": TODAY}),
                (actor(AUDITOR), qc().tasks.delete, (task.id,), {}),
                (mcp_actor(ADMIN), qc().tasks.delete, (task.id,), {})):
            with self.subTest(denied=call.__name__, role=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, call, who, *args, **kwargs)
        qc().tasks.create(db.session, mcp_actor(AUDITOR), data, today=TODAY)
        qc().tasks.update(db.session, mcp_actor(AUDITOR), task.id, {"resultado": "MCP"},
                          today=TODAY)
        db.session.commit()
        self.assertEqual(2, db.session.query(TareaCambio).count())


class TaskFilterTestCase(Helpers, ServiceBase):
    def test_filters_combine_and_overdue_needs_an_open_change(self) -> None:
        ana, eva = self.person("Ana"), self.person("Eva")
        change, other = self.change(), self.approved(titulo="Otro")
        (approved_task,) = other.tareas
        late = self.task(change.id, responsable_id=ana, fecha_limite=YESTERDAY)
        today = self.task(change.id, responsable_id=eva, fecha_limite=TODAY,
                          estado="en_curso")
        done = self.task(change.id, responsable_id=eva, fecha_limite=date(2026, 10, 1),
                         estado="completada", fecha_completada=date(2026, 10, 1))
        gone = self.change(titulo="Cancelado")
        stale = self.task(gone.id, responsable_id=ana, fecha_limite=YESTERDAY)
        qc().cancel(db.session, self.admin(), gone.id, motivo="No", today=TODAY)
        db.session.commit()
        who = actor(OPERATIVO)

        def found(**filters):
            return self.ids(qc().tasks.list_(db.session, who, today=TODAY, **filters))

        # The soonest deadline first.
        self.assertEqual([done.id, late.id, stale.id, today.id, approved_task.id], found())
        self.assertEqual([done.id, late.id, today.id], found(cambio_id=change.id))
        self.assertEqual([approved_task.id], found(cambio_id=other.id))
        self.assertEqual([today.id], found(estado="en_curso"))
        self.assertEqual([done.id], found(estado=EstadoTareaCambio.completada))
        self.assertEqual([late.id, stale.id], found(responsable_id=ana))
        self.assertEqual([late.id], found(vencida=True))
        self.assertEqual([done.id, stale.id, today.id, approved_task.id], found(vencida=False))
        self.assertEqual([], found(cambio_id=999))
        self.assertEqual([], found(responsable_id="1"))
        for filters in ({"estado": "Pendiente"}, {"vencida": "si"}):
            with self.subTest(filters=filters):
                self.refused(errors().ValidationError, qc().tasks.list_, who, today=TODAY,
                             **filters)
        with self.assertRaises(ValueError):
            qc().tasks.list_(db.session, who, vencida=True, today=None)


if __name__ == "__main__":
    unittest.main()
