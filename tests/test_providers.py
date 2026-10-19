"""External providers and their evaluations (EP-1 of ``external-providers``).

ISO 9001 clause 8.4, decisions EP1 to EP5. A provider has a unique name, an
optional unique tax id (NIF), its contact, what it supplies, the processes it
affects and selection criteria; its state (``pendiente`` at first) changes only
through the administrators' web decisions, which record the date, the deciding
person and the reason. Evaluations score quality, delivery time, service and
documentation from 1 to 5; the mean and its result (``apto``, ``condicionado``,
``no_apto``) are computed, never stored. Nonconformities with the ``proveedor``
origin may name the provider. The SQLite cases run with the audit flush guard;
the database cases also run on PostgreSQL, which names the foreign key a delete
breaks.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import ADMIN, AUDITOR, OPERATIVO, McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    AuditLog, EstadoNoConformidad, EstadoProveedor, EvaluacionProveedor, NoConformidad,
    OrigenNoConformidad, Person, ProcesoOperacion, Proveedor, ResultadoEvaluacion,
    TipoSuministro, User,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
TODAY = date(2026, 10, 18)
YESTERDAY = TODAY - timedelta(days=1)
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
PROVIDER = {"nombre": "Aceros del Norte", "tipo_suministro": "producto"}
GOOD = {"calidad": 4, "plazo": 4, "servicio": 3, "documentacion": 5}  # 4.00, apto
BAD = {"calidad": 2, "plazo": 2, "servicio": 2, "documentacion": 3}  # 2.25, no_apto


def pv():
    from app.services import providers

    return providers


def po():
    from app.services import process_operations

    return process_operations


def people():
    from app.services import people as module

    return module


def nonconformities():
    from app.services import nonconformities as module

    return module


def scores_for(total: int) -> dict[str, int]:
    """Four 1-5 scores adding up to ``total`` (4 to 20)."""
    base, extra = divmod(total, 4)
    values = [base + 1] * extra + [base] * (4 - extra)
    return dict(zip(("calidad", "plazo", "servicio", "documentacion"), values))


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", activo: bool = True, **values) -> int:
        return self.insert(Person.__table__, nombre=nombre, activo=activo, **values)

    def process(self, nombre: str = "Compras") -> int:
        return self.insert(ProcesoOperacion.__table__, proceso=nombre)

    def admin(self, nombre: str = "Dirección") -> tuple[Actor, int]:
        """A web administrator whose user account is linked to a person; returns both."""
        user_id = self.insert(User.__table__, username=f"admin{nombre}", password="x",
                              role=ADMIN)
        person_id = self.person(nombre, user_id=user_id)
        return Actor(user_id=user_id, label="admin", role=ADMIN, channel="web"), person_id

    def provider(self, **values) -> Proveedor:
        created = pv().providers.create(db.session, CLI, PROVIDER | values)
        db.session.commit()
        return created

    def evaluate(self, provider_id: int, evaluador_id: int, fecha: date = date(2026, 10, 1),
                 scores: dict | None = None, **values) -> EvaluacionProveedor:
        created = pv().evaluations.create(db.session, CLI, {
            "proveedor_id": provider_id, "fecha": fecha, "evaluador_id": evaluador_id,
            **(GOOD if scores is None else scores), **values}, today=TODAY)
        db.session.commit()
        return created

    def nc(self, provider_id: int | None, estado=EstadoNoConformidad.abierta) -> int:
        return self.insert(NoConformidad.__table__, descripcion="Lote defectuoso",
                           fecha_detectada=date(2026, 10, 1), estado=estado,
                           origen=OrigenNoConformidad.proveedor, proveedor_id=provider_id)

    def links(self) -> list[tuple]:
        return [tuple(row) for row in db.session.execute(text(
            "SELECT proveedor_id, proceso_id FROM proveedor_procesos ORDER BY 1, 2"))]

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message

    def ids(self, records) -> list[int]:
        return [record.id for record in records]

    @staticmethod
    def change(row: AuditLog) -> tuple[dict, dict]:
        """An update row's before and after without the ``updated_*`` stamps."""
        def strip(values: dict) -> dict:
            return {k: v for k, v in values.items() if k not in ("updated_at", "updated_by_id")}

        return strip(row.before), strip(row.after)


class ScoreTestCase(unittest.TestCase):
    def test_the_score_is_the_mean_of_the_four_scores_with_two_decimals(self) -> None:
        for scores, expected in (((5, 5, 5, 5), "5.00"), ((4, 4, 4, 3), "3.75"),
                                 ((4, 3, 3, 4), "3.50"), ((3, 3, 3, 4), "3.25"),
                                 ((2, 3, 2, 3), "2.50"), ((2, 2, 2, 3), "2.25"),
                                 ((1, 1, 1, 1), "1.00")):
            with self.subTest(scores=scores):
                evaluation = SimpleNamespace(**dict(zip(
                    ("calidad", "plazo", "servicio", "documentacion"), scores)))
                score = pv().puntuacion(evaluation)
                self.assertEqual((Decimal(expected), expected), (score, str(score)))

    def test_the_result_bands_meet_at_their_edges(self) -> None:
        apto, condicionado, no_apto = (ResultadoEvaluacion.apto, ResultadoEvaluacion.condicionado,
                                       ResultadoEvaluacion.no_apto)
        for score, expected in ((5, apto), (Decimal("3.5"), apto), (Decimal("3.50"), apto),
                                (Decimal("3.49"), condicionado), (3, condicionado),
                                (Decimal("2.5"), condicionado), (Decimal("2.49"), no_apto),
                                (1, no_apto)):
            with self.subTest(score=score):
                self.assertIs(expected, pv().resultado(score))
        self.assertEqual(["Apto", "Condicionado", "No apto"],
                         [member.value for member in ResultadoEvaluacion])

    def test_the_choices_show_their_spanish_labels(self) -> None:
        self.assertEqual(["Producto", "Servicio", "Proceso externalizado"],
                         [member.value for member in TipoSuministro])
        self.assertEqual(["Pendiente", "Aprobado", "Condicionado", "Bloqueado"],
                         [member.value for member in EstadoProveedor])


