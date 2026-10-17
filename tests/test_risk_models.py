"""Risk and opportunity registers: models, CHECK constraints and delete protection (RO-1).

ISO 9001 clause 6.1, decisions RO1 to RO4 of ``risks-opportunities``. A risk
(``Riesgo``) is assessed by likelihood and impact, before and after its
actions; an opportunity (``Oportunidad``) by benefit and feasibility. Both
scales run from 1 to 5, and a pair is either complete or still pending (RO4).
``nivel`` and ``prioridad`` compute the band from the product. An action
(``AccionRiesgo``) belongs to exactly one risk or one opportunity, carries a
completion date only when completed, and its effectiveness is evaluated (result
and date together) only once completed.

People who own any of them, and processes and interested parties a risk or an
opportunity cites, cannot be deleted: the services check it explicitly and the
``ON DELETE RESTRICT`` foreign keys refuse a citation the check did not see.
The constraint cases run on SQLite with ``PRAGMA foreign_keys=ON`` and on
PostgreSQL when ``TEST_POSTGRES_URI`` is set (CI provides it).
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, errors

from app.extensions import db
from app.models import ParteInteresada, Person, ProcesoOperacion, RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=RoleEnum.ADMINISTRADOR, channel="cli")
RISK = {"titulo": "Rotación de personal", "descripcion": "Salida de técnicos clave",
        "origen": "interno", "fecha_identificacion": date(2026, 10, 1)}
OPPORTUNITY = {"titulo": "Nuevo mercado", "descripcion": "Clientes del sector naval",
               "origen": "externo", "fecha_identificacion": date(2026, 10, 2)}
ACTION = {"descripcion": "Plan de carrera", "fecha_limite": date(2026, 12, 31)}


def models():
    import app.models as module

    return module


def services():
    from app.services import people, process_operations, stakeholders

    return people, process_operations, stakeholders


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert: these tests must not depend on the services RO-2 adds."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez") -> int:
        return self.insert(Person.__table__, nombre=nombre)

    def process(self, nombre: str = "Compras") -> int:
        return self.insert(ProcesoOperacion.__table__, proceso=nombre)

    def party(self, nombre: str = "Clientes") -> int:
        return self.insert(ParteInteresada.__table__, nombre=nombre)

    def risk(self, **values) -> int:
        return self.insert(models().Riesgo.__table__, **(RISK | values))

    def opportunity(self, **values) -> int:
        return self.insert(models().Oportunidad.__table__, **(OPPORTUNITY | values))

    def action(self, **values) -> int:
        if "responsable_id" not in values:
            values["responsable_id"] = self.person("Responsable de la acción")
        return self.insert(models().AccionRiesgo.__table__, **(ACTION | values))

    def seed(self) -> dict[str, int]:
        """A person, a process and an interested party cited by one risk and one
        opportunity, each with an action that person owns."""
        ids = {"person": self.person(), "process": self.process(), "party": self.party()}
        links = {"responsable_id": ids["person"], "proceso_id": ids["process"],
                 "parte_interesada_id": ids["party"]}
        ids["risk"] = self.risk(**links)
        ids["opportunity"] = self.opportunity(**links)
        ids["risk_action"] = self.action(riesgo_id=ids["risk"], responsable_id=ids["person"])
        ids["opportunity_action"] = self.action(
            oportunidad_id=ids["opportunity"], responsable_id=ids["person"])
        return ids

    def rows(self, sql: str) -> list[tuple]:
        return [tuple(row) for row in db.session.execute(text(sql))]

    def accepted(self, *statements: str) -> None:
        for statement in statements:
            db.session.execute(text(statement))
        db.session.commit()

    def assert_refused(self, *statements: str) -> None:
        for statement in statements:
            with self.subTest(refused=statement):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement))
                    db.session.commit()
                db.session.rollback()

    def refused(self, error, call, *args) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args)
        db.session.rollback()
        return caught.exception.message


class LevelTestCase(unittest.TestCase):
    """``nivel`` (RO1) and ``prioridad`` (RO2) band the product of a 1-5 pair."""

    def test_risk_levels_at_every_band_edge(self) -> None:
        level = models().NivelRiesgo
        for (likelihood, impact), expected in {
            (1, 1): level.bajo, (2, 2): level.bajo, (1, 4): level.bajo,
            (1, 5): level.medio, (3, 3): level.medio,
            (2, 5): level.alto, (4, 4): level.alto,
            (4, 5): level.critico, (5, 5): level.critico,
        }.items():
            with self.subTest(pair=(likelihood, impact)):
                self.assertIs(expected, models().nivel(likelihood, impact))
                self.assertIs(expected, models().nivel(impact, likelihood))

    def test_every_risk_pair_falls_in_the_band_of_its_product(self) -> None:
        level = models().NivelRiesgo
        for likelihood in range(1, 6):
            for impact in range(1, 6):
                product = likelihood * impact
                expected = (level.bajo if product <= 4 else level.medio if product <= 9
                            else level.alto if product <= 16 else level.critico)
                with self.subTest(pair=(likelihood, impact)):
                    self.assertIs(expected, models().nivel(likelihood, impact))

    def test_opportunity_priorities_at_every_band_edge(self) -> None:
        priority = models().PrioridadOportunidad
        for (benefit, feasibility), expected in {
            (1, 1): priority.baja, (2, 3): priority.baja,
            (2, 4): priority.media, (3, 4): priority.media,
            (3, 5): priority.alta, (5, 5): priority.alta,
        }.items():
            with self.subTest(pair=(benefit, feasibility)):
                self.assertIs(expected, models().prioridad(benefit, feasibility))
                self.assertIs(expected, models().prioridad(feasibility, benefit))
        for benefit in range(1, 6):
            for feasibility in range(1, 6):
                product = benefit * feasibility
                expected = (priority.baja if product <= 6 else priority.media if product <= 12
                            else priority.alta)
                with self.subTest(pair=(benefit, feasibility)):
                    self.assertIs(expected, models().prioridad(benefit, feasibility))

    def test_a_pending_assessment_has_no_band(self) -> None:
        self.assertIsNone(models().nivel(None, None))
        self.assertIsNone(models().prioridad(None, None))

    def test_values_off_the_scale_or_half_an_assessment_are_refused(self) -> None:
        for pair in ((0, 3), (3, 6), (None, 3), (3, None), (True, 3), (2.5, 2), ("3", 3)):
            for helper in (models().nivel, models().prioridad):
                with self.subTest(helper=helper.__name__, pair=pair):
                    with self.assertRaises(ValueError):
                        helper(*pair)

    def test_records_expose_their_bands(self) -> None:
        risk = models().Riesgo(probabilidad=4, impacto=5)
        self.assertIs(models().NivelRiesgo.critico, risk.nivel)
        self.assertIsNone(risk.nivel_residual)
        risk.probabilidad_residual, risk.impacto_residual = 1, 3
        self.assertIs(models().NivelRiesgo.bajo, risk.nivel_residual)
        self.assertIsNone(models().Riesgo().nivel)
        opportunity = models().Oportunidad(beneficio=3, viabilidad=5)
        self.assertIs(models().PrioridadOportunidad.alta, opportunity.prioridad)
        self.assertIsNone(models().Oportunidad().prioridad)

    def test_choices_are_stored_by_name_and_shown_in_spanish(self) -> None:
        m = models()
        for enum_cls, labels in (
            (m.OrigenRiesgo, {"interno": "Interno", "externo": "Externo"}),
            (m.TratamientoRiesgo, {"aceptar": "Aceptar", "mitigar": "Mitigar",
                                   "transferir": "Transferir", "evitar": "Evitar"}),
            (m.EstadoRiesgo, {"identificado": "Identificado", "en_tratamiento": "En tratamiento",
                              "controlado": "Controlado", "cerrado": "Cerrado"}),
            (m.DecisionOportunidad, {"aprovechar": "Aprovechar", "posponer": "Posponer",
                                     "descartar": "Descartar"}),
            (m.EstadoOportunidad, {"identificada": "Identificada", "en_curso": "En curso",
                                   "aprovechada": "Aprovechada", "descartada": "Descartada"}),
            (m.EstadoAccionRiesgo, {"pendiente": "Pendiente", "en_curso": "En curso",
                                    "completada": "Completada", "cancelada": "Cancelada"}),
            (m.EficaciaAccion, {"eficaz": "Eficaz", "no_eficaz": "No eficaz"}),
            (m.NivelRiesgo, {"bajo": "Bajo", "medio": "Medio", "alto": "Alto",
                             "critico": "Crítico"}),
            (m.PrioridadOportunidad, {"baja": "Baja", "media": "Media", "alta": "Alta"}),
        ):
            with self.subTest(enum=enum_cls.__name__):
                self.assertEqual(labels, {member.name: member.value for member in enum_cls})


class SchemaTestCase(unittest.TestCase):
    """Columns, foreign keys and indexes as the models declare them."""

    def test_links_are_indexed_restrict_foreign_keys(self) -> None:
        m = models()
        for model, column, target, nullable in (
            (m.Riesgo, "parte_interesada_id", "partes_interesadas.id_interesado", True),
            (m.Riesgo, "proceso_id", "procesos_operacion.id_proceso", True),
            (m.Riesgo, "responsable_id", "personas.id", True),  # NULL for migrated rows
            (m.Oportunidad, "parte_interesada_id", "partes_interesadas.id_interesado", True),
            (m.Oportunidad, "proceso_id", "procesos_operacion.id_proceso", True),
            (m.Oportunidad, "responsable_id", "personas.id", True),
            (m.AccionRiesgo, "riesgo_id", "riesgos.id", True),
            (m.AccionRiesgo, "oportunidad_id", "oportunidades.id", True),
            (m.AccionRiesgo, "responsable_id", "personas.id", False),
        ):
            table = model.__table__
            with self.subTest(table=table.name, column=column):
                (fk,) = table.c[column].foreign_keys
                self.assertEqual(target, fk.target_fullname)
                self.assertEqual("RESTRICT", fk.ondelete)
                self.assertEqual(nullable, table.c[column].nullable)
                self.assertIn([column], [[c.name for c in index.columns]
                                         for index in table.indexes])

    def test_required_columns_and_defaults(self) -> None:
        m = models()
        for model, required, state in (
            (m.Riesgo, {"titulo", "descripcion", "origen", "estado", "fecha_identificacion"},
             "identificado"),
            (m.Oportunidad, {"titulo", "descripcion", "origen", "estado",
                             "fecha_identificacion"}, "identificada"),
            (m.AccionRiesgo, {"descripcion", "responsable_id", "fecha_limite", "estado"},
             "pendiente"),
        ):
            table = model.__table__
            with self.subTest(table=table.name):
                metadata = {"id", "created_at", "updated_at", "created_by_id", "updated_by_id"}
                self.assertEqual(required, {c.name for c in table.c
                                            if not c.nullable and c.name not in metadata})
                self.assertEqual(state, table.c.estado.server_default.arg)
        self.assertEqual(200, m.Riesgo.__table__.c.titulo.type.length)
        self.assertEqual(200, m.Oportunidad.__table__.c.titulo.type.length)
        for name in ("probabilidad_residual", "impacto_residual", "tratamiento"):
            self.assertIn(name, m.Riesgo.__table__.c)
            self.assertNotIn(name, m.Oportunidad.__table__.c)
        self.assertNotIn("nivel", m.Riesgo.__table__.c)  # computed, never stored


class ConstraintCases:
    """The CHECK constraints and foreign keys; shared by the SQLite and PostgreSQL cases."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_records_take_their_default_state_and_accept_a_full_assessment(self) -> None:
        self.enforce_foreign_keys()
        self.seed()
        self.assertEqual([("identificado", None, None)], self.rows(
            "SELECT estado, probabilidad, tratamiento FROM riesgos"))
        self.assertEqual([("identificada", None, None)], self.rows(
            "SELECT estado, beneficio, decision FROM oportunidades"))
        self.assertEqual([("pendiente", None, None), ("pendiente", None, None)], self.rows(
            "SELECT estado, fecha_completada, eficacia FROM acciones_riesgo ORDER BY id"))
        self.accepted(
            "UPDATE riesgos SET probabilidad = 1, impacto = 5, tratamiento = 'mitigar', "
            "probabilidad_residual = 5, impacto_residual = 1, estado = 'en_tratamiento', "
            "fecha_revision = '2027-01-31', notas = 'Revisar en enero'",
            "UPDATE oportunidades SET beneficio = 5, viabilidad = 1, decision = 'aprovechar', "
            "estado = 'en_curso', fecha_revision = '2027-02-28'",
            "UPDATE acciones_riesgo SET estado = 'completada', fecha_completada = '2026-11-30', "
            "eficacia = 'no_eficaz', fecha_evaluacion = '2027-01-15', "
            "comentario_eficacia = 'Siguen saliendo técnicos'",
        )
        self.assertEqual([(1, 5, 5, 1)], self.rows(
            "SELECT probabilidad, impacto, probabilidad_residual, impacto_residual FROM riesgos"))
        self.assertEqual([("no_eficaz", "2027-01-15")] * 2, self.rows(
            "SELECT eficacia, CAST(fecha_evaluacion AS VARCHAR) FROM acciones_riesgo "
            "ORDER BY id"))

    def test_the_risk_checks(self) -> None:
        self.enforce_foreign_keys()
        self.seed()
        self.assert_refused(
            "UPDATE riesgos SET probabilidad = 3",  # half an assessment
            "UPDATE riesgos SET impacto = 3",
            "UPDATE riesgos SET probabilidad = 0, impacto = 3",
            "UPDATE riesgos SET probabilidad = 3, impacto = 6",
            "UPDATE riesgos SET probabilidad_residual = 2",
            "UPDATE riesgos SET impacto_residual = 2",
            "UPDATE riesgos SET probabilidad_residual = 6, impacto_residual = 1",
            "UPDATE riesgos SET probabilidad_residual = 1, impacto_residual = 0",
            "UPDATE riesgos SET origen = 'Interno'",
            "UPDATE riesgos SET tratamiento = 'compartir'",
            "UPDATE riesgos SET estado = 'Identificado'",
            "UPDATE riesgos SET titulo = NULL",
            "UPDATE riesgos SET descripcion = NULL",
            "UPDATE riesgos SET origen = NULL",
            "UPDATE riesgos SET estado = NULL",
            "UPDATE riesgos SET fecha_identificacion = NULL",
        )
        self.assertEqual([(None, None, None, None)], self.rows(
            "SELECT probabilidad, impacto, probabilidad_residual, impacto_residual FROM riesgos"))

    def test_the_opportunity_checks(self) -> None:
        self.enforce_foreign_keys()
        self.seed()
        self.assert_refused(
            "UPDATE oportunidades SET beneficio = 3",
            "UPDATE oportunidades SET viabilidad = 3",
            "UPDATE oportunidades SET beneficio = 0, viabilidad = 3",
            "UPDATE oportunidades SET beneficio = 3, viabilidad = 6",
            "UPDATE oportunidades SET origen = 'mixto'",
            "UPDATE oportunidades SET decision = 'Aprovechar'",
            "UPDATE oportunidades SET estado = 'identificado'",
            "UPDATE oportunidades SET titulo = NULL",
            "UPDATE oportunidades SET fecha_identificacion = NULL",
        )

    def test_the_action_checks(self) -> None:
        self.enforce_foreign_keys()
        self.seed()
        done = "estado = 'completada', fecha_completada = '2026-11-30'"
        self.assert_refused(
            "UPDATE acciones_riesgo SET oportunidad_id = (SELECT id FROM oportunidades)",
            "UPDATE acciones_riesgo SET riesgo_id = NULL, oportunidad_id = NULL",
            "UPDATE acciones_riesgo SET estado = 'completada'",  # without its date
            "UPDATE acciones_riesgo SET fecha_completada = '2026-11-30'",  # not completed
            f"UPDATE acciones_riesgo SET {done}, eficacia = 'eficaz'",  # without its date
            f"UPDATE acciones_riesgo SET {done}, fecha_evaluacion = '2027-01-15'",
            "UPDATE acciones_riesgo SET eficacia = 'eficaz', fecha_evaluacion = '2027-01-15'",
            "UPDATE acciones_riesgo SET estado = 'en_curso', eficacia = 'eficaz', "
            "fecha_evaluacion = '2027-01-15'",
            f"UPDATE acciones_riesgo SET {done}, eficacia = 'Eficaz', "
            "fecha_evaluacion = '2027-01-15'",
            "UPDATE acciones_riesgo SET estado = 'Pendiente'",
            "UPDATE acciones_riesgo SET descripcion = NULL",
            "UPDATE acciones_riesgo SET responsable_id = NULL",
            "UPDATE acciones_riesgo SET fecha_limite = NULL",
        )
        # An evaluated action cannot leave ``completada`` while keeping its evaluation.
        self.accepted(f"UPDATE acciones_riesgo SET {done}, eficacia = 'eficaz', "
                      "fecha_evaluacion = '2027-01-15'")
        self.assert_refused(
            "UPDATE acciones_riesgo SET estado = 'en_curso', fecha_completada = NULL",
            "UPDATE acciones_riesgo SET fecha_evaluacion = NULL",
            "UPDATE acciones_riesgo SET eficacia = NULL",
        )
        self.accepted("UPDATE acciones_riesgo SET estado = 'cancelada', fecha_completada = NULL, "
                      "eficacia = NULL, fecha_evaluacion = NULL")

    def test_links_are_real_restrict_foreign_keys(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            *(f"UPDATE {table} SET {column} = 999"
              for table in ("riesgos", "oportunidades")
              for column in ("responsable_id", "proceso_id", "parte_interesada_id")),
            f"UPDATE acciones_riesgo SET riesgo_id = 999 WHERE id = {ids['risk_action']}",
            "UPDATE acciones_riesgo SET oportunidad_id = 999 "
            f"WHERE id = {ids['opportunity_action']}",
            "UPDATE acciones_riesgo SET responsable_id = 999",
            "DELETE FROM riesgos",  # it has an action
            "DELETE FROM oportunidades",
            "DELETE FROM procesos_operacion",
            "DELETE FROM partes_interesadas",
            f"DELETE FROM personas WHERE id = {ids['person']}",
        )
        self.assertEqual([(1, 1, 2)], self.rows(
            "SELECT (SELECT count(*) FROM riesgos), (SELECT count(*) FROM oportunidades), "
            "(SELECT count(*) FROM acciones_riesgo)"))


