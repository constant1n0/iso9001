"""Records a change to the QMS cites cannot be deleted (CP-1 of ``change-planning``).

ISO 9001 clause 6.3, decision CP1. A change (``CambioSGC``) cites its owner, its
approver and its effectiveness reviewer, its tasks cite their owners, its links
cite the processes it affects, and it may name the record behind it: a
management review action, a nonconformity, an audit, a risk, an opportunity or
a context issue. Every service that deletes one of those refuses with a Spanish
``Conflict`` that says a change cites it, and leaves both rows in place; an
uncited record is still deleted. The ``ON DELETE RESTRICT`` foreign keys refuse
a citation the check did not see; PostgreSQL names them, so the services whose
delete reads that name still say why. The cases run on SQLite without and with
``PRAGMA foreign_keys=ON``, and on PostgreSQL when ``TEST_POSTGRES_URI`` is set.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, errors

from app.extensions import db
from app.models import (
    AccionRevision, Auditoria, CuestionContexto, NoConformidad, Oportunidad, Person,
    ProcesoOperacion, RevisionDireccion, Riesgo, RoleEnum,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid when the foreign keys are enforced.
CLI = Actor(user_id=None, label="cli", role=RoleEnum.ADMINISTRADOR, channel="cli")
CHANGE = {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos por un ERP",
          "origen": "otro", "proposito": "Trazabilidad de los pedidos",
          "fecha_propuesta": date(2026, 10, 1)}


def models():
    import app.models as module

    return module


def services():
    from app.services import (
        audits, context_issues, management_reviews, nonconformities, people,
        process_operations, risks,
    )

    return {"audits": audits, "context_issues": context_issues,
            "management_reviews": management_reviews, "nonconformities": nonconformities,
            "people": people, "process_operations": process_operations, "risks": risks}


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert: the changes have no service before CP-2."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str) -> int:
        return self.insert(Person.__table__, nombre=nombre)

    def change(self, **values) -> int:
        if "responsable_id" not in values:
            values["responsable_id"] = self.person("Responsable del cambio")
        return self.insert(models().CambioSGC.__table__, **(CHANGE | values))

    def review(self) -> int:
        return self.insert(RevisionDireccion.__table__, fecha=date(2026, 7, 10),
                           periodo_desde=date(2026, 4, 1), periodo_hasta=date(2026, 6, 30))

    def review_action(self, review_id: int) -> int:
        return self.insert(AccionRevision.__table__, revision_id=review_id, tipo="cambio_sgc",
                           descripcion="Implantar un ERP",
                           responsable_id=self.person("Responsable de la acción"),
                           fecha_limite=date(2026, 12, 31))

    def nonconformity(self) -> int:
        return self.insert(NoConformidad.__table__, descripcion="Pedido perdido",
                           fecha_detectada=date(2026, 9, 1))

    def internal_audit(self) -> int:
        return self.insert(Auditoria.__table__, area_auditada="Ventas", fecha=date(2026, 9, 2),
                           auditor="Luis", resultado="Sin trazabilidad")

    def risk(self) -> int:
        return self.insert(Riesgo.__table__, titulo="Pérdida de pedidos",
                           descripcion="Hoja compartida", origen="interno",
                           fecha_identificacion=date(2026, 9, 3))

    def opportunity(self) -> int:
        return self.insert(Oportunidad.__table__, titulo="Venta en línea",
                           descripcion="Pedidos web", origen="externo",
                           fecha_identificacion=date(2026, 9, 4))

    def issue(self) -> int:
        return self.insert(CuestionContexto.__table__, tipo="interna", categoria="conocimiento",
                           titulo="Saber disperso", descripcion="Cada comercial lleva su hoja",
                           efecto="negativo", pertinente=True,
                           fecha_identificacion=date(2026, 9, 5))

    def count(self, model) -> int:
        return db.session.scalar(select(func.count()).select_from(model))

    def refused(self, error, call, *args) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args)
        db.session.rollback()
        return caught.exception.message

    def unchecked_refusal(self, module, check: str, call, record_id: int) -> str:
        """The message a delete gives when its check misses the citation and only the
        foreign key refuses it; the database error never reaches the caller."""
        with patch.object(module, check, return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                call(db.session, CLI, record_id)
        db.session.rollback()
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        return caught.exception.message


class DeleteProtectionCases:
    """The services refuse to delete what a change cites, with a Spanish ``Conflict``."""

    names_violated_constraints = True  # SQLite only says a foreign key failed
    foreign_keys_enforced = True

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def named(self, message: str, fallback: str | None = None) -> str:
        """``message`` when the database names the broken key, else the service's fallback."""
        if self.names_violated_constraints:
            return message
        return errors().Conflict.default_message if fallback is None else fallback

    def test_people_a_change_or_a_task_cites_cannot_be_deleted(self) -> None:
        people = services()["people"]
        self.enforce_foreign_keys()
        owner, approver, reviewer, task_owner, free = (
            self.person(name) for name in ("Ana", "Bea", "Carla", "Dora", "Elena"))
        change = self.change(
            responsable_id=owner, estado="eficacia_revisada", aprobado_por_id=approver,
            fecha_aprobacion=date(2026, 10, 10), fecha_implantacion=date(2026, 11, 20),
            eficacia="eficaz", fecha_eficacia=date(2027, 1, 15), revisor_eficacia_id=reviewer)
        self.insert(models().TareaCambio.__table__, cambio_id=change, descripcion="Migrar",
                    responsable_id=task_owner, fecha_limite=date(2026, 11, 15),
                    estado="completada", fecha_completada=date(2026, 11, 14))
        for person_id in (owner, approver, reviewer, task_owner):
            with self.subTest(person=person_id):
                self.assertEqual(people.STILL_REFERENCED, self.refused(
                    errors().Conflict, people.delete, CLI, person_id))
                if self.foreign_keys_enforced:
                    self.assertEqual(people.STILL_REFERENCED, self.unchecked_refusal(
                        people, "_is_referenced", people.delete, person_id))
        people.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual((4, 1, 1), (self.count(Person), self.count(models().CambioSGC),
                                     self.count(models().TareaCambio)))

    def test_processes_a_change_affects_cannot_be_deleted(self) -> None:
        process_operations = services()["process_operations"]
        self.enforce_foreign_keys()
        affected = self.insert(ProcesoOperacion.__table__, proceso="Ventas")
        free = self.insert(ProcesoOperacion.__table__, proceso="Almacén")
        change = self.change()
        db.session.execute(models().cambio_procesos.insert().values(
            cambio_id=change, proceso_id=affected))
        db.session.commit()
        self.assertEqual(process_operations.LINKED_TO_CHANGES, self.refused(
            errors().Conflict, process_operations.delete, CLI, affected))
        if self.foreign_keys_enforced:
            self.assertEqual(self.named(process_operations.LINKED_TO_CHANGES),
                             self.unchecked_refusal(process_operations, "_linked_to_changes",
                                                    process_operations.delete, affected))
        process_operations.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual((1, 1), (self.count(ProcesoOperacion),
                                  self.count(models().cambio_procesos)))

    def test_review_actions_a_change_cites_and_their_reviews_cannot_be_deleted(self) -> None:
        reviews = services()["management_reviews"]
        self.enforce_foreign_keys()
        cited_review, free_review = self.review(), self.review()
        cited, free = self.review_action(cited_review), self.review_action(free_review)
        self.change(origen="revision_direccion", accion_revision_id=cited)
        self.assertEqual(reviews.ACTION_CITED_BY_CHANGES, self.refused(
            errors().Conflict, reviews.actions.delete, CLI, cited))
        self.assertEqual(reviews.ACTIONS_CITED_BY_CHANGES, self.refused(
            errors().Conflict, reviews.reviews.delete, CLI, cited_review))
        if self.foreign_keys_enforced:
            self.assertEqual(errors().Conflict.default_message, self.unchecked_refusal(
                reviews, "_cited_by_changes", reviews.actions.delete, cited))
        # A review whose actions no change cites keeps its own refusal.
        self.assertEqual(reviews.HAS_ACTIONS, self.refused(
            errors().Conflict, reviews.reviews.delete, CLI, free_review))
        reviews.actions.delete(db.session, CLI, free)
        reviews.reviews.delete(db.session, CLI, free_review)
        db.session.commit()
        self.assertEqual((1, 1, 1), (self.count(RevisionDireccion), self.count(AccionRevision),
                                     self.count(models().CambioSGC)))

    def test_nonconformities_a_change_cites_cannot_be_deleted(self) -> None:
        nonconformities = services()["nonconformities"]
        self.enforce_foreign_keys()
        cited, free = self.nonconformity(), self.nonconformity()
        self.change(origen="no_conformidad", no_conformidad_id=cited)
        self.assertEqual(nonconformities.CITED_BY_CHANGES, self.refused(
            errors().Conflict, nonconformities.delete, CLI, cited))
        if self.foreign_keys_enforced:
            self.assertEqual(errors().Conflict.default_message, self.unchecked_refusal(
                nonconformities, "_cited_by_changes", nonconformities.delete, cited))
        nonconformities.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual((1, 1), (self.count(NoConformidad), self.count(models().CambioSGC)))

    def test_audits_a_change_cites_cannot_be_deleted(self) -> None:
        audits = services()["audits"]
        self.enforce_foreign_keys()
        cited, free = self.internal_audit(), self.internal_audit()
        self.change(origen="auditoria", auditoria_id=cited)
        self.assertEqual(audits.CITED_BY_CHANGES, self.refused(
            errors().Conflict, audits.delete, CLI, cited))
        if self.foreign_keys_enforced:
            self.assertEqual(self.named(audits.CITED_BY_CHANGES, audits.DELETE_CONFLICT),
                             self.unchecked_refusal(audits, "_cited_by_changes", audits.delete,
                                                    cited))
        audits.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual((1, 1), (self.count(Auditoria), self.count(models().CambioSGC)))

    def test_risks_and_opportunities_a_change_cites_cannot_be_deleted(self) -> None:
        risks = services()["risks"]
        self.enforce_foreign_keys()
        for register, model, seed, origin, column, message in (
            (risks.risks, Riesgo, self.risk, "riesgo", "riesgo_id",
             risks.RISK_CITED_BY_CHANGES),
            (risks.opportunities, Oportunidad, self.opportunity, "oportunidad",
             "oportunidad_id", risks.OPPORTUNITY_CITED_BY_CHANGES),
        ):
            with self.subTest(register=model.__tablename__):
                cited, free = seed(), seed()
                self.change(origen=origin, **{column: cited})
                self.assertEqual(message, self.refused(
                    errors().Conflict, register.delete, CLI, cited))
                if self.foreign_keys_enforced:
                    self.assertEqual(self.named(message, risks.DELETE_CONFLICT),
                                     self.unchecked_refusal(risks, "_cited_by_changes",
                                                            register.delete, cited))
                register.delete(db.session, CLI, free)
                db.session.commit()
                self.assertEqual(1, self.count(model))
        self.assertEqual(2, self.count(models().CambioSGC))

    def test_context_issues_a_change_cites_cannot_be_deleted(self) -> None:
        context_issues = services()["context_issues"]
        self.enforce_foreign_keys()
        cited, free = self.issue(), self.issue()
        self.change(origen="cuestion_contexto", cuestion_id=cited)
        self.assertEqual(context_issues.CITED_BY_CHANGES, self.refused(
            errors().Conflict, context_issues.issues.delete, CLI, cited))
        if self.foreign_keys_enforced:
            self.assertEqual(self.named(context_issues.CITED_BY_CHANGES,
                                        context_issues.DELETE_CONFLICT),
                             self.unchecked_refusal(context_issues, "_cited_by_changes",
                                                    context_issues.issues.delete, cited))
        context_issues.issues.delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual((1, 1), (self.count(CuestionContexto),
                                  self.count(models().CambioSGC)))