class ProviderRegisterTestCase(Helpers, ServiceBase):
    def test_a_new_provider_starts_pending_with_no_decision_and_an_audit_row(self) -> None:
        created = pv().providers.create(db.session, actor(AUDITOR), PROVIDER | {
            "contacto": "Luis Gil", "telefono": "+34 600 000 000",
            "descripcion_suministro": "Chapa laminada", "criterios_seleccion": "ISO 9001",
            "fecha_proxima_evaluacion": date(2027, 1, 15), "notas": "Proveedor histórico"})
        db.session.commit()
        self.assertEqual(
            (EstadoProveedor.pendiente, None, None, None, TipoSuministro.producto,
             date(2027, 1, 15), [], 7),
            (created.estado, created.fecha_estado, created.decidido_por_id,
             created.motivo_estado, created.tipo_suministro, created.fecha_proxima_evaluacion,
             created.proceso_ids, created.created_by_id))
        (row,) = self.rows("proveedores")
        self.assertEqual(("create", "Pendiente", "Producto", [], "Chapa laminada"),
                         (row.action, row.after["estado"], row.after["tipo_suministro"],
                          row.after["procesos"], row.after["descripcion_suministro"]))
        self.assertIs(created, pv().providers.get(db.session, actor(OPERATIVO), created.id))

    def test_the_tax_id_is_normalised_and_lightly_checked(self) -> None:
        for given, stored in (("b-1234 5678", "B12345678"), ("12345678z", "12345678Z"),
                              ("x-1234567-l", "X1234567L"), ("", None), ("   ", None),
                              (None, None)):
            with self.subTest(nif=given):
                created = pv().providers.create(db.session, CLI, PROVIDER | {
                    "nombre": f"Proveedor {given!r}", "nif": given})
                self.assertEqual(stored, created.nif)
                db.session.rollback()
        for given in ("1234567", "ABCDEFGHIJK", "ESB12345678", "12.345.678", "B1234567€",
                      12345678):
            with self.subTest(refused=given):
                message = self.refused(errors().ValidationError, pv().providers.create, CLI,
                                       PROVIDER | {"nif": given})
                self.assertIn("NIF", message)
        self.assertEqual(0, db.session.query(Proveedor).count())

    def test_contact_email_and_supply_are_validated(self) -> None:
        created = pv().providers.create(db.session, CLI, PROVIDER | {
            "email": "  Compras@Aceros.Example ", "tipo_suministro": TipoSuministro.servicio})
        self.assertEqual(("compras@aceros.example", TipoSuministro.servicio),
                         (created.email, created.tipo_suministro))
        self.assertIsNone(pv().providers.update(db.session, CLI, created.id, {"email": ""}).email)
        db.session.rollback()
        for data in ({"email": "sin-arroba"}, {"tipo_suministro": "material"},
                     {"tipo_suministro": "Producto"}, {"nombre": "x" * 201}, {"nombre": " "},
                     {"contacto": "x" * 151}, {"telefono": "x" * 31},
                     {"fecha_proxima_evaluacion": "2027-01-15"}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, pv().providers.create, CLI,
                             PROVIDER | data)
        for missing in ("nombre", "tipo_suministro"):
            with self.subTest(missing=missing):
                data = {key: value for key, value in PROVIDER.items() if key != missing}
                message = self.refused(errors().ValidationError, pv().providers.create, CLI, data)
                self.assertIn(missing, message)
        self.assertEqual(0, db.session.query(Proveedor).count())

    def test_names_and_tax_ids_are_unique(self) -> None:
        first = self.provider(nif="B12345678")
        other = self.provider(nombre="Transportes Sur")
        for call, args in ((pv().providers.create, (PROVIDER,)),
                           (pv().providers.update, (other.id, {"nombre": PROVIDER["nombre"]}))):
            with self.subTest(call=call.__name__):
                self.assertEqual(pv().NAME_TAKEN,
                                 self.refused(errors().Conflict, call, CLI, *args))
        for call, args in ((pv().providers.create, (PROVIDER | {"nombre": "Otro",
                                                                "nif": "b-12345678"},)),
                           (pv().providers.update, (other.id, {"nif": "B12345678"}))):
            with self.subTest(call=call.__name__, nif=True):
                self.assertEqual(pv().NIF_TAKEN,
                                 self.refused(errors().Conflict, call, CLI, *args))
        self.assertEqual("B12345678",
                         pv().providers.update(db.session, CLI, first.id,
                                               {"nif": "B-12345678"}).nif)

    def test_the_state_and_its_decision_are_never_written_by_create_or_update(self) -> None:
        provider = self.provider()
        for key, value in (("estado", "aprobado"), ("fecha_estado", TODAY),
                           ("decidido_por_id", 1), ("motivo_estado", "Bien"), ("id", 9)):
            with self.subTest(key=key):
                for call, args in ((pv().providers.create, (PROVIDER | {key: value},)),
                                   (pv().providers.update, (provider.id, {key: value}))):
                    message = self.refused(errors().ValidationError, call, CLI, *args)
                    self.assertIn("Campos no permitidos", message)
        db.session.expire_all()
        self.assertEqual(EstadoProveedor.pendiente, db.session.get(Proveedor, provider.id).estado)
        self.assertEqual(1, len(self.rows("proveedores")))

    def test_procesos_replaces_the_linked_processes_and_the_audit_rows_carry_it(self) -> None:
        compras, almacen, ventas = (self.process(n) for n in ("Compras", "Almacén", "Ventas"))
        provider = self.provider(procesos=[ventas, compras])
        self.assertEqual(([compras, ventas], ["Compras", "Ventas"]),
                         (provider.proceso_ids, [p.proceso for p in provider.procesos]))
        self.assertEqual([compras, ventas], self.rows("proveedores")[-1].after["procesos"])
        pv().providers.update(db.session, CLI, provider.id, {"procesos": [almacen, compras]})
        db.session.commit()
        self.assertEqual([(provider.id, compras), (provider.id, almacen)], self.links())
        self.assertEqual(({"procesos": [compras, ventas]}, {"procesos": [compras, almacen]}),
                         self.change(self.rows("proveedores")[-1]))
        pv().providers.update(db.session, CLI, provider.id, {"procesos": None})
        db.session.commit()
        self.assertEqual([], self.links())
        for value in ([999], [compras, "x"], "1", [True], 7):
            with self.subTest(procesos=value):
                self.refused(errors().ValidationError, pv().providers.update, CLI, provider.id,
                             {"procesos": value})
        self.assertEqual(3, db.session.query(ProcesoOperacion).count())

    def test_an_update_that_changes_nothing_writes_nothing(self) -> None:
        compras = self.process()
        provider = self.provider(nif="B12345678", email="a@b.es", procesos=[compras])
        before = len(self.rows("proveedores"))
        same = pv().providers.update(db.session, CLI, provider.id, {
            "nombre": " Aceros del Norte ", "nif": "b-12345678", "email": "A@B.ES",
            "tipo_suministro": "producto", "procesos": [compras], "notas": ""})
        db.session.commit()
        self.assertIs(provider, same)
        self.assertEqual(before, len(self.rows("proveedores")))

    def test_access_by_role_and_channel(self) -> None:
        provider = self.provider()
        change = {"notas": "Cambio"}
        denied = ((actor(OPERATIVO), pv().providers.create, (PROVIDER,)),
                  (actor(OPERATIVO), pv().providers.update, (provider.id, change)),
                  (actor(AUDITOR), pv().providers.delete, (provider.id,)),
                  (mcp_actor(ADMIN), pv().providers.delete, (provider.id,)),
                  (actor(ADMIN, scopes={"read"}), pv().providers.update, (provider.id, change)),
                  (actor(ADMIN, scopes={"write"}), pv().providers.get, (provider.id,)))
        for who, call, args in denied:
            with self.subTest(role=who.role.name, channel=who.channel, call=call.__name__):
                self.refused(errors().PermissionDenied, call, who, *args)
        for role in (OPERATIVO, AUDITOR, ADMIN):
            with self.subTest(reads=role.name):
                self.assertEqual([provider.id],
                                 self.ids(pv().providers.list_(db.session, actor(role))))
        self.assertEqual("Cambio", pv().providers.update(db.session, mcp_actor(AUDITOR),
                                                         provider.id, change).notas)
        self.assertEqual(pv().PROVIDER_NOT_FOUND,
                         self.refused(errors().NotFound, pv().providers.get, CLI, 999))


