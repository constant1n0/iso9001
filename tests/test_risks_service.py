"""Risks, opportunities and their actions: the services (RO-2 of ``risks-opportunities``).

ISO 9001 clause 6.1, decisions RO1 to RO4 and RO6. A risk is assessed by
likelihood and impact, before and after its actions; an opportunity by benefit
and feasibility. Each pair is complete or still pending, the residual pair
needs the first one, an owner is required on new records, the next review
falls on or after the identification date, which defaults to ``today`` and is
never in the future. Actions belong to exactly one open risk or opportunity
and never move; a completed one records when, and only then is its
effectiveness evaluated, on or after the completion and never in the future.
A record with actions is never deleted. ``attention`` gathers what the RO-3
dashboard flags. The SQLite cases run with the audit flush guard.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import AuditLog, ParteInteresada, Person, ProcesoOperacion, RoleEnum
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid without a users row.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
TODAY = date(2026, 10, 17)
RISK = {"titulo": "Rotación de personal", "descripcion": "Salida de técnicos clave",
        "origen": "interno"}
OPPORTUNITY = {"titulo": "Nuevo mercado", "descripcion": "Clientes del sector naval",
               "origen": "externo"}
ACTION = {"descripcion": "Plan de carrera", "fecha_limite": date(2026, 12, 31)}


def ro():
    from app.services import risks

    return risks


def models():
    import app.models as module

    return module


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        return self.insert(Person.__table__, nombre=nombre, activo=activo)

    def risk(self, who: Actor = CLI, **overrides):
        overrides.setdefault("responsable_id", self.person())
        found = ro().risks.create(db.session, who, RISK | overrides, today=TODAY)
        db.session.commit()
        return found

    def opportunity(self, who: Actor = CLI, **overrides):
        overrides.setdefault("responsable_id", self.person())
        found = ro().opportunities.create(db.session, who, OPPORTUNITY | overrides, today=TODAY)
        db.session.commit()
        return found

    def action(self, who: Actor = CLI, **overrides):
        overrides.setdefault("responsable_id", self.person("Eva Gil"))
        found = ro().actions.create(db.session, who, ACTION | overrides, today=TODAY)
        db.session.commit()
        return found

    def edit(self, register: str, record_id: int, data: dict, who: Actor = CLI):
        found = getattr(ro(), register).update(db.session, who, record_id, data, today=TODAY)
        db.session.commit()
        return found

    def migrated(self, **values) -> int:
        """A risk as ``c6e8a2f4b1d9`` copied it: no owner and no assessment."""
        return self.insert(models().Riesgo.__table__, **{
            "titulo": "Fallo de proveedor", "descripcion": "Fallo de proveedor",
            "origen": "interno", "fecha_identificacion": date(2026, 3, 1)} | values)

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message


class RiskWriteTestCase(Helpers, ServiceBase):
    def test_create_stores_every_field_with_defaults_stamps_and_audit(self) -> None:
        process_id = self.insert(ProcesoOperacion.__table__, proceso="Compras")
        party_id = self.insert(ParteInteresada.__table__, nombre="Clientes")
        created = self.risk(actor(AUDITOR), proceso_id=process_id, parte_interesada_id=party_id,
                            probabilidad=4, impacto=5, tratamiento="mitigar",
                            probabilidad_residual=2, impacto_residual=2,
                            fecha_revision=date(2027, 1, 15), notas="Revisar con RR. HH.")
        m = models()
        self.assertEqual(
            (m.EstadoRiesgo.identificado, TODAY, m.NivelRiesgo.critico, m.NivelRiesgo.bajo,
             m.TratamientoRiesgo.mitigar, m.OrigenRiesgo.interno, process_id, party_id),
            (created.estado, created.fecha_identificacion, created.nivel, created.nivel_residual,
             created.tratamiento, created.origen, created.proceso_id,
             created.parte_interesada_id))
        self.assertEqual((7, 7), (created.created_by_id, created.updated_by_id))
        (row,) = self.rows("riesgos")
        self.assertEqual(("create", created.id), (row.action, row.entity_id))
        self.assertEqual(("Identificado", "Mitigar", "Interno", "2026-10-17"),
                         tuple(row.after[k] for k in ("estado", "tratamiento", "origen",
                                                      "fecha_identificacion")))
        given = self.risk(fecha_identificacion=date(2026, 1, 2))
        self.assertEqual((date(2026, 1, 2), None), (given.fecha_identificacion, given.nivel))

    def test_an_owner_is_required_on_create_but_migrated_rows_may_lack_one(self) -> None:
        create = ro().risks.create
        message = self.refused(errors().ValidationError, create, CLI, RISK, today=TODAY)
        self.assertEqual("Faltan campos obligatorios: responsable_id.", message)
        self.refused(errors().ValidationError, create, CLI, RISK | {"responsable_id": None},
                     today=TODAY)
        self.assertIn("ninguna persona", self.refused(
            errors().ValidationError, create, CLI, RISK | {"responsable_id": 999}, today=TODAY))
        inactive = self.person("Baja", activo=False)
        self.refused(errors().ValidationError, create, CLI,
                     RISK | {"responsable_id": inactive}, today=TODAY)
        legacy = self.migrated()
        self.assertEqual("Nuevo título", self.edit("risks", legacy, {"titulo": "Nuevo título"})
                         .titulo)
        self.assertIsNone(db.session.get(models().Riesgo, legacy).responsable_id)
        self.refused(errors().ValidationError, ro().risks.update, CLI, legacy,
                     {"responsable_id": None}, today=TODAY)
        self.assertIn("ningún proceso", self.refused(
            errors().ValidationError, ro().risks.update, CLI, legacy, {"proceso_id": 999},
            today=TODAY))
        self.assertIn("ninguna parte interesada", self.refused(
            errors().ValidationError, ro().risks.update, CLI, legacy,
            {"parte_interesada_id": 999}, today=TODAY))

    def test_scales_come_in_pairs_from_one_to_five(self) -> None:
        create = ro().risks.create
        owner = self.person()
        for bad in ({"probabilidad": 3}, {"impacto": 3, "probabilidad": None},
                    {"probabilidad_residual": 2, "impacto_residual": None,
                     "probabilidad": 3, "impacto": 3}):
            with self.subTest(bad=bad):
                message = self.refused(errors().ValidationError, create, CLI,
                                       RISK | {"responsable_id": owner} | bad, today=TODAY)
                self.assertIn("a la vez", message)
        for value in (0, 6, True, "3", 2.5):
            with self.subTest(value=value):
                message = self.refused(errors().ValidationError, create, CLI, RISK | {
                    "responsable_id": owner, "probabilidad": value, "impacto": 3}, today=TODAY)
                self.assertEqual(
                    "El campo «probabilidad» debe ser un número entero del 1 al 5.", message)
        found = self.risk(probabilidad=3, impacto=3)
        self.refused(errors().ValidationError, ro().risks.update, CLI, found.id,
                     {"impacto": None}, today=TODAY)
        self.assertEqual(models().NivelRiesgo.alto,
                         self.edit("risks", found.id, {"impacto": 4}).nivel)
        self.assertIsNone(self.edit("risks", found.id,
                                    {"probabilidad": None, "impacto": None}).nivel)

    def test_residual_values_need_an_assessment(self) -> None:
        message = self.refused(errors().ValidationError, ro().risks.create, CLI, RISK | {
            "responsable_id": self.person(), "probabilidad_residual": 1,
            "impacto_residual": 1}, today=TODAY)
        self.assertEqual(ro().RESIDUAL_WITHOUT_ASSESSMENT, message)
        found = self.risk(probabilidad=4, impacto=4, probabilidad_residual=2,
                          impacto_residual=2)
        self.assertEqual(ro().RESIDUAL_WITHOUT_ASSESSMENT, self.refused(
            errors().ValidationError, ro().risks.update, CLI, found.id,
            {"probabilidad": None, "impacto": None}, today=TODAY))
        cleared = self.edit("risks", found.id, {"probabilidad": None, "impacto": None,
                                                "probabilidad_residual": None,
                                                "impacto_residual": None})
        self.assertEqual((None, None), (cleared.nivel, cleared.nivel_residual))

    def test_the_review_follows_the_identification_which_is_never_in_the_future(self) -> None:
        create = ro().risks.create
        owner = self.person()
        message = self.refused(errors().ValidationError, create, CLI, RISK | {
            "responsable_id": owner, "fecha_identificacion": date(2026, 10, 18)}, today=TODAY)
        self.assertEqual(ro().IDENTIFIED_IN_FUTURE, message)
        message = self.refused(errors().ValidationError, create, CLI, RISK | {
            "responsable_id": owner, "fecha_revision": date(2026, 10, 16)}, today=TODAY)
        self.assertEqual(ro().REVIEW_BEFORE_IDENTIFICATION, message)
        found = self.risk(fecha_identificacion=date(2026, 9, 1), fecha_revision=date(2026, 9, 1))
        self.assertEqual(ro().REVIEW_BEFORE_IDENTIFICATION, self.refused(
            errors().ValidationError, ro().risks.update, CLI, found.id,
            {"fecha_identificacion": date(2026, 9, 2)}, today=TODAY))
        self.refused(errors().ValidationError, ro().risks.update, CLI, found.id,
                     {"fecha_identificacion": None}, today=TODAY)
        self.assertIsNone(self.edit("risks", found.id, {"fecha_revision": None}).fecha_revision)

    def test_unknown_keys_and_bad_choices_are_refused_before_any_lookup(self) -> None:
        for register in ("risks", "opportunities", "actions"):
            service = getattr(ro(), register)
            with self.subTest(register=register):
                self.assertEqual("Campos no permitidos: nivel.", self.refused(
                    errors().ValidationError, service.update, CLI, 999, {"nivel": "alto"},
                    today=TODAY))
                self.assertEqual("Campos no permitidos: nivel.", self.refused(
                    errors().ValidationError, service.create, CLI, {"nivel": "alto"},
                    today=TODAY))
        found = self.risk()
        for data in ({"origen": "Interno"}, {"estado": "Cerrado"}, {"tratamiento": "compartir"},
                     {"titulo": " "}, {"titulo": "x" * 201}, {"descripcion": ""}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, ro().risks.update, CLI, found.id, data,
                             today=TODAY)
        self.assertEqual("Riesgo no encontrado.", self.refused(
            errors().NotFound, ro().risks.update, CLI, 999, {"titulo": "X"}, today=TODAY))
        self.assertEqual("Oportunidad no encontrada.", self.refused(
            errors().NotFound, ro().opportunities.get, CLI, 999))

    def test_updates_audit_only_changed_fields_and_a_no_op_writes_nothing(self) -> None:
        found = self.risk(probabilidad=2, impacto=2)
        self.edit("risks", found.id, {"titulo": RISK["titulo"], "probabilidad": 2})
        self.edit("risks", found.id, {"estado": "en_tratamiento", "titulo": RISK["titulo"]})
        create, update = self.rows("riesgos")
        self.assertEqual(("create", "update"), (create.action, update.action))
        self.assertEqual(("Identificado", "En tratamiento"),
                         (update.before["estado"], update.after["estado"]))

    def test_a_record_with_actions_is_never_deleted(self) -> None:
        risk, opportunity = self.risk(), self.opportunity()
        risk_action = self.action(riesgo_id=risk.id)
        self.action(oportunidad_id=opportunity.id)
        self.assertEqual(ro().RISK_HAS_ACTIONS, self.refused(
            errors().Conflict, ro().risks.delete, CLI, risk.id))
        self.assertEqual(ro().OPPORTUNITY_HAS_ACTIONS, self.refused(
            errors().Conflict, ro().opportunities.delete, CLI, opportunity.id))
        ro().actions.delete(db.session, CLI, risk_action.id)
        ro().risks.delete(db.session, CLI, risk.id)
        db.session.commit()
        self.assertIsNone(db.session.get(models().Riesgo, risk.id))
        self.assertEqual(["create", "delete"], [r.action for r in self.rows("riesgos")])
        self.assertEqual("delete", self.rows("acciones_riesgo")[-1].action)

    def test_the_actions_key_refuses_a_delete_the_check_did_not_see(self) -> None:
        risk = self.risk()
        self.action(riesgo_id=risk.id)
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())
        with patch.object(ro(), "_has_actions", return_value=False):
            message = self.refused(errors().Conflict, ro().risks.delete, CLI, risk.id)
        # SQLite does not name the key, so the generic message stands.
        self.assertEqual(ro().DELETE_CONFLICT, message)
        self.assertIsNotNone(db.session.get(models().Riesgo, risk.id))


class OpportunityWriteTestCase(Helpers, ServiceBase):
    def test_an_opportunity_has_a_priority_a_decision_and_no_residual_pair(self) -> None:
        created = self.opportunity(beneficio=3, viabilidad=5, decision="aprovechar",
                                   fecha_revision=date(2026, 12, 1))
        m = models()
        self.assertEqual((m.EstadoOportunidad.identificada, m.PrioridadOportunidad.alta,
                          m.DecisionOportunidad.aprovechar, TODAY),
                         (created.estado, created.prioridad, created.decision,
                          created.fecha_identificacion))
        self.assertEqual("Campos no permitidos: probabilidad_residual.", self.refused(
            errors().ValidationError, ro().opportunities.update, CLI, created.id,
            {"probabilidad_residual": 1}, today=TODAY))
        self.assertIn("a la vez", self.refused(
            errors().ValidationError, ro().opportunities.update, CLI, created.id,
            {"beneficio": None}, today=TODAY))
        self.assertEqual(m.PrioridadOportunidad.baja, self.edit(
            "opportunities", created.id, {"beneficio": 1, "viabilidad": 5}).prioridad)
        self.assertEqual(ro().REVIEW_BEFORE_IDENTIFICATION, self.refused(
            errors().ValidationError, ro().opportunities.update, CLI, created.id,
            {"fecha_revision": date(2026, 10, 1)}, today=TODAY))
        self.assertEqual("Faltan campos obligatorios: responsable_id.", self.refused(
            errors().ValidationError, ro().opportunities.create, CLI, OPPORTUNITY, today=TODAY))
        (row, *_) = self.rows("oportunidades")
        self.assertEqual(("create", "Aprovechar"), (row.action, row.after["decision"]))


class ActionTestCase(Helpers, ServiceBase):
    def test_an_action_belongs_to_exactly_one_existing_open_record(self) -> None:
        risk, opportunity = self.risk(), self.opportunity()
        owner = self.person("Eva Gil")
        create = ro().actions.create
        base = ACTION | {"responsable_id": owner}
        for parents in ({}, {"riesgo_id": risk.id, "oportunidad_id": opportunity.id},
                        {"riesgo_id": None, "oportunidad_id": None}):
            with self.subTest(parents=parents):
                self.assertEqual(ro().ONE_PARENT, self.refused(
                    errors().ValidationError, create, CLI, base | parents, today=TODAY))
        self.assertEqual(ro().UNKNOWN_RISK, self.refused(
            errors().ValidationError, create, CLI, base | {"riesgo_id": 999}, today=TODAY))
        self.assertEqual(ro().UNKNOWN_OPPORTUNITY, self.refused(
            errors().ValidationError, create, CLI, base | {"oportunidad_id": 999}, today=TODAY))
        created = self.action(riesgo_id=risk.id)
        self.assertEqual((models().EstadoAccionRiesgo.pendiente, risk.id, None),
                         (created.estado, created.riesgo_id, created.oportunidad_id))
        self.assertEqual("create", self.rows("acciones_riesgo")[0].action)
        self.edit("risks", risk.id, {"estado": "cerrado"})
        self.assertEqual(ro().RISK_CLOSED, self.refused(
            errors().ValidationError, create, CLI, base | {"riesgo_id": risk.id}, today=TODAY))
        for estado in ("descartada", "aprovechada"):
            self.edit("opportunities", opportunity.id, {"estado": estado})
            with self.subTest(estado=estado):
                self.assertEqual(ro().OPPORTUNITY_CLOSED, self.refused(
                    errors().ValidationError, create, CLI,
                    base | {"oportunidad_id": opportunity.id}, today=TODAY))
        # The actions a closed record already has stay editable (their follow-up).
        self.assertEqual("en_curso", self.edit("actions", created.id,
                                               {"estado": "en_curso"}).estado.name)

    def test_an_action_never_moves_to_another_record(self) -> None:
        risk, other, opportunity = self.risk(), self.risk(), self.opportunity()
        found = self.action(riesgo_id=risk.id)
        for data in ({"riesgo_id": other.id}, {"riesgo_id": None},
                     {"oportunidad_id": opportunity.id}):
            with self.subTest(data=data):
                self.assertEqual(ro().PARENT_FIXED, self.refused(
                    errors().ValidationError, ro().actions.update, CLI, found.id, data,
                    today=TODAY))
        self.edit("actions", found.id, {"riesgo_id": risk.id, "oportunidad_id": None})
        self.assertEqual(1, len(self.rows("acciones_riesgo")))  # a no-op writes nothing
        self.assertEqual("Acción no encontrada.", self.refused(
            errors().NotFound, ro().actions.update, CLI, 999, {"descripcion": "X"}, today=TODAY))

    def test_completion_follows_the_checks_and_is_never_in_the_future(self) -> None:
        found = self.action(riesgo_id=self.risk().id)
        update = ro().actions.update
        for data, message in (
            ({"estado": "completada"}, ro().COMPLETION_REQUIRED),
            ({"fecha_completada": date(2026, 10, 1)}, ro().COMPLETION_ONLY_WHEN_DONE),
            ({"estado": "completada", "fecha_completada": date(2026, 10, 18)},
             ro().COMPLETION_IN_FUTURE),
        ):
            with self.subTest(data=data):
                self.assertEqual(message, self.refused(errors().ValidationError, update, CLI,
                                                       found.id, data, today=TODAY))
        self.refused(errors().ValidationError, ro().actions.create, CLI, ACTION | {
            "riesgo_id": found.riesgo_id, "responsable_id": found.responsable_id,
            "estado": "completada"}, today=TODAY)
        done = self.edit("actions", found.id, {"estado": "completada",
                                               "fecha_completada": TODAY})
        self.assertEqual(TODAY, done.fecha_completada)
        self.assertEqual(ro().COMPLETION_ONLY_WHEN_DONE, self.refused(
            errors().ValidationError, update, CLI, found.id, {"estado": "en_curso"},
            today=TODAY))
        self.assertIsNone(self.edit("actions", found.id, {"estado": "en_curso",
                                                          "fecha_completada": None})
                          .fecha_completada)

    def test_effectiveness_is_evaluated_only_after_completion_and_never_ahead(self) -> None:
        found = self.action(riesgo_id=self.risk().id)
        update = ro().actions.update
        evaluated = {"eficacia": "eficaz", "fecha_evaluacion": date(2026, 10, 10)}
        self.assertEqual(ro().EVALUATION_NOT_DONE, self.refused(
            errors().ValidationError, update, CLI, found.id, evaluated, today=TODAY))
        self.edit("actions", found.id, {"estado": "completada",
                                        "fecha_completada": date(2026, 10, 5)})
        for data, message in (
            ({"eficacia": "eficaz"}, ro().EVALUATION_PAIR),
            ({"fecha_evaluacion": date(2026, 10, 10)}, ro().EVALUATION_PAIR),
            (evaluated | {"fecha_evaluacion": date(2026, 10, 4)},
             ro().EVALUATION_BEFORE_COMPLETION),
            (evaluated | {"fecha_evaluacion": date(2026, 10, 18)}, ro().EVALUATION_IN_FUTURE),
            ({"eficacia": "Eficaz", "fecha_evaluacion": date(2026, 10, 10)}, None),
        ):
            with self.subTest(data=data):
                got = self.refused(errors().ValidationError, update, CLI, found.id, data,
                                   today=TODAY)
                if message is not None:
                    self.assertEqual(message, got)
        same_day = self.edit("actions", found.id, evaluated | {
            "fecha_evaluacion": date(2026, 10, 5), "comentario_eficacia": "Sin bajas"})
        self.assertEqual((models().EficaciaAccion.eficaz, "Sin bajas"),
                         (same_day.eficacia, same_day.comentario_eficacia))
        self.assertEqual(ro().EVALUATION_NOT_DONE, self.refused(
            errors().ValidationError, update, CLI, found.id,
            {"estado": "en_curso", "fecha_completada": None}, today=TODAY))
        before_completion = {"fecha_completada": date(2026, 10, 6)}
        self.assertEqual(ro().EVALUATION_BEFORE_COMPLETION, self.refused(
            errors().ValidationError, update, CLI, found.id, before_completion, today=TODAY))


class ListTestCase(Helpers, ServiceBase):
    def test_risk_filters_including_the_computed_ones(self) -> None:
        process_id = self.insert(ProcesoOperacion.__table__, proceso="Compras")
        eva = self.person("Eva Gil")
        low = self.risk(titulo="Bajo", probabilidad=2, impacto=2)  # 4
        medium = self.risk(titulo="Medio", probabilidad=1, impacto=5, origen="externo")  # 5
        high = self.risk(titulo="Alto", probabilidad=4, impacto=4, responsable_id=eva,
                         fecha_identificacion=date(2026, 9, 1),
                         fecha_revision=date(2026, 10, 16))  # 16
        critical = self.risk(titulo="Crítico", probabilidad=4, impacto=5, proceso_id=process_id)
        pending = self.risk(titulo="Sin valorar", fecha_revision=date(2026, 10, 17))
        closed = self.risk(titulo="Cerrado", fecha_identificacion=date(2026, 1, 1),
                           fecha_revision=date(2026, 2, 1))
        self.edit("risks", closed.id, {"estado": "cerrado"})
        listed = ro().risks.list_
        m = models()
        cases = {
            (("nivel", "bajo"),): [low], (("nivel", m.NivelRiesgo.medio),): [medium],
            (("nivel", "alto"),): [high], (("nivel", "critico"),): [critical],
            (("sin_valorar", True),): [pending, closed],
            (("sin_valorar", False),): [low, medium, high, critical],
            (("revision_vencida", True),): [high],
            (("estado", "cerrado"),): [closed], (("origen", "externo"),): [medium],
            (("responsable_id", eva),): [high], (("proceso_id", process_id),): [critical],
            (("proceso_id", 2 ** 40),): [],
            (("nivel", "alto"), ("revision_vencida", False)): [],
        }
        for filters, expected in cases.items():
            with self.subTest(filters=filters):
                self.assertEqual({r.id for r in expected},
                                 {r.id for r in listed(db.session, CLI, **dict(filters),
                                                       today=TODAY)})
        self.assertEqual(6, len(listed(db.session, CLI)))
        self.assertEqual(([closed], 6), ro().risks.list_page(db.session, CLI, page=6,
                                                             per_page=1))
        for filters in ({"nivel": "Alto"}, {"estado": "Cerrado"}, {"origen": "otro"},
                        {"sin_valorar": "si"}, {"revision_vencida": 1}):
            with self.subTest(refused=filters):
                self.refused(errors().ValidationError, listed, CLI, **filters, today=TODAY)
        with self.assertRaises(ValueError):  # the adapter forgot its date
            listed(db.session, CLI, revision_vencida=True)

    def test_the_level_and_priority_bands_follow_the_models(self) -> None:
        m = models()
        self.assertEqual({m.NivelRiesgo.bajo: (1, 4), m.NivelRiesgo.medio: (5, 9),
                          m.NivelRiesgo.alto: (10, 16), m.NivelRiesgo.critico: (20, 25)},
                         ro().LEVEL_RANGES)
        self.assertEqual({m.PrioridadOportunidad.baja: (1, 6),
                          m.PrioridadOportunidad.media: (8, 12),
                          m.PrioridadOportunidad.alta: (15, 25)}, ro().PRIORITY_RANGES)

    def test_opportunity_and_action_filters(self) -> None:
        eva = self.person("Eva Gil")
        low = self.opportunity(beneficio=2, viabilidad=3)  # 6
        medium = self.opportunity(beneficio=2, viabilidad=4, fecha_revision=date(2026, 10, 1),
                                  fecha_identificacion=date(2026, 9, 1))  # 8
        high = self.opportunity(beneficio=5, viabilidad=3, responsable_id=eva)  # 15
        pending = self.opportunity(origen="interno")
        listed = ro().opportunities.list_
        for filters, expected in (
            ({"prioridad": "baja"}, [low]), ({"prioridad": "media"}, [medium]),
            ({"prioridad": models().PrioridadOportunidad.alta}, [high]),
            ({"sin_valorar": True}, [pending]), ({"revision_vencida": True}, [medium]),
            ({"responsable_id": eva}, [high]), ({"origen": "interno"}, [pending]),
            ({"estado": "identificada"}, [low, medium, high, pending]),
        ):
            with self.subTest(filters=filters):
                self.assertEqual({r.id for r in expected},
                                 {r.id for r in listed(db.session, CLI, **filters, today=TODAY)})
        self.refused(errors().ValidationError, listed, CLI, prioridad="critico")
        self.edit("opportunities", medium.id, {"estado": "descartada"})
        self.assertEqual([], listed(db.session, CLI, revision_vencida=True, today=TODAY))
        risk = self.risk()
        late = self.action(riesgo_id=risk.id, fecha_limite=date(2026, 10, 16))
        due = self.action(riesgo_id=risk.id, fecha_limite=TODAY, responsable_id=eva)
        other = self.action(oportunidad_id=high.id, fecha_limite=date(2026, 1, 31))
        self.edit("actions", other.id, {"estado": "cancelada"})
        actions = ro().actions.list_
        for filters, expected in (
            ({"riesgo_id": risk.id}, [late, due]), ({"oportunidad_id": high.id}, [other]),
            ({"estado": "cancelada"}, [other]), ({"responsable_id": eva}, [due]),
            ({"vencida": True}, [late]), ({"vencida": False}, [other, due]),
            ({"riesgo_id": 2 ** 40}, []),
        ):
            with self.subTest(filters=filters):
                self.assertEqual([a.id for a in expected],
                                 [a.id for a in actions(db.session, CLI, **filters, today=TODAY)])
        self.assertTrue(ro().is_overdue(late, today=TODAY))
        self.assertFalse(ro().is_overdue(due, today=TODAY))
        self.refused(errors().ValidationError, actions, CLI, vencida="si", today=TODAY)


    def test_the_screen_filters_by_cell_open_and_high_without_action(self) -> None:
        """The heat map's cell (likelihood and impact), open records and the dashboard's
        high or critical risks without a pending or in-progress action (RO-3)."""
        bare = self.risk(titulo="Crítico", probabilidad=4, impacto=5)
        treated = self.risk(titulo="Tratado", probabilidad=4, impacto=5)
        self.action(riesgo_id=treated.id)
        medium = self.risk(titulo="Medio", probabilidad=3, impacto=3)
        closed = self.risk(titulo="Cerrado", probabilidad=5, impacto=5)
        self.edit("risks", closed.id, {"estado": "cerrado"})
        used = self.opportunity(beneficio=5, viabilidad=5)
        self.edit("opportunities", used.id, {"estado": "aprovechada"})
        waiting = self.opportunity()
        pending = self.risk(titulo="Sin valorar")
        listed = ro().risks.list_
        for filters, expected in (
            ({"probabilidad": 4, "impacto": 5}, [bare, treated]), ({"probabilidad": 3}, [medium]),
            ({"impacto": 5, "abierto": False}, [closed]),
            ({"abierto": True}, [bare, treated, medium, pending]),
            ({"alto_sin_accion": True}, [bare]),
            ({"alto_sin_accion": False}, [treated, medium, closed, pending]),
        ):
            with self.subTest(filters=filters):
                self.assertEqual({r.id for r in expected},
                                 {r.id for r in listed(db.session, CLI, **filters)})
        self.assertEqual([waiting.id], [o.id for o in ro().opportunities.list_(
            db.session, CLI, abierto=True)])
        for filters in ({"probabilidad": 6}, {"impacto": "4"}, {"probabilidad": True},
                        {"abierto": "1"}, {"alto_sin_accion": 1}):
            with self.subTest(refused=filters):
                self.refused(errors().ValidationError, listed, CLI, **filters)

    def test_the_heat_map_counts_open_assessed_risks_by_cell(self) -> None:
        for values in ({"probabilidad": 4, "impacto": 5}, {"probabilidad": 4, "impacto": 5},
                       {"probabilidad": 1, "impacto": 1}, {}):
            self.risk(**values)
        closed = self.risk(probabilidad=4, impacto=5)
        self.edit("risks", closed.id, {"estado": "cerrado"})
        self.assertEqual({(4, 5): 2, (1, 1): 1}, ro().heat_map(db.session, actor(OPERATIVO)))
        self.refused(errors().PermissionDenied, ro().heat_map, actor(scopes={"write"}))


class ComputedTestCase(Helpers, ServiceBase):
    def test_a_review_is_overdue_only_on_an_open_record_after_its_date(self) -> None:
        m = models()
        overdue = ro().revision_vencida
        risk = m.Riesgo(estado=m.EstadoRiesgo.controlado, fecha_revision=date(2026, 10, 16))
        self.assertTrue(overdue(risk, today=TODAY))
        self.assertFalse(overdue(risk, today=date(2026, 10, 16)))
        risk.estado = m.EstadoRiesgo.cerrado
        self.assertFalse(overdue(risk, today=TODAY))
        self.assertFalse(overdue(m.Riesgo(estado=m.EstadoRiesgo.identificado), today=TODAY))
        for estado, expected in ((m.EstadoOportunidad.en_curso, True),
                                 (m.EstadoOportunidad.aprovechada, False),
                                 (m.EstadoOportunidad.descartada, False)):
            with self.subTest(estado=estado):
                self.assertEqual(expected, overdue(m.Oportunidad(
                    estado=estado, fecha_revision=date(2026, 1, 1)), today=TODAY))

    def test_attention_gathers_what_the_dashboard_flags(self) -> None:
        bare = self.risk(titulo="Crítico sin acciones", probabilidad=5, impacto=5)
        done = self.risk(titulo="Alto con acción completada", probabilidad=4, impacto=3)
        self.edit("actions", self.action(riesgo_id=done.id).id,
                  {"estado": "completada", "fecha_completada": TODAY})
        treated = self.risk(titulo="Alto en tratamiento", probabilidad=4, impacto=3)
        self.action(riesgo_id=treated.id)
        closed = self.risk(titulo="Alto cerrado", probabilidad=5, impacto=4,
                           fecha_identificacion=date(2026, 1, 1),
                           fecha_revision=date(2026, 2, 1))
        self.edit("risks", closed.id, {"estado": "cerrado"})
        self.risk(titulo="Medio", probabilidad=3, impacto=3)
        late = self.risk(titulo="Revisión vencida", probabilidad=1, impacto=1,
                         fecha_identificacion=date(2026, 1, 1), fecha_revision=date(2026, 9, 30))
        pending = self.risk(titulo="Sin valorar")
        late_opportunity = self.opportunity(beneficio=1, viabilidad=1,
                                            fecha_identificacion=date(2026, 1, 1),
                                            fecha_revision=date(2026, 10, 1))
        pending_opportunity = self.opportunity()
        discarded = self.opportunity()
        self.edit("opportunities", discarded.id, {"estado": "descartada"})
        found = ro().attention(db.session, actor(OPERATIVO), today=TODAY)
        self.assertEqual({
            "riesgos_altos_sin_accion": [bare.id, done.id],
            "riesgos_revision_vencida": [late.id],
            "oportunidades_revision_vencida": [late_opportunity.id],
            "riesgos_sin_valorar": [pending.id],
            "oportunidades_sin_valorar": [pending_opportunity.id],
        }, {key: [record.id for record in records] for key, records in found.items()})
        self.refused(errors().PermissionDenied, ro().attention, actor(scopes={"write"}),
                     today=TODAY)
        with self.assertRaises(ValueError):
            ro().attention(db.session, CLI, today=None)


class PolicyTestCase(Helpers, ServiceBase):
    def test_every_role_reads_admins_and_auditors_write_admins_delete(self) -> None:
        risk, opportunity = self.risk(), self.opportunity()
        action = self.action(riesgo_id=risk.id)
        cases = {"risks": (risk.id, {"titulo": "X"}), "opportunities": (opportunity.id,
                                                                         {"titulo": "X"}),
                 "actions": (action.id, {"descripcion": "X"})}
        for register, (record_id, change) in cases.items():
            service = getattr(ro(), register)
            for role in RoleEnum:
                with self.subTest(register=register, read=role.name):
                    self.assertEqual(record_id, service.get(db.session, actor(role),
                                                            record_id).id)
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
                    kwargs = {"today": TODAY} if operation in ("create", "update") else {}
                    self.refused(errors().PermissionDenied, getattr(service, operation), who,
                                 *args, **kwargs)
            self.edit(register, record_id, change, actor(AUDITOR))
        ro().actions.delete(db.session, actor(ADMIN), action.id)
        ro().risks.delete(db.session, actor(ADMIN), risk.id)
        ro().opportunities.delete(db.session, actor(ADMIN), opportunity.id)
        db.session.commit()
        self.assertEqual(["create", "update", "delete"],
                         [r.action for r in self.rows("oportunidades")])


class McpTestCase(McpDbCase):
    async def test_risks_opportunities_and_actions_round_trip(self) -> None:
        ana = self.seed("personas")
        who = mcp_actor(AUDITOR)
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "riesgos", "data": RISK | {
                "responsable_id": ana, "probabilidad": 4, "impacto": 5, "tratamiento": "mitigar",
                "probabilidad_residual": 2, "impacto_residual": 3,
                "fecha_identificacion": "2026-09-01", "fecha_revision": "2026-10-16"}})
            self.assertFalse(created.is_error, created.content)
            risk = created.structured_content
            self.assertEqual(("Crítico", "Medio", True, "Identificado", "2026-09-01"),
                             tuple(risk[k] for k in ("nivel", "nivel_residual",
                                                     "revision_vencida", "estado",
                                                     "fecha_identificacion")))
            future = await self.call(who, "qms_create", {"module": "riesgos", "data": RISK | {
                "responsable_id": ana, "fecha_identificacion": "2026-10-18"}})
            self.assertTrue(future.is_error)
            self.assertIn("futura", future.content[0].text)
            opportunity = (await self.call(who, "qms_create", {
                "module": "oportunidades", "data": OPPORTUNITY | {
                    "responsable_id": ana, "beneficio": 4, "viabilidad": 3}})).structured_content
            self.assertEqual(("Media", False), (opportunity["prioridad"],
                                                opportunity["revision_vencida"]))
            added = await self.call(who, "qms_create", {"module": "acciones_riesgo", "data": {
                "riesgo_id": risk["id"], "descripcion": "Plan de carrera",
                "responsable_id": ana, "fecha_limite": "2026-10-16"}})
            self.assertFalse(added.is_error, added.content)
            self.assertEqual((True, "Pendiente"), (added.structured_content["vencida"],
                                                   added.structured_content["estado"]))
            completed = await self.call(who, "qms_update", {
                "module": "acciones_riesgo", "id": added.structured_content["id"], "data": {
                    "estado": "completada", "fecha_completada": "2026-10-17",
                    "eficacia": "eficaz", "fecha_evaluacion": "2026-10-17"}})
            self.assertFalse(completed.is_error, completed.content)
            self.assertEqual((False, "Eficaz"), (completed.structured_content["vencida"],
                                                 completed.structured_content["eficacia"]))
            for slug, filters, total in (
                ("riesgos", {"nivel": "critico", "sin_valorar": False,
                             "revision_vencida": True, "estado": "identificado",
                             "origen": "interno", "responsable_id": ana}, 1),
                ("riesgos", {"nivel": "bajo"}, 0),
                ("oportunidades", {"prioridad": "media", "sin_valorar": False,
                                   "revision_vencida": False}, 1),
                ("acciones_riesgo", {"riesgo_id": risk["id"], "estado": "completada",
                                     "vencida": False}, 1),
            ):
                with self.subTest(slug=slug, filters=filters):
                    listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                             {"module": slug, "filters": filters})
                    self.assertFalse(listed.is_error, listed.content)
                    self.assertEqual(total, listed.structured_content["total"])
            bad = await self.call(who, "qms_list", {"module": "riesgos",
                                                    "filters": {"sin_valorar": "si"}})
            self.assertTrue(bad.is_error)
            denied = await self.call(mcp_actor(OPERATIVO), "qms_create", {
                "module": "riesgos", "data": RISK | {"responsable_id": ana}})
            self.assertTrue(denied.is_error)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        self.assertNotIn("riesgos_oportunidades", described)
        fields = {f["name"]: f for f in described["riesgos"]["fields"]}
        self.assertEqual((True, ["interno", "externo"]),
                         (fields["responsable_id"]["required"], fields["origen"]["allowed"]))
        self.assertEqual({"estado", "nivel", "responsable_id", "proceso_id", "origen",
                          "sin_valorar", "revision_vencida"},
                         {f["name"] for f in described["riesgos"]["filters"]})
        self.assertEqual({"estado", "prioridad", "responsable_id", "proceso_id", "origen",
                          "sin_valorar", "revision_vencida"},
                         {f["name"] for f in described["oportunidades"]["filters"]})
        self.assertEqual({"riesgo_id", "oportunidad_id", "estado", "responsable_id", "vencida"},
                         {f["name"] for f in described["acciones_riesgo"]["filters"]})


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDeleteTestCase(Helpers, unittest.TestCase):
    """PostgreSQL names the foreign key a delete breaks, so a delete racing an action insert
    (one the explicit check did not see) still gets the register's own message."""

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

    def test_the_actions_key_maps_to_the_has_actions_message(self) -> None:
        risk, opportunity = self.risk(), self.opportunity()
        self.action(riesgo_id=risk.id)
        self.action(oportunidad_id=opportunity.id)
        with patch.object(ro(), "_has_actions", return_value=False):
            for register, record, expected in (
                    ("risks", risk, ro().RISK_HAS_ACTIONS),
                    ("opportunities", opportunity, ro().OPPORTUNITY_HAS_ACTIONS)):
                with self.subTest(register=register):
                    with self.assertRaises(errors().Conflict) as caught:
                        getattr(ro(), register).delete(db.session, CLI, record.id)
                    db.session.rollback()
                    self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                    self.assertEqual(expected, caught.exception.message)
        self.assertIsNotNone(db.session.get(models().Riesgo, risk.id))
        self.assertIsNotNone(db.session.get(models().Oportunidad, opportunity.id))


if __name__ == "__main__":
    unittest.main()