class DeleteProtectionCases:
    """The services refuse to delete what a register cites, with a Spanish ``Conflict``."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_people_owning_a_risk_an_opportunity_or_an_action_cannot_be_deleted(self) -> None:
        people = services()[0]
        self.enforce_foreign_keys()
        risk_owner, opportunity_owner, action_owner, free = (
            self.person(name) for name in ("Ana", "Bea", "Carla", "Dora"))
        risk = self.risk(responsable_id=risk_owner)
        self.opportunity(responsable_id=opportunity_owner)
        self.action(riesgo_id=risk, responsable_id=action_owner)
        for person_id in (risk_owner, opportunity_owner, action_owner):
            with self.subTest(person=person_id):
                message = self.refused(errors().Conflict, people.delete, CLI, person_id)
                self.assertEqual(people.STILL_REFERENCED, message)
        people.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual(3, db.session.query(Person).count())

    def test_processes_cited_by_a_risk_or_an_opportunity_cannot_be_deleted(self) -> None:
        process_operations = services()[1]
        self.enforce_foreign_keys()
        by_risk, by_opportunity, free = (self.process(name)
                                         for name in ("Compras", "Ventas", "Almacén"))
        self.risk(proceso_id=by_risk)
        self.opportunity(proceso_id=by_opportunity)
        for process_id in (by_risk, by_opportunity):
            with self.subTest(process=process_id):
                message = self.refused(errors().Conflict, process_operations.delete, CLI,
                                       process_id)
                self.assertEqual(process_operations.CITED_BY_RISKS, message)
        # The foreign key refuses a citation the check did not see: with the same message
        # when the database names it, otherwise with the original conflict's.
        expected = (process_operations.CITED_BY_RISKS if self.names_violated_constraints
                    else errors().Conflict.default_message)
        if self.foreign_keys_enforced:
            with patch.object(process_operations, "_cited_by_risks", return_value=False):
                for process_id in (by_risk, by_opportunity):
                    with self.subTest(unchecked=process_id):
                        with self.assertRaises(errors().Conflict) as caught:
                            process_operations.delete(db.session, CLI, process_id)
                        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                        self.assertEqual(expected, caught.exception.message)
                        db.session.rollback()
        process_operations.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual(2, db.session.query(ProcesoOperacion).count())

    def test_interested_parties_cited_by_a_risk_or_an_opportunity_cannot_be_deleted(
            self) -> None:
        stakeholders = services()[2]
        self.enforce_foreign_keys()
        by_risk, by_opportunity, free = (self.party(name)
                                         for name in ("Clientes", "Proveedores", "Bancos"))
        self.risk(parte_interesada_id=by_risk)
        self.opportunity(parte_interesada_id=by_opportunity)
        for party_id in (by_risk, by_opportunity):
            with self.subTest(party=party_id):
                message = self.refused(errors().Conflict, stakeholders.delete, CLI, party_id)
                self.assertEqual(stakeholders.CITED_BY_RISKS, message)
        expected = (stakeholders.CITED_BY_RISKS if self.names_violated_constraints
                    else errors().Conflict.default_message)
        if self.foreign_keys_enforced:
            with patch.object(stakeholders, "_cited_by_risks", return_value=False):
                for party_id in (by_risk, by_opportunity):
                    with self.subTest(unchecked=party_id):
                        with self.assertRaises(errors().Conflict) as caught:
                            stakeholders.delete(db.session, CLI, party_id)
                        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                        self.assertEqual(expected, caught.exception.message)
                        db.session.rollback()
        stakeholders.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual(2, db.session.query(ParteInteresada).count())

    def test_another_conflict_keeps_its_own_message(self) -> None:
        _, process_operations, stakeholders = services()
        self.enforce_foreign_keys()
        for service, record_id in ((process_operations, self.process()),
                                   (stakeholders, self.party())):
            with self.subTest(service=service.__name__):
                with patch.object(service.crud, "delete",
                                  side_effect=errors().Conflict("Otro conflicto.")):
                    message = self.refused(errors().Conflict, service.delete, CLI, record_id)
                self.assertEqual("Otro conflicto.", message)


class SqliteWithoutForeignKeysTestCase(DeleteProtectionCases, Helpers, ServiceBase):
    """SQLite's default: only the services' explicit checks protect the records."""

    names_violated_constraints = False
    foreign_keys_enforced = False

    def enforce_foreign_keys(self) -> None:
        self.assertEqual(0, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


class SqliteTestCase(ConstraintCases, DeleteProtectionCases, Helpers, ServiceBase):
    names_violated_constraints = False
    foreign_keys_enforced = True

    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresTestCase(ConstraintCases, DeleteProtectionCases, Helpers, unittest.TestCase):
    """The same cases against PostgreSQL, whose foreign keys are always enforced."""

    foreign_keys_enforced = True

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