class DecisionTestCase(Helpers, ServiceBase):
    def test_an_administrator_approves_conditions_and_blocks_with_date_decider_and_reason(
            self) -> None:
        admin, decider = self.admin()
        provider = self.provider()
        estado = EstadoProveedor
        steps = (
            (pv().approve, (), {}, (estado.aprobado, None)),
            (pv().condition, ("  Entregas tardías  ",), {},
             (estado.condicionado, "Entregas tardías")),
            (pv().block, ("Certificado caducado",), {}, (estado.bloqueado, "Certificado caducado")),
            (pv().approve, (), {"motivo": "Certificado renovado"},
             (estado.aprobado, "Certificado renovado")),
        )
        for day, (decide, args, kwargs, expected) in enumerate(steps, start=1):
            with self.subTest(decision=decide.__name__, step=day):
                on = date(2026, 10, day)
                found = decide(db.session, admin, provider.id, *args, today=on, **kwargs)
                db.session.commit()
                self.assertEqual((*expected, on, decider),
                                 (found.estado, found.motivo_estado, found.fecha_estado,
                                  found.decidido_por_id))
        rows = self.rows("proveedores")
        self.assertEqual(["create", "update", "update", "update", "update"],
                         [row.action for row in rows])
        self.assertEqual(({"estado": "Pendiente", "fecha_estado": None, "decidido_por_id": None},
                          {"estado": "Aprobado", "fecha_estado": "2026-10-01",
                           "decidido_por_id": decider}),
                         self.change(rows[1]))
        self.assertEqual({"estado": "Condicionado", "motivo_estado": "Entregas tardías",
                          "fecha_estado": "2026-10-02"}, self.change(rows[2])[1])

    def test_conditioning_and_blocking_need_a_reason(self) -> None:
        admin, _ = self.admin()
        provider = self.provider()
        for decide in (pv().condition, pv().block):
            for motivo in (None, "", "   ", 7):
                with self.subTest(decision=decide.__name__, motivo=motivo):
                    message = self.refused(errors().ValidationError, decide, admin, provider.id,
                                           motivo, today=TODAY)
                    self.assertIn("motivo", message)
        self.assertIsNone(pv().approve(db.session, admin, provider.id, today=TODAY,
                                       motivo="  ").motivo_estado)

    def test_a_decision_that_keeps_the_state_is_refused(self) -> None:
        admin, _ = self.admin()
        provider = self.provider()
        for first, again, args in ((pv().approve, pv().approve, ()),
                                   (pv().condition, pv().condition, ("Plazos",)),
                                   (pv().block, pv().block, ("Calidad",))):
            with self.subTest(decision=again.__name__):
                first(db.session, admin, provider.id, *args, today=TODAY)
                db.session.commit()
                rows = len(self.rows("proveedores"))
                message = self.refused(errors().ValidationError, again, admin, provider.id,
                                       *args, today=TODAY)
                self.assertIn("ya está", message)
                self.assertEqual(rows, len(self.rows("proveedores")))

    def test_only_administrators_on_the_web_decide(self) -> None:
        provider = self.provider()
        denied = (actor(AUDITOR), actor(OPERATIVO), mcp_actor(ADMIN),
                  actor(ADMIN, scopes={"read"}))
        for who in denied:
            for decide, args in ((pv().approve, ()), (pv().condition, ("Plazos",)),
                                 (pv().block, ("Calidad",))):
                with self.subTest(role=who.role.name, channel=who.channel,
                                  decision=decide.__name__):
                    self.refused(errors().PermissionDenied, decide, who, provider.id, *args,
                                 today=TODAY)
            with self.subTest(may_decide=who.role.name, channel=who.channel):
                self.assertFalse(pv().may_decide(who))
        self.assertTrue(pv().may_decide(actor(ADMIN)))
        self.assertTrue(pv().may_decide(CLI))
        self.assertEqual(EstadoProveedor.pendiente, provider.estado)
        self.assertEqual(1, len(self.rows("proveedores")))

    def test_a_decider_without_a_person_is_recorded_as_none(self) -> None:
        provider = self.provider()
        found = pv().block(db.session, actor(ADMIN), provider.id, "Sin certificado", today=TODAY)
        self.assertEqual((EstadoProveedor.bloqueado, None, TODAY),
                         (found.estado, found.decidido_por_id, found.fecha_estado))

    def test_unknown_providers_and_a_date_that_is_not_a_date(self) -> None:
        admin, _ = self.admin()
        self.assertEqual(pv().PROVIDER_NOT_FOUND,
                         self.refused(errors().NotFound, pv().approve, admin, 999, today=TODAY))
        provider = self.provider()
        for today in ("2026-10-18", None):
            with self.subTest(today=today):
                with self.assertRaises(ValueError):
                    pv().approve(db.session, admin, provider.id, today=today)
                db.session.rollback()


