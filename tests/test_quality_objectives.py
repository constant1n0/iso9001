"""Quality objectives and their measurements (QO-1: decisions QO1 to QO4, ISO 9001 clause 6.2).

An objective has an owner, an indicator, a target with a direction (``≥`` or
``≤``), a start date, a deadline and a measurement frequency. Measurements are
recorded only while the objective is ``activo``, on a date inside its period
and never in the future; the target and its direction are fixed once the
objective has measurements, and such an objective cannot be deleted.
``summary`` and ``off_track`` compute whether the target is met, the trend and
whether a measurement is overdue (QO3).

The SQLite cases run with the audit flush guard (``ServiceBase``). The foreign
keys are exercised on SQLite with ``PRAGMA foreign_keys=ON`` and on PostgreSQL
when ``TEST_POSTGRES_URI`` is set (CI provides it).
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

from mcp_support import McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import AuditLog, Person, ProcesoOperacion, RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
TODAY = date(2026, 10, 11)
OBJECTIVE = {"titulo": "Entregas a tiempo", "indicador": "% de pedidos entregados a tiempo",
             "unidad": "%", "meta": 95, "sentido": "mayor_igual",
             "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2026, 12, 31),
             "frecuencia": "mensual"}


def qo():
    from app.services import quality_objectives

    return quality_objectives


def models():
    import app.models as module

    return module


class Helpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        """Core insert: these tests must not depend on the people write API."""
        result = db.session.execute(Person.__table__.insert().values(nombre=nombre, activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def process(self, nombre: str = "Compras") -> int:
        result = db.session.execute(ProcesoOperacion.__table__.insert().values(proceso=nombre))
        db.session.commit()
        return result.inserted_primary_key[0]

    def objective(self, who: Actor = CLI, **overrides):
        if "responsable_id" not in overrides:
            overrides["responsable_id"] = self.person()
        found = qo().objectives.create(db.session, who, OBJECTIVE | overrides)
        db.session.commit()
        return found

    def measure(self, objective_id: int, fecha: date, valor, who: Actor = CLI, **extra):
        found = qo().measurements.create(
            db.session, who, {"objetivo_id": objective_id, "fecha": fecha, "valor": valor} | extra,
            today=TODAY)
        db.session.commit()
        return found

    def edit(self, register: str, record_id: int, data: dict, who: Actor = CLI, **kwargs):
        found = getattr(qo(), register).update(db.session, who, record_id, data, **kwargs)
        db.session.commit()
        return found

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


class ObjectiveWriteTestCase(Helpers, ServiceBase):
    def test_create_stores_every_field_with_defaults_stamps_and_audit(self) -> None:
        process_id = self.process()
        created = self.objective(actor(AUDITOR), proceso_id=process_id, valor_inicial="80.5",
                                 descripcion="Plazo pactado", recursos="ERP",
                                 evaluacion="Revisión mensual del indicador")
        m = models()
        self.assertEqual(
            (Decimal("95.0000"), m.SentidoObjetivo.mayor_igual, Decimal("80.5000"), 2026,
             m.FrecuenciaMedicion.mensual, m.EstadoObjetivo.activo, process_id),
            (created.meta, created.sentido, created.valor_inicial, created.anio,
             created.frecuencia, created.estado, created.proceso_id))
        self.assertEqual((7, 7), (created.created_by_id, created.updated_by_id))
        (row,) = self.rows("objetivos_calidad")
        self.assertEqual(("create", created.id), (row.action, row.entity_id))
        self.assertEqual(("≥", "95.0000", "Activo", "Mensual"),
                         tuple(row.after[k] for k in ("sentido", "meta", "estado", "frecuencia")))
        self.assertEqual(["≥", "≤"], [s.value for s in m.SentidoObjetivo])

    def test_numbers_take_json_numbers_or_decimal_text_rounded_to_four_places(self) -> None:
        for given, stored in ((95.123456, "95.1235"), ("12.5", "12.5000"), (7, "7.0000"),
                              (Decimal("-3.25"), "-3.2500")):
            with self.subTest(given=given):
                self.assertEqual(Decimal(stored), self.objective(meta=given).meta)
        self.assertEqual(2027, self.objective(anio=2027).anio)
        self.assertIsNone(self.objective(valor_inicial="  ").valor_inicial)

    def test_invalid_or_missing_values_are_refused_and_nothing_is_written(self) -> None:
        owner, inactive = self.person(), self.person("Eva", activo=False)
        valid = OBJECTIVE | {"responsable_id": owner}
        create = qo().objectives.create
        for key in ("titulo", "indicador", "meta", "sentido", "responsable_id", "fecha_inicio",
                    "fecha_limite", "frecuencia"):
            with self.subTest(missing=key):
                data = {k: v for k, v in valid.items() if k != key}
                self.assertIn(key, self.refused(errors().ValidationError, create, CLI, data))
        cases = {
            "meta": ("mucho", True, None, float("nan"), 10 ** 8, "1e8"),
            "valor_inicial": ([], "-100000000"),
            "sentido": ("≥", "mayor"),
            "frecuencia": ("semanal", "Mensual"),
            "estado": ("Activo", "cerrado"),
            "anio": (1999, 2101, "2026"),
            "titulo": ("x" * 201, " "),
            "unidad": ("x" * 31,),
            "proceso_id": (999, 2 ** 40),
            "responsable_id": (999, inactive, None),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.refused(errors().ValidationError, create, CLI, valid | {key: value})
        message = self.refused(errors().ValidationError, create, CLI,
                               valid | {"fecha_limite": date(2025, 12, 31)})
        self.assertEqual(qo().DEADLINE_BEFORE_START, message)
        self.assertIn("Campos no permitidos",
                      self.refused(errors().ValidationError, create, CLI, valid | {"id": 3}))
        self.assertEqual(0, self.count(models().ObjetivoCalidad))

    def test_update_writes_changed_fields_only_and_a_no_op_writes_nothing(self) -> None:
        found = self.objective()
        self.edit("objectives", found.id, {"titulo": "Entregas puntuales", "meta": "95"})
        row = self.rows("objetivos_calidad")[-1]
        changed = {k: v for k, v in row.after.items() if k not in ("updated_at", "updated_by_id")}
        self.assertEqual(("update", {"titulo": "Entregas puntuales"}), (row.action, changed))
        self.edit("objectives", found.id, {"meta": 95.0, "estado": "activo"})
        self.assertEqual(2, len(self.rows("objetivos_calidad")))
        message = self.refused(errors().ValidationError, qo().objectives.update, CLI, found.id,
                               {"fecha_inicio": date(2027, 1, 1)})
        self.assertEqual(qo().DEADLINE_BEFORE_START, message)
        self.refused(errors().NotFound, qo().objectives.update, CLI, 999, {"titulo": "X"})

    def test_target_and_direction_are_fixed_once_the_objective_is_measured(self) -> None:
        found = self.objective()
        self.edit("objectives", found.id, {"meta": 96, "sentido": "menor_igual"})
        self.edit("objectives", found.id, {"sentido": "mayor_igual"})
        self.measure(found.id, date(2026, 1, 31), 97)
        for change in ({"meta": 90}, {"sentido": "menor_igual"}):
            with self.subTest(change=change):
                message = self.refused(errors().ValidationError, qo().objectives.update, CLI,
                                       found.id, change)
                self.assertEqual(qo().TARGET_FIXED, message)
        self.edit("objectives", found.id,
                  {"meta": "96.0000", "titulo": "Otro", "estado": "alcanzado"})
        self.assertEqual((Decimal("96.0000"), "Otro"), (found.meta, found.titulo))

    def test_the_period_must_keep_its_measurements_inside(self) -> None:
        found = self.objective()
        self.measure(found.id, date(2026, 3, 31), 97)
        for change in ({"fecha_inicio": date(2026, 4, 1)}, {"fecha_limite": date(2026, 3, 30)}):
            with self.subTest(change=change):
                message = self.refused(errors().ValidationError, qo().objectives.update, CLI,
                                       found.id, change)
                self.assertIn("31/03/2026", message)
        self.edit("objectives", found.id, {"fecha_inicio": date(2026, 3, 31),
                                           "fecha_limite": date(2026, 3, 31)})

    def test_a_deactivated_owner_stays_but_cannot_be_newly_chosen(self) -> None:
        found = self.objective()
        table = Person.__table__
        db.session.execute(table.update().where(table.c.id == found.responsable_id)
                           .values(activo=False))
        db.session.commit()
        self.edit("objectives", found.id, {"titulo": "Sigue editable"})
        other = self.person("Eva", activo=False)
        message = self.refused(errors().ValidationError, qo().objectives.update, CLI, found.id,
                               {"responsable_id": other})
        self.assertIn("desactivada", message)

    def test_delete_keeps_a_snapshot_and_refuses_a_measured_objective(self) -> None:
        free, measured = self.objective(), self.objective(titulo="Reclamaciones")
        self.measure(measured.id, date(2026, 2, 1), 90)
        qo().objectives.delete(db.session, CLI, free.id)
        db.session.commit()
        row = self.rows("objetivos_calidad")[-1]
        self.assertEqual(("delete", free.id, "Entregas a tiempo"),
                         (row.action, row.entity_id, row.before["titulo"]))
        message = self.refused(errors().Conflict, qo().objectives.delete, CLI, measured.id)
        self.assertEqual(qo().HAS_MEASUREMENTS, message)
        self.assertEqual((1, 1), (self.count(models().ObjetivoCalidad),
                                  self.count(models().MedicionObjetivo)))


class MeasurementTestCase(Helpers, ServiceBase):
    def test_create_records_the_value_stamps_and_audit(self) -> None:
        objective, ana = self.objective(), self.person("Eva")
        created = self.measure(objective.id, date(2026, 1, 31), "92.5", actor(AUDITOR),
                               comentario="Dos retrasos", registrado_por_id=ana)
        self.assertEqual((Decimal("92.5000"), "Dos retrasos", ana, (7, 7)),
                         (created.valor, created.comentario, created.registrado_por_id,
                          (created.created_by_id, created.updated_by_id)))
        (row,) = self.rows("mediciones_objetivo")
        self.assertEqual(("create", "92.5000", "2026-01-31"),
                         (row.action, row.after["valor"], row.after["fecha"]))
        self.edit("measurements", created.id, {"valor": 93}, today=TODAY)
        self.edit("measurements", created.id, {"valor": "93.0", "comentario": "Dos retrasos"},
                  today=TODAY)
        self.assertEqual(["create", "update"], [r.action for r in self.rows("mediciones_objetivo")])
        qo().measurements.delete(db.session, CLI, created.id)
        db.session.commit()
        self.assertEqual("delete", self.rows("mediciones_objetivo")[-1].action)

    def test_the_date_lies_in_the_period_and_never_in_the_future(self) -> None:
        objective = self.objective(fecha_limite=date(2026, 10, 31))
        for fecha in (date(2026, 1, 1), TODAY):
            self.measure(objective.id, fecha, 90)
        short = self.objective(titulo="Corto", fecha_limite=date(2026, 6, 30))
        self.measure(short.id, date(2026, 6, 30), 90)
        create = qo().measurements.create
        for objective_id, fecha, message in (
            (objective.id, date(2025, 12, 31), qo().OUTSIDE_PERIOD),
            (short.id, date(2026, 7, 1), qo().OUTSIDE_PERIOD),
            (objective.id, date(2026, 10, 12), qo().IN_THE_FUTURE),
        ):
            with self.subTest(fecha=fecha):
                got = self.refused(errors().ValidationError, create, CLI,
                                   {"objetivo_id": objective_id, "fecha": fecha, "valor": 1},
                                   today=TODAY)
                self.assertTrue(got.startswith(message.split("{")[0]), got)
        first = qo().measurements.list_(db.session, CLI, objetivo_id=objective.id)[0]
        self.refused(errors().ValidationError, qo().measurements.update, CLI, first.id,
                     {"fecha": date(2026, 10, 12)}, today=TODAY)
        for bad_today in (None, "2026-10-11"):
            with self.subTest(today=bad_today), self.assertRaises(ValueError):
                create(db.session, CLI, {"objetivo_id": objective.id, "fecha": date(2026, 2, 1),
                                         "valor": 1}, today=bad_today)
            db.session.rollback()

    def test_only_an_active_objective_takes_or_edits_measurements(self) -> None:
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        for estado in ("alcanzado", "no_alcanzado", "cancelado"):
            with self.subTest(estado=estado):
                self.edit("objectives", objective.id, {"estado": estado})
                for call, args in ((qo().measurements.create, ({
                        "objetivo_id": objective.id, "fecha": date(2026, 2, 28), "valor": 1},)),
                        (qo().measurements.update, (measured.id, {"comentario": "x"}))):
                    message = self.refused(errors().ValidationError, call, CLI, *args, today=TODAY)
                    self.assertEqual(qo().OBJECTIVE_NOT_ACTIVE, message)
        self.edit("objectives", objective.id, {"estado": "activo"})
        self.edit("measurements", measured.id, {"comentario": "Revisado"}, today=TODAY)

    def test_a_no_op_edit_of_a_closed_objectives_measurement_writes_nothing(self) -> None:
        """The no-op rule wins over the closed-objective refusal; any change is refused."""
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90, comentario="Enero")
        self.edit("objectives", objective.id, {"estado": "cancelado"})
        audit_rows, stamp = len(self.rows("mediciones_objetivo")), measured.updated_at
        same = {"objetivo_id": objective.id, "fecha": date(2026, 1, 31), "valor": "90.00",
                "comentario": "Enero", "registrado_por_id": None}
        for data in (same, {"valor": 90}, {}):
            with self.subTest(data=data):
                found = self.edit("measurements", measured.id, data, today=TODAY)
                self.assertEqual((Decimal("90"), stamp), (found.valor, found.updated_at))
        self.assertEqual(audit_rows, len(self.rows("mediciones_objetivo")))
        for data in ({"valor": 91}, {"comentario": "Revisado"}, {"valor": "noventa"},
                     {"fecha": date(2026, 2, 28)}):
            with self.subTest(data=data):
                message = self.refused(errors().ValidationError, qo().measurements.update, CLI,
                                       measured.id, data, today=TODAY)
                self.assertEqual(qo().OBJECTIVE_NOT_ACTIVE, message)
        message = self.refused(errors().ValidationError, qo().measurements.update, CLI,
                               measured.id, {"objetivo_id": objective.id + 1}, today=TODAY)
        self.assertEqual(qo().OBJECTIVE_FIXED, message)
        self.assertEqual(audit_rows, len(self.rows("mediciones_objetivo")))

    def test_one_per_date_its_objective_fixed_and_existing(self) -> None:
        objective, other = self.objective(), self.objective(titulo="Otro")
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        message = self.refused(errors().Conflict, qo().measurements.create, CLI, {
            "objetivo_id": objective.id, "fecha": date(2026, 1, 31), "valor": 91}, today=TODAY)
        self.assertEqual(qo().DUPLICATE_DATE, message)
        self.assertEqual(qo().OBJECTIVE_FIXED, self.refused(
            errors().ValidationError, qo().measurements.update, CLI, measured.id,
            {"objetivo_id": other.id}, today=TODAY))
        for missing in (999, 2 ** 40):
            self.assertEqual(qo().UNKNOWN_OBJECTIVE, self.refused(
                errors().ValidationError, qo().measurements.create, CLI,
                {"objetivo_id": missing, "fecha": date(2026, 2, 1), "valor": 1}, today=TODAY))
        for data in ({"objetivo_id": objective.id, "fecha": date(2026, 2, 1)},
                     {"objetivo_id": objective.id, "fecha": "2026-02-01", "valor": 1},
                     {"objetivo_id": objective.id, "fecha": date(2026, 2, 1), "valor": "x"},
                     {"objetivo_id": objective.id, "fecha": date(2026, 2, 1), "valor": 1,
                      "registrado_por_id": 999}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, qo().measurements.create, CLI, data,
                             today=TODAY)
        self.assertEqual(1, self.count(models().MedicionObjetivo))

    def test_unknown_keys_are_refused_before_any_lookup(self) -> None:
        """Like every service, ahead of the measurement, its objective and missing keys."""
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        create, update = qo().measurements.create, qo().measurements.update
        cases = (
            (create, ({"objetivo_id": objective.id, "fecha": date(2026, 2, 28), "valor": 1,
                       "nota": "x"},)),
            (create, ({"objetivo_id": objective.id, "nota": "x"},)),
            (create, ({"objetivo_id": 999, "fecha": date(2026, 2, 28), "valor": 1,
                       "nota": "x"},)),
            (update, (measured.id, {"nota": "x"})),
            (update, (measured.id, {"valor": 91, "nota": "x"})),
            (update, (999, {"valor": 91, "nota": "x"})),
        )
        for closed in (False, True):
            if closed:
                self.edit("objectives", objective.id, {"estado": "cancelado"})
            for call, args in cases:
                with self.subTest(closed=closed, args=args):
                    message = self.refused(errors().ValidationError, call, CLI, *args,
                                           today=TODAY)
                    self.assertEqual("Campos no permitidos: nota.", message)
        self.assertEqual([Decimal("90")], [m.valor for m in qo().measurements.list_(
            db.session, CLI)])
        self.assertEqual(["create"], [r.action for r in self.rows("mediciones_objetivo")])

    def test_numbers_must_be_finite(self) -> None:
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        owner = self.person("Eva")
        for value in ("nan", "NaN", "inf", "-Infinity", " Infinity ", "sNaN", float("nan"),
                      float("-inf"), Decimal("sNaN"), Decimal("Infinity")):
            calls = (
                ("meta", qo().objectives.create, (OBJECTIVE | {"responsable_id": owner,
                                                               "meta": value},), {}),
                ("valor_inicial", qo().objectives.create, (
                    OBJECTIVE | {"responsable_id": owner, "valor_inicial": value},), {}),
                ("meta", qo().objectives.update, (objective.id, {"meta": value}), {}),
                ("valor_inicial", qo().objectives.update,
                 (objective.id, {"valor_inicial": value}), {}),
                ("valor", qo().measurements.create, (
                    {"objetivo_id": objective.id, "fecha": date(2026, 2, 28), "valor": value},),
                 {"today": TODAY}),
                ("valor", qo().measurements.update, (measured.id, {"valor": value}),
                 {"today": TODAY}),
            )
            for key, call, args, kwargs in calls:
                with self.subTest(value=value, key=key, call=call):
                    message = self.refused(errors().ValidationError, call, CLI, *args, **kwargs)
                    self.assertEqual(qo().NOT_FINITE.format(key=key), message)
        self.assertEqual(1, self.count(models().ObjetivoCalidad))
        self.assertEqual([Decimal("90")], [m.valor for m in qo().measurements.list_(
            db.session, CLI)])

    def test_a_closed_objectives_measurement_cannot_be_deleted(self) -> None:
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        for estado in ("alcanzado", "no_alcanzado", "cancelado"):
            with self.subTest(estado=estado):
                self.edit("objectives", objective.id, {"estado": estado})
                message = self.refused(errors().ValidationError, qo().measurements.delete, CLI,
                                       measured.id)
                self.assertEqual(qo().OBJECTIVE_NOT_ACTIVE, message)
        self.assertEqual(1, self.count(models().MedicionObjetivo))
        self.refused(errors().NotFound, qo().measurements.delete, CLI, 999)
        self.refused(errors().PermissionDenied, qo().measurements.delete, actor(AUDITOR),
                     measured.id)
        self.edit("objectives", objective.id, {"estado": "activo"})
        qo().measurements.delete(db.session, CLI, measured.id)
        db.session.commit()
        self.assertEqual(0, self.count(models().MedicionObjetivo))
        self.assertEqual(["create", "delete"],
                         [r.action for r in self.rows("mediciones_objetivo")])


class ReadAndPolicyTestCase(Helpers, ServiceBase):
    def test_measurements_by_objective_come_in_date_order_from_one_query(self) -> None:
        first, second, empty = (self.objective(titulo=t) for t in ("Uno", "Dos", "Tres"))
        for objective_id, fecha in ((first.id, date(2026, 3, 31)), (second.id, date(2026, 2, 28)),
                                    (first.id, date(2026, 1, 31))):
            self.measure(objective_id, fecha, 90)
        ids = [first.id, second.id, empty.id, 999]
        executed: list[str] = []

        def count(conn, cursor, statement, *args) -> None:
            executed.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            got = qo().measurements.by_objective(db.session, actor(OPERATIVO), ids)
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        self.assertEqual(1, len(executed))
        self.assertEqual({first.id: [date(2026, 1, 31), date(2026, 3, 31)],
                          second.id: [date(2026, 2, 28)], empty.id: [], 999: []},
                         {key: [m.fecha for m in found] for key, found in got.items()})
        self.assertEqual({}, qo().measurements.by_objective(db.session, CLI, []))
        self.refused(errors().PermissionDenied, qo().measurements.by_objective,
                     actor(OPERATIVO, channel="mcp", scopes=()), ids)

    def test_lists_filter_by_year_state_owner_process_and_objective(self) -> None:
        process_id, eva = self.process(), self.person("Eva")
        a = self.objective(fecha_limite=date(2026, 6, 30), proceso_id=process_id)
        b = self.objective(titulo="B", responsable_id=eva)
        c = self.objective(titulo="C", anio=2025)
        self.edit("objectives", b.id, {"estado": "cancelado"})
        listed = qo().objectives.list_
        self.assertEqual([a, b, c], listed(db.session, CLI))
        self.assertEqual([a, b], listed(db.session, CLI, anio=2026))
        self.assertEqual([b], listed(db.session, CLI, estado="cancelado"))
        self.assertEqual([a, c], listed(db.session, CLI, estado=models().EstadoObjetivo.activo))
        self.assertEqual([b], listed(db.session, CLI, responsable_id=eva))
        self.assertEqual([a], listed(db.session, CLI, proceso_id=process_id))
        self.assertEqual([], listed(db.session, CLI, proceso_id=2 ** 40))
        self.assertEqual(([b], 3), qo().objectives.list_page(db.session, CLI, page=2, per_page=1))
        self.refused(errors().ValidationError, listed, CLI, estado="Activo")
        later = self.measure(a.id, date(2026, 3, 31), 1)
        earlier = self.measure(a.id, date(2026, 2, 28), 2)
        self.measure(c.id, date(2026, 2, 28), 3)
        self.assertEqual([earlier, later],
                         qo().measurements.list_(db.session, CLI, objetivo_id=a.id))
        self.assertEqual((1, a.id), (len(qo().measurements.list_page(
            db.session, CLI, objetivo_id=a.id, per_page=1)[0]), later.objetivo_id))

    def test_every_role_reads_admins_and_auditors_write_admins_delete(self) -> None:
        objective = self.objective()
        measured = self.measure(objective.id, date(2026, 1, 31), 90)
        cases = {
            "objectives": (objective.id, {"titulo": "X"}, {}),
            "measurements": (measured.id, {"comentario": "X"}, {"today": TODAY}),
        }
        for register, (record_id, change, extra) in cases.items():
            service = getattr(qo(), register)
            for role in RoleEnum:
                with self.subTest(register=register, read=role.name):
                    self.assertEqual(record_id, service.get(db.session, actor(role), record_id).id)
                    self.assertEqual(1, len(service.list_(db.session, actor(role))))
                    self.assertEqual(1, service.list_page(db.session, actor(role))[1])
            denied = [(actor(OPERATIVO), "create"), (actor(OPERATIVO), "update"),
                      (actor(AUDITOR), "delete"), (mcp_actor(ADMIN), "delete"),
                      (actor(AUDITOR, scopes={"read"}), "update"),
                      (actor(AUDITOR, scopes={"write"}), "get")]
            for who, operation in denied:
                with self.subTest(register=register, denied=operation, role=who.role.name):
                    args = {"create": ({},), "update": (record_id, change),
                            "delete": (record_id,), "get": (record_id,)}[operation]
                    kwargs = extra if operation in ("create", "update") else {}
                    self.refused(errors().PermissionDenied, getattr(service, operation), who,
                                 *args, **kwargs)
            self.edit(register, record_id, change, actor(AUDITOR), **extra)
        self.refused(errors().PermissionDenied, qo().off_track, actor(scopes={"write"}),
                     today=TODAY)
        qo().measurements.delete(db.session, actor(ADMIN), measured.id)
        qo().objectives.delete(db.session, actor(ADMIN), objective.id)
        db.session.commit()
        self.assertEqual(0, self.count(models().ObjetivoCalidad))


def built(**objective):
    """An unsaved objective for the pure helpers."""
    values = {"meta": Decimal("95"), "sentido": models().SentidoObjetivo.mayor_igual,
              "estado": models().EstadoObjetivo.activo,
              "frecuencia": models().FrecuenciaMedicion.mensual,
              "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2027, 12, 31)}
    return models().ObjetivoCalidad(**(values | objective))


def measured(fecha: date, valor) -> object:
    return models().MedicionObjetivo(fecha=fecha, valor=Decimal(str(valor)))


class SummaryTestCase(unittest.TestCase):
    def test_meets_in_both_directions(self) -> None:
        S = models().SentidoObjetivo
        for value, meta, sentido, expected in (
            (95, 95, S.mayor_igual, True), (Decimal("94.9999"), 95, S.mayor_igual, False),
            (96, Decimal("95.5"), S.mayor_igual, True), (3, 3, S.menor_igual, True),
            (Decimal("3.0001"), 3, S.menor_igual, False), (-1, 0, S.menor_igual, True),
        ):
            with self.subTest(value=value, meta=meta, sentido=sentido.name):
                self.assertIs(expected, qo().meets(value, meta, sentido))

    def test_latest_value_target_and_trend(self) -> None:
        objective = built()
        self.assertEqual(
            {"ultimo_valor": None, "ultima_fecha": None, "cumple": None, "tendencia": None,
             "medicion_vencida": False, "proxima_medicion": date(2026, 2, 1)},
            qo().summary(objective, [], today=date(2026, 1, 15)))
        jan, feb = measured(date(2026, 1, 31), 90), measured(date(2026, 2, 28), 96)
        one = qo().summary(objective, [jan], today=date(2026, 2, 1))
        self.assertEqual((Decimal("90"), False, None), (one["ultimo_valor"], one["cumple"],
                                                       one["tendencia"]))
        for later, trend, meets in ((96, "sube", True), (90, "estable", False),
                                    (80, "baja", False)):
            with self.subTest(trend=trend):
                feb = measured(date(2026, 2, 28), later)
                got = qo().summary(objective, [feb, jan], today=date(2026, 3, 1))
                self.assertEqual((date(2026, 2, 28), trend, meets),
                                 (got["ultima_fecha"], got["tendencia"], got["cumple"]))
        lower = built(sentido=models().SentidoObjetivo.menor_igual, meta=Decimal("5"))
        self.assertTrue(qo().summary(lower, [measured(date(2026, 1, 31), 4)],
                                     today=date(2026, 2, 1))["cumple"])

    def test_the_overdue_boundary_of_each_frequency(self) -> None:
        F = models().FrecuenciaMedicion
        last = measured(date(2026, 1, 31), 99)
        for frecuencia, due in ((F.mensual, date(2026, 2, 28)), (F.trimestral, date(2026, 4, 30)),
                                (F.semestral, date(2026, 7, 31)), (F.anual, date(2027, 1, 31))):
            objective = built(frecuencia=frecuencia)
            with self.subTest(frecuencia=frecuencia.name):
                on_time = qo().summary(objective, [last], today=due)
                self.assertEqual((due, False), (on_time["proxima_medicion"],
                                                on_time["medicion_vencida"]))
                late = qo().summary(objective, [last], today=date.fromordinal(due.toordinal() + 1))
                self.assertTrue(late["medicion_vencida"])
        unmeasured = built(frecuencia=F.trimestral)
        self.assertEqual(date(2026, 4, 1), qo().summary(
            unmeasured, [], today=date(2026, 4, 1))["proxima_medicion"])
        self.assertTrue(qo().summary(unmeasured, [], today=date(2026, 4, 2))["medicion_vencida"])

    def test_no_measurement_is_due_after_the_deadline_or_once_closed(self) -> None:
        short = built(fecha_limite=date(2026, 2, 15))
        got = qo().summary(short, [measured(date(2026, 1, 31), 99)], today=date(2026, 2, 16))
        self.assertEqual((date(2026, 2, 15), True),
                         (got["proxima_medicion"], got["medicion_vencida"]))
        final = qo().summary(short, [measured(date(2026, 2, 15), 99)], today=date(2026, 9, 1))
        self.assertEqual((None, False), (final["proxima_medicion"], final["medicion_vencida"]))
        for estado in ("alcanzado", "no_alcanzado", "cancelado"):
            closed = built(estado=models().EstadoObjetivo[estado])
            got = qo().summary(closed, [measured(date(2026, 1, 31), 90)], today=date(2027, 6, 1))
            self.assertEqual((None, False, False), (got["proxima_medicion"],
                                                    got["medicion_vencida"], got["cumple"]))


class OffTrackTestCase(Helpers, ServiceBase):
    def test_lists_active_objectives_off_target_or_overdue(self) -> None:
        on_track = self.objective(titulo="En plazo")
        self.measure(on_track.id, date(2026, 10, 1), 96)
        missed = self.objective(titulo="Por debajo", fecha_limite=date(2026, 11, 30))
        self.measure(missed.id, date(2026, 10, 1), 90)
        overdue = self.objective(titulo="Sin medir", fecha_limite=date(2026, 10, 31))
        closed = self.objective(titulo="Cerrado")
        self.measure(closed.id, date(2026, 10, 1), 10)
        self.edit("objectives", closed.id, {"estado": "no_alcanzado"})
        self.objective(titulo="Reciente", fecha_inicio=date(2026, 10, 1))
        found = qo().off_track(db.session, actor(OPERATIVO), today=TODAY)
        self.assertEqual([overdue, missed], [objective for objective, _ in found])
        flags = [(s["cumple"], s["medicion_vencida"]) for _, s in found]
        self.assertEqual([(None, True), (False, False)], flags)


class PeopleAndProcessesTestCase(Helpers, ServiceBase):
    def test_people_cited_by_an_objective_or_a_measurement_cannot_be_deleted(self) -> None:
        from app.services import people

        objective = self.objective()
        recorder = self.person("Eva")
        self.measure(objective.id, date(2026, 1, 31), 90, registrado_por_id=recorder)
        for person_id in (objective.responsable_id, recorder):
            with self.subTest(person=person_id):
                message = self.refused(errors().Conflict, people.delete, CLI, person_id)
                self.assertEqual(people.STILL_REFERENCED, message)
        self.assertEqual(2, self.count(Person))

    def test_a_process_cited_by_an_objective_is_refused_without_foreign_keys(self) -> None:
        from app.services import process_operations

        self.assertEqual(0, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())
        cited, free = self.process("Compras"), self.process("Ventas")
        self.objective(proceso_id=cited)
        message = self.refused(errors().Conflict, process_operations.delete, CLI, cited)
        self.assertEqual(process_operations.CITED_BY_OBJECTIVES, message)
        self.refused(errors().PermissionDenied, process_operations.delete, actor(OPERATIVO),
                     cited)
        self.refused(errors().NotFound, process_operations.delete, CLI, 999)
        self.assertEqual(2, self.count(ProcesoOperacion))
        process_operations.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual(1, self.count(ProcesoOperacion))

    def test_another_conflict_keeps_its_own_message(self) -> None:
        from app.services import process_operations

        process_id = self.process()
        with patch.object(process_operations.crud, "delete",
                          side_effect=errors().Conflict("Otro conflicto.")):
            message = self.refused(errors().Conflict, process_operations.delete, CLI, process_id)
        self.assertEqual("Otro conflicto.", message)
        self.assertEqual(1, self.count(ProcesoOperacion))


class ForeignKeyCases:
    """The schema backs the service; shared by the SQLite and PostgreSQL cases."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_measurements_key_refuses_a_delete_the_check_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        objective = self.objective()
        self.measure(objective.id, date(2026, 1, 31), 90)
        with patch.object(qo(), "_has_measurements", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                qo().objectives.delete(db.session, CLI, objective.id)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        expected = (qo().HAS_MEASUREMENTS if self.names_violated_constraints
                    else qo().DELETE_CONFLICT)
        self.assertEqual(expected, caught.exception.message)
        db.session.rollback()
        self.assertEqual(1, self.count(models().ObjetivoCalidad))

    def test_a_process_cited_by_an_objective_cannot_be_deleted(self) -> None:
        from app.services import process_operations

        self.enforce_foreign_keys()
        process_id = self.process()
        objective = self.objective(proceso_id=process_id)
        message = self.refused(errors().Conflict, process_operations.delete, CLI, process_id)
        self.assertEqual(process_operations.CITED_BY_OBJECTIVES, message)
        # The foreign key refuses a citation the check did not see: with the same message
        # when the database names it, otherwise with the original conflict's.
        with patch.object(process_operations, "_cited_by_objectives", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                process_operations.delete(db.session, CLI, process_id)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        expected = (process_operations.CITED_BY_OBJECTIVES if self.names_violated_constraints
                    else errors().Conflict.default_message)
        self.assertEqual(expected, caught.exception.message)
        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(process_id, db.session.get(models().ObjetivoCalidad,
                                                    objective.id).proceso_id)

    def test_links_to_people_and_objectives_are_real_foreign_keys(self) -> None:
        self.enforce_foreign_keys()
        objective = self.objective()
        table = models().MedicionObjetivo.__table__
        for statement in (
            table.insert().values(objetivo_id=999, fecha=date(2026, 2, 1), valor=1),
            table.insert().values(objetivo_id=objective.id, fecha=date(2026, 2, 1), valor=1,
                                  registrado_por_id=999),
            Person.__table__.delete(),
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
    async def test_objectives_and_measurements_round_trip(self) -> None:
        ana, process_id = self.seed("personas"), self.seed("procesos")
        who = mcp_actor(AUDITOR)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "objetivos_calidad", "data": {
                "titulo": "Entregas a tiempo", "indicador": "% a tiempo", "meta": 95,
                "sentido": "mayor_igual", "responsable_id": ana, "proceso_id": process_id,
                "fecha_inicio": "2026-01-01", "fecha_limite": "2026-12-31",
                "frecuencia": "mensual"}})
            self.assertFalse(created.is_error, created.content)
            record = created.structured_content
            # Nothing measured since 1 February: the first measurement is overdue.
            self.assertEqual(("95.0000", "≥", "Activo", "Mensual", 2026, None, None, True,
                              "2026-02-01"),
                             tuple(record[k] for k in ("meta", "sentido", "estado", "frecuencia",
                                                       "anio", "ultimo_valor", "cumple",
                                                       "medicion_vencida", "proxima_medicion")))
            measurement = {"module": "mediciones_objetivo", "data": {
                "objetivo_id": record["id"], "fecha": "2026-09-30", "valor": 96.5}}
            added = await self.call(who, "qms_create", measurement)
            self.assertFalse(added.is_error, added.content)
            self.assertEqual(("96.5000", True), (added.structured_content["valor"],
                                                 added.structured_content["cumple"]))
            future = await self.call(who, "qms_create", {**measurement, "data": {
                **measurement["data"], "fecha": "2026-10-12"}})
            self.assertTrue(future.is_error)
            self.assertIn("futura", future.content[0].text)
            got = (await self.call(who, "qms_get", {"module": "objetivos_calidad",
                                                    "id": record["id"]})).structured_content
            self.assertEqual(("96.5000", "2026-09-30", True, None, False, "2026-10-30"),
                             tuple(got[k] for k in ("ultimo_valor", "ultima_fecha", "cumple",
                                                    "tendencia", "medicion_vencida",
                                                    "proxima_medicion")))
            for slug, filters in (
                ("objetivos_calidad", {"anio": 2026, "estado": "activo", "responsable_id": ana,
                                       "proceso_id": process_id}),
                ("mediciones_objetivo", {"objetivo_id": record["id"]}),
            ):
                listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                         {"module": slug, "filters": filters})
                self.assertFalse(listed.is_error, listed.content)
                self.assertEqual(1, listed.structured_content["total"])
            fixed = await self.call(who, "qms_update", {"module": "objetivos_calidad",
                                                        "id": record["id"], "data": {"meta": 90}})
            self.assertTrue(fixed.is_error)
            self.assertIn("meta", fixed.content[0].text)
            denied = await self.call(mcp_actor(OPERATIVO), "qms_create", measurement)
            self.assertTrue(denied.is_error)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        fields = {f["name"]: f for f in described["objetivos_calidad"]["fields"]}
        self.assertEqual(("number", True, ["mayor_igual", "menor_igual"]),
                         (fields["meta"]["type"], fields["meta"]["required"],
                          fields["sentido"]["allowed"]))
        self.assertEqual({"anio", "estado", "responsable_id", "proceso_id"},
                         {f["name"] for f in described["objetivos_calidad"]["filters"]})
        self.assertEqual(["objetivo_id"],
                         [f["name"] for f in described["mediciones_objetivo"]["filters"]])

    async def test_non_finite_numbers_are_refused(self) -> None:
        ana = self.seed("personas")
        who = mcp_actor(AUDITOR)
        objective = {"titulo": "Entregas a tiempo", "indicador": "% a tiempo", "meta": 95,
                     "sentido": "mayor_igual", "responsable_id": ana,
                     "fecha_inicio": "2026-01-01", "fecha_limite": "2026-12-31",
                     "frecuencia": "mensual"}
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "objetivos_calidad",
                                                          "data": objective})
            self.assertFalse(created.is_error, created.content)
            record_id = created.structured_content["id"]
            for value in ("NaN", "Infinity", "-inf", "sNaN"):
                for module, key, data in (
                    ("objetivos_calidad", "meta", objective | {"meta": value}),
                    ("objetivos_calidad", "valor_inicial", objective | {"valor_inicial": value}),
                    ("mediciones_objetivo", "valor", {"objetivo_id": record_id,
                                                      "fecha": "2026-09-30", "valor": value}),
                ):
                    with self.subTest(value=value, key=key):
                        refused = await self.call(who, "qms_create",
                                                  {"module": module, "data": data})
                        self.assertTrue(refused.is_error)
                        self.assertIn(qo().NOT_FINITE.format(key=key),
                                      refused.content[0].text)
                updated = await self.call(who, "qms_update", {
                    "module": "objetivos_calidad", "id": record_id, "data": {"meta": value}})
                self.assertTrue(updated.is_error)
                self.assertIn("finito", updated.content[0].text)
            listed = await self.call(who, "qms_list", {"module": "mediciones_objetivo"})
            self.assertEqual(0, listed.structured_content["total"])


if __name__ == "__main__":
    unittest.main()