class MessagesTestCase(unittest.TestCase):
    def test_the_messages_say_a_change_cites_the_record(self) -> None:
        s = services()
        for message in (s["process_operations"].LINKED_TO_CHANGES,
                        s["management_reviews"].ACTION_CITED_BY_CHANGES,
                        s["management_reviews"].ACTIONS_CITED_BY_CHANGES,
                        s["nonconformities"].CITED_BY_CHANGES, s["audits"].CITED_BY_CHANGES,
                        s["risks"].RISK_CITED_BY_CHANGES,
                        s["risks"].OPPORTUNITY_CITED_BY_CHANGES,
                        s["context_issues"].CITED_BY_CHANGES):
            with self.subTest(message=message):
                self.assertTrue(message.startswith("No se puede eliminar"))
                self.assertIn("cambios en el SGC", message)


class SqliteWithoutForeignKeysTestCase(DeleteProtectionCases, Helpers, ServiceBase):
    """SQLite's default: only the services' explicit checks protect the records."""

    names_violated_constraints = False
    foreign_keys_enforced = False

    def enforce_foreign_keys(self) -> None:
        self.assertEqual(0, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


class SqliteTestCase(DeleteProtectionCases, Helpers, ServiceBase):
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
class PostgresTestCase(DeleteProtectionCases, Helpers, unittest.TestCase):
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