class EvaluationTestCase(Helpers, ServiceBase):
    def test_an_evaluation_records_its_scores_and_reschedules_the_provider(self) -> None:
        ana = self.person()
        provider = self.provider(fecha_proxima_evaluacion=date(2026, 10, 1))
        created = pv().evaluations.create(db.session, actor(AUDITOR), {
            "proveedor_id": provider.id, "fecha": date(2026, 10, 10), "evaluador_id": ana,
            **GOOD, "comentario": "Buen servicio", "fecha_proxima": date(2027, 4, 10)},
            today=TODAY)
        db.session.commit()
        self.assertEqual((4, 4, 3, 5, "Buen servicio", Decimal("4.00")),
                         (created.calidad, created.plazo, created.servicio,
                          created.documentacion, created.comentario, pv().puntuacion(created)))
        self.assertEqual(date(2027, 4, 10), provider.fecha_proxima_evaluacion)
        (row,) = self.rows("evaluaciones_proveedor")
        self.assertEqual(("create", 4, "2027-04-10"),
                         (row.action, row.after["calidad"], row.after["fecha_proxima"]))
        self.assertEqual(({"fecha_proxima_evaluacion": "2026-10-01"},
                          {"fecha_proxima_evaluacion": "2027-04-10"}),
                         self.change(self.rows("proveedores")[-1]))
        later = self.evaluate(provider.id, ana, fecha=date(2026, 10, 12))
        self.assertEqual((date(2027, 4, 10), 2),
                         (provider.fecha_proxima_evaluacion, len(self.rows("proveedores"))))
        pv().evaluations.update(db.session, CLI, later.id, {"fecha_proxima": date(2027, 5, 1)},
                                today=TODAY)
        db.session.commit()
        self.assertEqual(date(2027, 5, 1), provider.fecha_proxima_evaluacion)
        self.assertIs(later, pv().latest_evaluation(db.session, provider.id))
        self.assertEqual(EstadoProveedor.pendiente, provider.estado)

    def test_an_earlier_evaluation_never_reschedules_the_provider(self) -> None:
        ana = self.person()
        provider = self.provider()
        latest = self.evaluate(provider.id, ana, fecha=date(2026, 10, 10),
                               fecha_proxima=date(2027, 4, 10))
        earlier = self.evaluate(provider.id, ana, fecha=date(2026, 9, 1),
                                fecha_proxima=date(2026, 12, 1))
        pv().evaluations.update(db.session, CLI, earlier.id, {"fecha_proxima": date(2027, 1, 1)},
                                today=TODAY)
        db.session.commit()
        self.assertEqual(date(2027, 4, 10), provider.fecha_proxima_evaluacion)
        self.assertIs(latest, pv().latest_evaluation(db.session, provider.id))
        same_day = self.evaluate(provider.id, ana, fecha=date(2026, 10, 10))
        self.assertIs(same_day, pv().latest_evaluation(db.session, provider.id))
        self.assertIsNone(pv().latest_evaluation(db.session, self.provider(nombre="Otro").id))

    def test_scores_are_whole_numbers_from_one_to_five(self) -> None:
        ana, provider = self.person(), self.provider()
        for value in (0, 6, -1, True, "4", 4.0, None):
            for key in GOOD:
                with self.subTest(key=key, value=value):
                    message = self.refused(errors().ValidationError, pv().evaluations.create, CLI, {
                        "proveedor_id": provider.id, "fecha": TODAY, "evaluador_id": ana,
                        **GOOD, key: value}, today=TODAY)
                    self.assertIn(key, message)
        message = self.refused(errors().ValidationError, pv().evaluations.create, CLI, {
            "proveedor_id": provider.id, "fecha": TODAY, "evaluador_id": ana, "calidad": 3},
            today=TODAY)
        self.assertIn("Faltan campos obligatorios", message)
        self.assertEqual(0, db.session.query(EvaluacionProveedor).count())

    def test_the_date_is_never_in_the_future_and_the_next_one_comes_after_it(self) -> None:
        ana, provider = self.person(), self.provider()
        base = {"proveedor_id": provider.id, "evaluador_id": ana, **GOOD}
        for data, expected in (
            ({"fecha": TODAY + timedelta(days=1)}, pv().EVALUATED_IN_FUTURE),
            ({"fecha": TODAY, "fecha_proxima": TODAY}, pv().NEXT_NOT_AFTER),
            ({"fecha": TODAY, "fecha_proxima": YESTERDAY}, pv().NEXT_NOT_AFTER),
        ):
            with self.subTest(data=data):
                self.assertEqual(expected, self.refused(
                    errors().ValidationError, pv().evaluations.create, CLI, base | data,
                    today=TODAY))
        found = self.evaluate(provider.id, ana, fecha=TODAY,
                              fecha_proxima=TODAY + timedelta(days=1))
        for data, expected in (({"fecha": TODAY + timedelta(days=1)}, pv().EVALUATED_IN_FUTURE),
                               ({"fecha_proxima": TODAY}, pv().NEXT_NOT_AFTER)):
            with self.subTest(update=data):
                self.assertEqual(expected, self.refused(
                    errors().ValidationError, pv().evaluations.update, CLI, found.id, data,
                    today=TODAY))
        with self.assertRaises(ValueError):
            pv().evaluations.create(db.session, CLI, base | {"fecha": TODAY}, today=None)

    def test_the_evaluator_is_an_active_person_and_the_provider_exists(self) -> None:
        ana, eva = self.person(), self.person("Eva Gómez", activo=False)
        provider = self.provider()
        base = {"proveedor_id": provider.id, "fecha": TODAY, "evaluador_id": ana, **GOOD}
        for data, words in (({"evaluador_id": eva}, "desactivada"),
                            ({"evaluador_id": 999}, "ninguna persona"),
                            ({"evaluador_id": None}, "evaluador_id"),
                            ({"proveedor_id": 999}, "ningún proveedor"),
                            ({"proveedor_id": "1"}, "proveedor_id")):
            with self.subTest(data=data):
                self.assertIn(words, self.refused(errors().ValidationError,
                                                  pv().evaluations.create, CLI, base | data,
                                                  today=TODAY))
        found = self.evaluate(provider.id, ana)
        db.session.execute(Person.__table__.update().where(Person.id == ana).values(activo=False))
        db.session.commit()
        self.assertEqual("Sigue igual", pv().evaluations.update(
            db.session, CLI, found.id, {"comentario": "Sigue igual"}, today=TODAY).comentario)

    def test_any_provider_takes_evaluations_and_none_change_its_state(self) -> None:
        admin, _ = self.admin()
        ana = self.person()
        provider = self.provider()
        pv().block(db.session, admin, provider.id, "Rechazo de lote", today=TODAY)
        db.session.commit()
        self.evaluate(provider.id, ana, scores={"calidad": 5, "plazo": 5, "servicio": 5,
                                                "documentacion": 5})
        self.assertEqual((EstadoProveedor.bloqueado, "Rechazo de lote"),
                         (provider.estado, provider.motivo_estado))

    def test_an_evaluation_never_moves_to_another_provider(self) -> None:
        ana, provider, other = self.person(), self.provider(), self.provider(nombre="Otro")
        found = self.evaluate(provider.id, ana)
        self.assertEqual(pv().PROVIDER_FIXED, self.refused(
            errors().ValidationError, pv().evaluations.update, CLI, found.id,
            {"proveedor_id": other.id}, today=TODAY))
        same = pv().evaluations.update(db.session, CLI, found.id,
                                       {"proveedor_id": provider.id, "calidad": 4}, today=TODAY)
        self.assertEqual(1, len(self.rows("evaluaciones_proveedor")))
        self.assertIs(found, same)
        self.assertEqual(pv().EVALUATION_NOT_FOUND, self.refused(
            errors().NotFound, pv().evaluations.update, CLI, 999, {"calidad": 3}, today=TODAY))

    def test_access_by_role(self) -> None:
        ana, provider = self.person(), self.provider()
        data = {"proveedor_id": provider.id, "fecha": TODAY, "evaluador_id": ana, **GOOD}
        self.refused(errors().PermissionDenied, pv().evaluations.create, actor(OPERATIVO), data,
                     today=TODAY)
        found = pv().evaluations.create(db.session, mcp_actor(AUDITOR), data, today=TODAY)
        db.session.commit()
        for who in (actor(AUDITOR), mcp_actor(ADMIN)):
            with self.subTest(denied_delete=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, pv().evaluations.delete, who, found.id)
        self.assertEqual([found.id], self.ids(pv().evaluations.list_(db.session,
                                                                     actor(OPERATIVO))))
        pv().evaluations.delete(db.session, actor(ADMIN), found.id)
        db.session.commit()
        self.assertEqual(["create", "delete"],
                         [row.action for row in self.rows("evaluaciones_proveedor")])


class FiltersTestCase(Helpers, ServiceBase):
    def test_providers_filter_by_name_state_supply_and_process(self) -> None:
        admin, _ = self.admin()
        compras = self.process()
        aceros = self.provider(procesos=[compras])
        transportes = self.provider(nombre="Transportes Sur", tipo_suministro="servicio")
        pv().approve(db.session, admin, transportes.id, today=TODAY)
        db.session.commit()
        who = actor(OPERATIVO)
        for filters, expected in (
            ({}, [aceros, transportes]),
            ({"nombre": "ACEROS"}, [aceros]),
            ({"estado": "aprobado"}, [transportes]),
            ({"estado": EstadoProveedor.pendiente}, [aceros]),
            ({"tipo_suministro": "servicio"}, [transportes]),
            ({"tipo_suministro": TipoSuministro.proceso_externalizado}, []),
            ({"proceso_id": compras}, [aceros]),
            ({"proceso_id": 999}, []),
            ({"proceso_id": "1"}, []),
            ({"estado": "pendiente", "proceso_id": compras}, [aceros]),
        ):
            with self.subTest(filters=filters):
                self.assertEqual(self.ids(expected),
                                 self.ids(pv().providers.list_(db.session, who, **filters)))
        page, total = pv().providers.list_page(db.session, who, page=2, per_page=1)
        self.assertEqual(([transportes.id], 2), (self.ids(page), total))

    def test_evaluacion_vencida_needs_today_and_skips_blocked_providers(self) -> None:
        admin, _ = self.admin()
        late = self.provider(nombre="Vencido", fecha_proxima_evaluacion=YESTERDAY)
        due = self.provider(nombre="Hoy", fecha_proxima_evaluacion=TODAY)
        unscheduled = self.provider(nombre="Sin fecha")
        blocked = self.provider(nombre="Bloqueado", fecha_proxima_evaluacion=YESTERDAY)
        pv().block(db.session, admin, blocked.id, "Calidad", today=TODAY)
        db.session.commit()
        who = actor(OPERATIVO)
        self.assertEqual([late.id], self.ids(pv().providers.list_(
            db.session, who, evaluacion_vencida=True, today=TODAY)))
        self.assertEqual(sorted([due.id, unscheduled.id, blocked.id]), sorted(self.ids(
            pv().providers.list_(db.session, who, evaluacion_vencida=False, today=TODAY))))
        self.assertEqual((True, False, False, False),
                         tuple(pv().evaluacion_vencida(p, today=TODAY)
                               for p in (late, due, unscheduled, blocked)))
        with self.assertRaises(ValueError):
            pv().providers.list_(db.session, who, evaluacion_vencida=True)

    def test_ultimo_resultado_reads_the_latest_evaluation(self) -> None:
        ana = self.person()
        recovered = self.provider(nombre="Recuperado")
        self.evaluate(recovered.id, ana, fecha=date(2026, 3, 1), scores=BAD)
        self.evaluate(recovered.id, ana, fecha=date(2026, 9, 1), scores=GOOD)
        worse = self.provider(nombre="Empeora")
        self.evaluate(worse.id, ana, fecha=date(2026, 9, 1), scores=GOOD)
        self.evaluate(worse.id, ana, fecha=date(2026, 9, 1), scores=scores_for(12))
        self.provider(nombre="Sin evaluar")
        who = actor(OPERATIVO)
        for value, expected in (("apto", [recovered]), (ResultadoEvaluacion.condicionado, [worse]),
                                ("no_apto", [])):
            with self.subTest(ultimo_resultado=value):
                self.assertEqual(self.ids(expected), self.ids(pv().providers.list_(
                    db.session, who, ultimo_resultado=value)))

    def test_bad_filter_values_are_refused(self) -> None:
        who = actor(OPERATIVO)
        for filters in ({"estado": "Aprobado"}, {"tipo_suministro": "material"},
                        {"ultimo_resultado": "regular"},
                        {"evaluacion_vencida": "si", "today": TODAY}):
            with self.subTest(filters=filters):
                key = next(iter(filters))
                self.assertIn(key, self.refused(errors().ValidationError, pv().providers.list_,
                                                who, **filters))

    def test_evaluations_filter_by_provider_and_evaluator_newest_first(self) -> None:
        ana, luis = self.person(), self.person("Luis Gil")
        aceros, otro = self.provider(), self.provider(nombre="Otro")
        first = self.evaluate(aceros.id, ana, fecha=date(2026, 3, 1))
        second = self.evaluate(aceros.id, luis, fecha=date(2026, 9, 1))
        third = self.evaluate(otro.id, ana, fecha=date(2026, 9, 1))
        who = actor(OPERATIVO)
        for filters, expected in (({}, [third, second, first]),
                                  ({"proveedor_id": aceros.id}, [second, first]),
                                  ({"evaluador_id": ana}, [third, first]),
                                  ({"proveedor_id": aceros.id, "evaluador_id": ana}, [first]),
                                  ({"proveedor_id": "x"}, [])):
            with self.subTest(filters=filters):
                self.assertEqual(self.ids(expected),
                                 self.ids(pv().evaluations.list_(db.session, who, **filters)))
        page, total = pv().evaluations.list_page(db.session, who, proveedor_id=aceros.id,
                                                 per_page=1)
        self.assertEqual(([second.id], 2), (self.ids(page), total))


class PerformanceAttentionTestCase(Helpers, ServiceBase):
    def test_performance_counts_evaluations_average_latest_result_and_nonconformities(
            self) -> None:
        ana = self.person()
        provider, other = self.provider(), self.provider(nombre="Otro")
        self.evaluate(provider.id, ana, fecha=date(2026, 3, 1), scores=scores_for(20))
        self.evaluate(provider.id, ana, fecha=date(2026, 9, 1), scores=BAD)
        self.evaluate(other.id, ana, scores=GOOD)
        for estado in (EstadoNoConformidad.abierta, EstadoNoConformidad.en_verificacion,
                       EstadoNoConformidad.cerrada, EstadoNoConformidad.cancelada):
            self.nc(provider.id, estado)
        self.nc(other.id)
        self.nc(None)
        found = pv().performance(db.session, actor(OPERATIVO), provider.id)
        # (5.00 + 2.25) / 2 = 3.625, rounded half up.
        self.assertEqual(pv().Performance(evaluaciones=2, puntuacion_media=Decimal("3.63"),
                                          ultima_puntuacion=Decimal("2.25"),
                                          ultimo_resultado=ResultadoEvaluacion.no_apto,
                                          no_conformidades=4, no_conformidades_abiertas=2),
                         found)
        self.assertEqual(Decimal("3.63"), pv().average_score(db.session, provider.id))
        empty = pv().performance(db.session, actor(OPERATIVO), self.provider(nombre="Nuevo").id)
        self.assertEqual((0, None, None, None, 0, 0),
                         (empty.evaluaciones, empty.puntuacion_media, empty.ultima_puntuacion,
                          empty.ultimo_resultado, empty.no_conformidades,
                          empty.no_conformidades_abiertas))
        self.refused(errors().NotFound, pv().performance, actor(OPERATIVO), 999)
        self.refused(errors().PermissionDenied, pv().performance,
                     actor(OPERATIVO, scopes={"write"}), provider.id)

    def test_attention_flags_overdue_blocked_with_processes_and_approved_not_suitable(
            self) -> None:
        admin, _ = self.admin()
        ana, compras = self.person(), self.process()
        late = self.provider(nombre="B vencido", fecha_proxima_evaluacion=YESTERDAY)
        later = self.provider(nombre="A vencido",
                              fecha_proxima_evaluacion=TODAY - timedelta(days=9))
        self.provider(nombre="Al día", fecha_proxima_evaluacion=TODAY)
        blocked_linked = self.provider(nombre="Bloqueado", procesos=[compras],
                                       fecha_proxima_evaluacion=YESTERDAY)
        blocked_free = self.provider(nombre="Bloqueado libre")
        for found in (blocked_linked, blocked_free):
            pv().block(db.session, admin, found.id, "Calidad", today=TODAY)
        unsuitable = self.provider(nombre="No apto")
        recovered = self.provider(nombre="Recuperado")
        conditioned = self.provider(nombre="Condicionado")
        for found in (unsuitable, recovered):
            pv().approve(db.session, admin, found.id, today=TODAY)
        pv().condition(db.session, admin, conditioned.id, "Plazos", today=TODAY)
        db.session.commit()
        for found in (unsuitable, recovered, conditioned):
            self.evaluate(found.id, ana, fecha=date(2026, 3, 1), scores=BAD)
        self.evaluate(recovered.id, ana, fecha=date(2026, 9, 1), scores=GOOD)
        flagged = pv().attention(db.session, actor(OPERATIVO), today=TODAY)
        self.assertEqual({"evaluaciones_vencidas": [later.id, late.id],
                          "bloqueados_con_procesos": [blocked_linked.id],
                          "aprobados_no_aptos": [unsuitable.id]},
                         {key: self.ids(value) for key, value in flagged.items()})
        self.refused(errors().PermissionDenied, pv().attention, actor(OPERATIVO, scopes={"write"}),
                     today=TODAY)


class DeleteTestCase(Helpers, ServiceBase):
    def test_a_provider_with_evaluations_or_nonconformities_is_not_deleted(self) -> None:
        ana = self.person()
        evaluated, cited = self.provider(), self.provider(nombre="Otro")
        self.evaluate(evaluated.id, ana)
        self.nc(cited.id)
        for found, expected in ((evaluated, pv().HAS_EVALUATIONS),
                                (cited, pv().HAS_NONCONFORMITIES)):
            with self.subTest(provider=found.nombre):
                self.assertEqual(expected, self.refused(errors().Conflict, pv().providers.delete,
                                                        CLI, found.id))
        self.assertEqual(2, db.session.query(Proveedor).count())

    def test_deleting_a_provider_removes_its_process_links_and_keeps_them_in_the_audit_row(
            self) -> None:
        compras, ventas = self.process("Compras"), self.process("Ventas")
        provider = self.provider(procesos=[ventas, compras])
        pv().providers.delete(db.session, actor(ADMIN), provider.id)
        db.session.commit()
        self.assertEqual(([], 0, 2), (self.links(), db.session.query(Proveedor).count(),
                                      db.session.query(ProcesoOperacion).count()))
        row = self.rows("proveedores")[-1]
        self.assertEqual(("delete", [compras, ventas], "Aceros del Norte"),
                         (row.action, row.before["procesos"], row.before["nombre"]))

    def test_people_cited_as_decider_or_evaluator_are_not_deleted(self) -> None:
        admin, decider = self.admin()
        ana = self.person()
        provider = self.provider()
        pv().approve(db.session, admin, provider.id, today=TODAY)
        db.session.commit()
        self.evaluate(provider.id, ana)
        for person_id in (decider, ana):
            with self.subTest(person=person_id):
                self.assertEqual(people().STILL_REFERENCED, self.refused(
                    errors().Conflict, people().delete, CLI, person_id))
        self.assertEqual(2, db.session.query(Person).count())

    def test_a_process_linked_to_a_provider_is_not_deleted(self) -> None:
        compras = self.process()
        provider = self.provider(procesos=[compras])
        self.assertEqual(po().LINKED_TO_PROVIDERS,
                         self.refused(errors().Conflict, po().delete, CLI, compras))
        pv().providers.update(db.session, CLI, provider.id, {"procesos": []})
        po().delete(db.session, CLI, compras)
        db.session.commit()
        self.assertEqual(0, db.session.query(ProcesoOperacion).count())

    def test_unknown_providers_and_evaluations_are_not_found(self) -> None:
        for call, expected in ((pv().providers.delete, pv().PROVIDER_NOT_FOUND),
                               (pv().evaluations.delete, pv().EVALUATION_NOT_FOUND),
                               (pv().providers.update, pv().PROVIDER_NOT_FOUND)):
            for record_id in (999, 2 ** 40, "7", None):
                with self.subTest(call=call.__name__, record_id=record_id):
                    args = (record_id, {"notas": "x"}) if call is pv().providers.update else (
                        record_id,)
                    self.assertEqual(expected, self.refused(errors().NotFound, call, CLI, *args))


class DatabaseCases:
    """The schema guards the provider rules itself; SQL filters run on each engine."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_schema_guards_decisions_scores_dates_and_links(self) -> None:
        self.enforce_foreign_keys()
        ana, compras = self.person(), self.process()
        provider = self.provider(nif="B12345678", procesos=[compras]).id
        self.evaluate(provider, ana)
        self.insert(NoConformidad.__table__, descripcion="Retraso",
                    fecha_detectada=date(2026, 10, 1), estado=EstadoNoConformidad.abierta,
                    origen=OrigenNoConformidad.cliente)
        decided = "fecha_estado = '2026-10-01'"
        for statement in (
            f"UPDATE proveedores SET {decided}",  # a pending provider has no decision
            "UPDATE proveedores SET decidido_por_id = :ana",
            "UPDATE proveedores SET motivo_estado = 'Bien'",
            "UPDATE proveedores SET estado = 'aprobado'",  # a decision has a date
            f"UPDATE proveedores SET estado = 'condicionado', {decided}",  # and here a reason
            f"UPDATE proveedores SET estado = 'bloqueado', {decided}",
            f"UPDATE proveedores SET estado = 'Aprobado', {decided}",  # the label is not stored
            "UPDATE proveedores SET tipo_suministro = 'material'",
            f"UPDATE proveedores SET estado = 'aprobado', {decided}, decidido_por_id = 999",
            "INSERT INTO proveedores (nombre, tipo_suministro, estado) "
            "VALUES ('Aceros del Norte', 'servicio', 'pendiente')",
            "INSERT INTO proveedores (nombre, nif, tipo_suministro, estado) "
            "VALUES ('Otro', 'B12345678', 'servicio', 'pendiente')",
            "INSERT INTO proveedor_procesos (proveedor_id, proceso_id) VALUES (:p, :c)",
            "INSERT INTO proveedor_procesos (proveedor_id, proceso_id) VALUES (:p, 999)",
            "INSERT INTO proveedor_procesos (proveedor_id, proceso_id) VALUES (999, :c)",
            "UPDATE evaluaciones_proveedor SET calidad = 0",
            "UPDATE evaluaciones_proveedor SET plazo = 6",
            "UPDATE evaluaciones_proveedor SET servicio = NULL",
            "UPDATE evaluaciones_proveedor SET documentacion = 9",
            "UPDATE evaluaciones_proveedor SET fecha_proxima = fecha",
            "UPDATE evaluaciones_proveedor SET evaluador_id = 999",
            "UPDATE evaluaciones_proveedor SET proveedor_id = 999",
            "UPDATE no_conformidades SET proveedor_id = :p",  # origin cliente
            "UPDATE no_conformidades SET proveedor_id = 999, origen = 'proveedor'",
        ):
            with self.subTest(refused=statement):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement), {"ana": ana, "p": provider, "c": compras})
                    db.session.commit()
                db.session.rollback()
        for statement in (
            f"UPDATE proveedores SET estado = 'aprobado', {decided}",
            f"UPDATE proveedores SET estado = 'bloqueado', {decided}, motivo_estado = 'Calidad', "
            "decidido_por_id = :ana",
            "UPDATE no_conformidades SET proveedor_id = :p, origen = 'proveedor'",
            "INSERT INTO proveedores (nombre, tipo_suministro, estado) "
            "VALUES ('Sin NIF 1', 'servicio', 'pendiente'), ('Sin NIF 2', 'servicio', 'pendiente')",
        ):
            with self.subTest(accepted=statement):
                db.session.execute(text(statement), {"ana": ana, "p": provider})
                db.session.commit()

    def test_foreign_keys_refuse_deletes_the_checks_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        admin, decider = self.admin()
        ana, compras = self.person(), self.process()
        evaluated = self.provider().id
        self.evaluate(evaluated, ana)
        cited = self.provider(nombre="Pinturas", procesos=[compras]).id
        self.nc(cited)
        pv().approve(db.session, admin, cited, today=TODAY)
        db.session.commit()
        named = self.names_violated_constraints
        cases = (
            (pv(), "_has_evaluations", pv().providers.delete, evaluated,
             pv().HAS_EVALUATIONS if named else pv().DELETE_CONFLICT),
            (pv(), "_has_nonconformities", pv().providers.delete, cited,
             pv().HAS_NONCONFORMITIES if named else pv().DELETE_CONFLICT),
            (po(), "_linked_to_providers", po().delete, compras,
             po().LINKED_TO_PROVIDERS if named else errors().Conflict.default_message),
            (people(), "_is_referenced", people().delete, ana, people().STILL_REFERENCED),
            (people(), "_is_referenced", people().delete, decider, people().STILL_REFERENCED),
        )
        for module, check, delete, record_id, expected in cases:
            with self.subTest(check=check, record=record_id):
                with patch.object(module, check, return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        delete(db.session, CLI, record_id)
                db.session.rollback()
                self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                self.assertEqual(expected, caught.exception.message)
        self.assertEqual((2, 1, 2), (db.session.query(Proveedor).count(),
                                     db.session.query(ProcesoOperacion).count(),
                                     db.session.query(Person).count()))

    def test_the_computed_filters_and_attention_run_in_the_database(self) -> None:
        self.enforce_foreign_keys()
        admin, _ = self.admin()
        ana = self.person()
        by_total = {}
        for total in range(4, 21):
            found = self.provider(nombre=f"Total {total:02d}")
            self.evaluate(found.id, ana, scores=scores_for(total))
            by_total[total] = found.id
        who = actor(OPERATIVO, user_id=None)
        for member in ResultadoEvaluacion:
            with self.subTest(ultimo_resultado=member.name):
                expected = [by_total[total] for total in sorted(by_total)
                            if pv().resultado(Decimal(total) / 4) is member]
                self.assertEqual(expected, self.ids(pv().providers.list_(
                    db.session, who, ultimo_resultado=member.name)))
        for name, totals in (("apto", range(14, 21)), ("condicionado", range(10, 14)),
                             ("no_apto", range(4, 10))):
            with self.subTest(band=name):
                self.assertEqual([by_total[t] for t in totals], self.ids(pv().providers.list_(
                    db.session, who, ultimo_resultado=name)))
        late = self.provider(nombre="Vencido", fecha_proxima_evaluacion=YESTERDAY).id
        pv().approve(db.session, admin, by_total[9], today=TODAY)
        db.session.commit()
        self.assertEqual([late], self.ids(pv().providers.list_(
            db.session, who, evaluacion_vencida=True, today=TODAY)))
        flagged = pv().attention(db.session, who, today=TODAY)
        self.assertEqual(([late], [], [by_total[9]]),
                         tuple(self.ids(flagged[key]) for key in (
                             "evaluaciones_vencidas", "bloqueados_con_procesos",
                             "aprobados_no_aptos")))
        self.assertEqual(Decimal("2.25"), pv().average_score(db.session, by_total[9]))


class SqliteDatabaseTestCase(DatabaseCases, Helpers, ServiceBase):
    names_violated_constraints = False

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


class McpTestCase(McpDbCase):
    async def test_providers_and_evaluations_round_trip_with_their_computed_values(self) -> None:
        ana = self.seed("personas")
        compras = self.seed("procesos")
        who = mcp_actor(AUDITOR)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "proveedores", "data": {
                "nombre": "Aceros del Norte", "nif": "b-1234 5678",
                "tipo_suministro": "proceso_externalizado", "email": "Compras@Aceros.example",
                "procesos": [compras], "fecha_proxima_evaluacion": "2026-10-17"}})
            self.assertFalse(created.is_error, created.content)
            record = created.structured_content
            self.assertEqual(
                ("Pendiente", "B12345678", "Proceso externalizado", "compras@aceros.example",
                 [compras], True, None, None),
                tuple(record[k] for k in ("estado", "nif", "tipo_suministro", "email",
                                          "procesos", "evaluacion_vencida", "ultimo_resultado",
                                          "puntuacion_media")))
            evaluation = await self.call(who, "qms_create", {
                "module": "evaluaciones_proveedor", "data": {
                    "proveedor_id": record["id"], "fecha": "2026-10-10", "evaluador_id": ana,
                    **BAD, "fecha_proxima": "2027-04-10"}})
            self.assertFalse(evaluation.is_error, evaluation.content)
            self.assertEqual(("2.25", "No apto"), (evaluation.structured_content["puntuacion"],
                                                   evaluation.structured_content["resultado"]))
            future = await self.call(who, "qms_create", {
                "module": "evaluaciones_proveedor", "data": {
                    "proveedor_id": record["id"], "fecha": "2026-10-19", "evaluador_id": ana,
                    **GOOD}})
            self.assertTrue(future.is_error)
            self.assertIn("futura", future.content[0].text)
            got = (await self.call(who, "qms_get", {"module": "proveedores",
                                                    "id": record["id"]})).structured_content
            self.assertEqual(("No apto", "2.25", False, "2027-04-10"),
                             tuple(got[k] for k in ("ultimo_resultado", "puntuacion_media",
                                                    "evaluacion_vencida",
                                                    "fecha_proxima_evaluacion")))
            supplier_nc = await self.call(who, "qms_create", {
                "module": "no_conformidades", "data": {
                    "descripcion": "Lote defectuoso", "fecha_detectada": "2026-10-11",
                    "origen": "proveedor", "proveedor_id": record["id"]}})
            self.assertFalse(supplier_nc.is_error, supplier_nc.content)
            self.assertEqual(record["id"], supplier_nc.structured_content["proveedor_id"])
            customer_nc = await self.call(who, "qms_create", {
                "module": "no_conformidades", "data": {
                    "descripcion": "Reclamación", "fecha_detectada": "2026-10-11",
                    "origen": "cliente", "proveedor_id": record["id"]}})
            self.assertTrue(customer_nc.is_error)
            for slug, filters, total in (
                ("proveedores", {"ultimo_resultado": "no_apto", "estado": "pendiente",
                                 "tipo_suministro": "proceso_externalizado",
                                 "proceso_id": compras, "nombre": "aceros"}, 1),
                ("proveedores", {"evaluacion_vencida": True}, 0),
                ("proveedores", {"ultimo_resultado": "apto"}, 0),
                ("evaluaciones_proveedor", {"proveedor_id": record["id"], "evaluador_id": ana}, 1),
                ("no_conformidades", {"proveedor_id": record["id"]}, 1),
            ):
                with self.subTest(slug=slug, filters=filters):
                    listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                             {"module": slug, "filters": filters})
                    self.assertFalse(listed.is_error, listed.content)
                    self.assertEqual(total, listed.structured_content["total"])
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        fields = {f["name"]: f for f in described["proveedores"]["fields"]}
        self.assertEqual(("integer_list", ["producto", "servicio", "proceso_externalizado"], True),
                         (fields["procesos"]["type"], fields["tipo_suministro"]["allowed"],
                          fields["nombre"]["required"]))
        self.assertFalse({"estado", "fecha_estado", "decidido_por_id", "motivo_estado"}
                         & set(fields))
        self.assertEqual({"nombre", "estado", "tipo_suministro", "proceso_id",
                          "evaluacion_vencida", "ultimo_resultado"},
                         {f["name"] for f in described["proveedores"]["filters"]})
        scores = {f["name"]: f for f in described["evaluaciones_proveedor"]["fields"]}
        self.assertEqual({"proveedor_id", "fecha", "evaluador_id", "calidad", "plazo", "servicio",
                          "documentacion"}, {name for name, f in scores.items() if f["required"]})
        self.assertEqual("integer", {f["name"]: f for f in described["no_conformidades"]["fields"]}
                         ["proveedor_id"]["type"])

    async def test_the_state_is_never_written_and_nothing_is_deleted_through_mcp(self) -> None:
        record_id = self.seed("proveedores")
        for name, arguments in (
            ("qms_create", {"module": "proveedores", "data": PROVIDER | {
                "nombre": "Otro", "estado": "aprobado"}}),
            ("qms_update", {"module": "proveedores", "id": record_id,
                            "data": {"estado": "aprobado", "fecha_estado": "2026-10-01"}}),
        ):
            with self.subTest(tool=name):
                result = await self.call(mcp_actor(ADMIN), name, arguments)
                self.assertTrue(result.is_error)
                self.assertIn("Campos no permitidos", result.content[0].text)
        denied = await self.call(mcp_actor(OPERATIVO), "qms_create",
                                 {"module": "proveedores", "data": PROVIDER | {"nombre": "Otro"}})
        self.assertTrue(denied.is_error)
        self.assertIn("No tienes permiso", denied.content[0].text)
        tools = {tool.name for tool in await self.list_tools()}
        self.assertFalse({name for name in tools if "delete" in name or "approve" in name})
        db.session.expire_all()
        self.assertEqual((1, EstadoProveedor.pendiente),
                         (db.session.query(Proveedor).count(),
                          db.session.get(Proveedor, record_id).estado))


if __name__ == "__main__":
    unittest.main()
