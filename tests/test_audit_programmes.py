"""Audit programmes and audit planning fields (AP-1: decisions AP1, AP2, AP3 and AP7).

A programme per year (``programas_auditoria``) starts as a draft and only an
administrator approves it, with a date and an approving person; an approved
programme keeps its year. Audits may belong to a programme (their date falls in
its year) and record scope, criteria, the audited-area owner and a team; no
auditor may own the audited area. Programmes share the ``AUDITS`` grant.

The SQLite cases run with the audit flush guard (``ServiceBase``). The foreign
keys are exercised on SQLite with ``PRAGMA foreign_keys=ON`` and on PostgreSQL
when ``TEST_POSTGRES_URI`` is set (CI provides it).
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import McpDbCase, mcp_actor
from register_routes import RegisterRoutesBase
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import Auditoria, AuditLog, Person, RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
TODAY = date(2026, 10, 10)
PROGRAMME = {"anio": 2026, "objetivos": "Auditar todos los procesos", "alcance": "Todo el SGC"}
AUDIT = {"area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Luis",
         "resultado": "Sin hallazgos"}


def programmes():
    from app.services import audit_programmes

    return audit_programmes


def audits():
    from app.services import audits as module

    return module


def models():
    import app.models as module

    return module


def draft():
    return models().EstadoProgramaAuditoria.borrador


def approved():
    return models().EstadoProgramaAuditoria.aprobado


class Helpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        """Core insert: these tests must not depend on the people write API."""
        result = db.session.execute(Person.__table__.insert().values(nombre=nombre, activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def deactivate(self, person_id: int) -> None:
        table = Person.__table__
        db.session.execute(table.update().where(table.c.id == person_id).values(activo=False))
        db.session.commit()

    def programme(self, who: Actor = CLI, **overrides):
        found = programmes().create(db.session, who, PROGRAMME | overrides)
        db.session.commit()
        return found

    def approve(self, programme_id: int, approver: int, who: Actor = CLI):
        found = programmes().approve(db.session, who, programme_id, aprobado_por_id=approver,
                                     today=TODAY)
        db.session.commit()
        return found

    def new_audit(self, who: Actor = CLI, **overrides) -> Auditoria:
        found = audits().create(db.session, who, AUDIT | overrides)
        db.session.commit()
        return found

    def update_audit(self, audit_id: int, data: dict, who: Actor = CLI) -> Auditoria:
        found = audits().update(db.session, who, audit_id, data)
        db.session.commit()
        return found

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs):
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message if hasattr(caught.exception, "message") else ""


class ProgrammeTestCase(Helpers, ServiceBase):
    def test_a_programme_starts_as_a_draft_with_an_audit_row(self) -> None:
        found = self.programme(actor(AUDITOR))
        self.assertEqual((2026, draft(), None, None),
                         (found.anio, found.estado, found.aprobado_en, found.aprobado_por_id))
        self.assertEqual((7, 7), (found.created_by_id, found.updated_by_id))
        (row,) = self.rows("programas_auditoria")
        self.assertEqual(("create", found.id, None), (row.action, row.entity_id, row.before))
        self.assertEqual(("Borrador", 2026, "Todo el SGC"),
                         (row.after["estado"], row.after["anio"], row.after["alcance"]))

    def test_invalid_programmes_are_refused_and_nothing_is_written(self) -> None:
        cases = {
            "missing year": {k: v for k, v in PROGRAMME.items() if k != "anio"},
            "missing objectives": {k: v for k, v in PROGRAMME.items() if k != "objetivos"},
            "missing scope": {k: v for k, v in PROGRAMME.items() if k != "alcance"},
            "year too early": PROGRAMME | {"anio": 1999},
            "year too late": PROGRAMME | {"anio": 2101},
            "year as text": PROGRAMME | {"anio": "2026"},
            "year as a flag": PROGRAMME | {"anio": True},
            "blank objectives": PROGRAMME | {"objetivos": "  "},
            "blank scope": PROGRAMME | {"alcance": None},
            "state is not writable": PROGRAMME | {"estado": "aprobado"},
            "approver is not writable": PROGRAMME | {"aprobado_por_id": 1},
            "not a mapping": ["anio"],
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.refused(errors().ValidationError, programmes().create, actor(ADMIN), data)
        message = self.refused(errors().ValidationError, programmes().create, actor(ADMIN),
                               PROGRAMME | {"anio": 1999})
        self.assertIn("2000", message)
        self.assertEqual([], programmes().list_(db.session, actor(ADMIN)))
        self.assertEqual([], self.rows("programas_auditoria"))

    def test_a_year_has_one_programme(self) -> None:
        self.programme()
        other = self.programme(anio=2027)
        message = self.refused(errors().Conflict, programmes().create, actor(ADMIN), PROGRAMME)
        self.assertIn("2026", message)
        self.refused(errors().Conflict, programmes().update, actor(ADMIN), other.id,
                     {"anio": 2026})
        self.assertEqual(2, len(self.rows("programas_auditoria")))

    def test_update_audits_the_changed_fields_and_a_no_op_writes_nothing(self) -> None:
        found = self.programme(actor(AUDITOR))
        programmes().update(db.session, actor(ADMIN, user_id=8), found.id,
                            {"anio": 2027, "objetivos": " Nuevos objetivos "})
        db.session.commit()
        self.assertEqual((2027, "Nuevos objetivos", 8),
                         (found.anio, found.objetivos, found.updated_by_id))
        row = self.rows("programas_auditoria")[-1]
        self.assertEqual("update", row.action)
        self.assertEqual((2026, 2027), (row.before["anio"], row.after["anio"]))
        self.assertNotIn("alcance", row.after)
        programmes().update(db.session, actor(ADMIN), found.id, {"alcance": "Todo el SGC"})
        db.session.commit()
        self.assertEqual(2, len(self.rows("programas_auditoria")))
        self.refused(errors().NotFound, programmes().update, actor(ADMIN), 999, {"anio": 2028})

    def test_lists_newest_year_first_and_filter_by_year_and_state(self) -> None:
        old = self.programme(anio=2025)
        new = self.programme(anio=2026)
        self.approve(new.id, self.person())
        who = actor(AUDITOR)
        self.assertEqual([new, old], programmes().list_(db.session, who))
        self.assertEqual([old], programmes().list_(db.session, who, anio=2025))
        self.assertEqual([new], programmes().list_(db.session, who, estado=approved()))
        self.assertEqual(([old], 2), programmes().list_page(db.session, who, page=2, per_page=1))
        self.assertEqual(([], 0), programmes().list_page(db.session, who, anio=2030))
        self.assertIs(new, programmes().get(db.session, who, new.id))
        self.refused(errors().NotFound, programmes().get, who, 999)


class ApprovalTestCase(Helpers, ServiceBase):
    def test_an_administrator_approves_a_draft_with_date_and_approver(self) -> None:
        ana = self.person()
        found = self.programme()
        programmes().approve(db.session, actor(ADMIN, user_id=8), found.id,
                             aprobado_por_id=ana, today=TODAY)
        db.session.commit()
        self.assertEqual((approved(), TODAY, ana, 8),
                         (found.estado, found.aprobado_en, found.aprobado_por_id,
                          found.updated_by_id))
        row = self.rows("programas_auditoria")[-1]
        self.assertEqual(("update", "Borrador"), (row.action, row.before["estado"]))
        self.assertEqual(("Aprobado", "2026-10-10", ana),
                         (row.after["estado"], row.after["aprobado_en"],
                          row.after["aprobado_por_id"]))

    def test_only_administrators_approve_and_never_through_mcp(self) -> None:
        ana = self.person()
        found = self.programme()
        for who in (actor(AUDITOR), actor(OPERATIVO), actor(ADMIN, channel="mcp"),
                    actor(ADMIN, scopes={"read"})):
            with self.subTest(role=who.role.name, channel=who.channel, scopes=who.scopes):
                self.refused(errors().PermissionDenied, programmes().approve, who, found.id,
                             aprobado_por_id=ana, today=TODAY)
        db.session.refresh(found)
        self.assertIs(draft(), found.estado)
        self.assertEqual(1, len(self.rows("programas_auditoria")))

    def test_approval_needs_a_draft_and_an_active_approving_person(self) -> None:
        ana, eva = self.person(), self.person("Eva")
        self.deactivate(eva)
        found = self.programme()
        for approver in (None, 999, eva, "1"):
            with self.subTest(approver=approver):
                self.refused(errors().ValidationError, programmes().approve, actor(ADMIN),
                             found.id, aprobado_por_id=approver, today=TODAY)
        self.approve(found.id, ana)
        message = self.refused(errors().ValidationError, programmes().approve, actor(ADMIN),
                               found.id, aprobado_por_id=ana, today=TODAY)
        self.assertIn("borrador", message)
        self.refused(errors().NotFound, programmes().approve, actor(ADMIN), 999,
                     aprobado_por_id=ana, today=TODAY)

    def test_a_database_conflict_on_approval_is_a_spanish_conflict(self) -> None:
        ana = self.person()
        found = self.programme()
        with patch.object(db.session, "flush",
                          side_effect=IntegrityError("stmt", {}, Exception("fk"))):
            message = self.refused(errors().Conflict, programmes().approve, actor(ADMIN),
                                   found.id, aprobado_por_id=ana, today=TODAY)
        self.assertEqual(programmes().APPROVE_CONFLICT, message)
        self.assertIn("aprobar el programa", message)
        db.session.refresh(found)
        self.assertIs(draft(), found.estado)

    def test_the_approval_date_comes_from_the_adapter(self) -> None:
        found = self.programme()
        ana = self.person()
        for today in (None, datetime(2026, 10, 10, 9, 0), "2026-10-10"):
            with self.subTest(today=today):
                with self.assertRaises(ValueError):
                    programmes().approve(db.session, actor(ADMIN), found.id,
                                         aprobado_por_id=ana, today=today)
                db.session.rollback()

    def test_an_approved_programme_keeps_its_year(self) -> None:
        found = self.programme()
        self.approve(found.id, self.person())
        message = self.refused(errors().ValidationError, programmes().update, actor(ADMIN),
                               found.id, {"anio": 2027})
        self.assertIn("aprobado", message)
        programmes().update(db.session, actor(AUDITOR), found.id,
                            {"anio": 2026, "objetivos": "Revisados"})
        db.session.commit()
        self.assertEqual((2026, "Revisados"), (found.anio, found.objetivos))

    def test_a_draft_with_audits_keeps_its_year(self) -> None:
        found = self.programme()
        self.new_audit(programa_id=found.id)
        message = self.refused(errors().ValidationError, programmes().update, actor(ADMIN),
                               found.id, {"anio": 2027})
        self.assertIn("auditorías", message)


class DeleteTestCase(Helpers, ServiceBase):
    def test_an_administrator_deletes_a_programme_without_audits(self) -> None:
        found = self.programme()
        found_id = found.id
        programmes().delete(db.session, actor(ADMIN), found_id)
        db.session.commit()
        self.assertEqual([], programmes().list_(db.session, actor(ADMIN)))
        row = self.rows("programas_auditoria")[-1]
        self.assertEqual(("delete", found_id, None, 2026),
                         (row.action, row.entity_id, row.after, row.before["anio"]))

    def test_a_programme_with_audits_cannot_be_deleted(self) -> None:
        found = self.programme()
        linked = self.new_audit(programa_id=found.id)
        message = self.refused(errors().Conflict, programmes().delete, actor(ADMIN), found.id)
        self.assertIn("auditorías", message)
        self.assertEqual([found], programmes().list_(db.session, actor(ADMIN)))
        self.update_audit(linked.id, {"programa_id": None})
        programmes().delete(db.session, actor(ADMIN), found.id)  # control: nothing cites it
        db.session.commit()
        self.assertEqual([], programmes().list_(db.session, actor(ADMIN)))

    def test_only_administrators_delete_and_never_through_mcp(self) -> None:
        found = self.programme()
        for who in (actor(AUDITOR), actor(OPERATIVO), actor(ADMIN, channel="mcp")):
            with self.subTest(role=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, programmes().delete, who, found.id)
        self.assertEqual([found], programmes().list_(db.session, actor(ADMIN)))
        self.refused(errors().NotFound, programmes().delete, actor(ADMIN), 999)


class PolicyTestCase(Helpers, ServiceBase):
    def test_administrators_and_auditors_work_and_operativo_is_refused(self) -> None:
        for year, role in ((2026, ADMIN), (2027, AUDITOR)):
            with self.subTest(role=role.name):
                who = actor(role)
                found = programmes().create(db.session, who, PROGRAMME | {"anio": year})
                programmes().update(db.session, who, found.id, {"objetivos": "Otros"})
                db.session.commit()
                self.assertIs(found, programmes().get(db.session, who, found.id))
                self.assertIn(found, programmes().list_(db.session, who))
        found = programmes().list_(db.session, actor(ADMIN))[0]
        who = actor(OPERATIVO)
        for name, args in (("get", (found.id,)), ("list_", ()), ("list_page", ()),
                           ("create", (PROGRAMME,)), ("update", (found.id, {"objetivos": "x"}))):
            call = getattr(programmes(), name)
            with self.subTest(call=name):
                self.refused(errors().PermissionDenied, call, who, *args)
        self.refused(errors().PermissionDenied, programmes().get,
                     actor(ADMIN, scopes={"write"}), found.id)
        self.refused(errors().PermissionDenied, programmes().create,
                     actor(AUDITOR, scopes={"read"}), PROGRAMME | {"anio": 2030})


class AuditPlanningTestCase(Helpers, ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.ana, self.luis, self.eva = (self.person(name) for name in ("Ana", "Luis", "Eva"))
        self.plan = self.programme()

    def test_an_audit_records_programme_scope_criteria_owner_and_team(self) -> None:
        found = self.new_audit(programa_id=self.plan.id, alcance="Proceso de compras",
                           criterios="ISO 9001:2026, procedimientos internos",
                           auditor_id=self.luis, responsable_area_id=self.ana,
                           equipo_ids=[self.eva, self.luis, self.eva])
        self.assertEqual(
            (self.plan.id, "Proceso de compras", "ISO 9001:2026, procedimientos internos",
             self.ana, sorted([self.eva, self.luis])),
            (found.programa_id, found.alcance, found.criterios, found.responsable_area_id,
             found.equipo_ids))
        row = self.rows("auditorias")[-1]
        self.assertEqual(("create", sorted([self.eva, self.luis]), self.plan.id, self.ana),
                         (row.action, row.after["equipo_ids"], row.after["programa_id"],
                          row.after["responsable_area_id"]))

    def test_changing_only_the_team_is_audited_with_the_ids_before_and_after(self) -> None:
        found = self.new_audit(equipo_ids=[self.eva])
        self.update_audit(found.id, {"equipo_ids": [self.luis]}, actor(AUDITOR, user_id=8))
        self.assertEqual(([self.luis], 8), (found.equipo_ids, found.updated_by_id))
        row = self.rows("auditorias")[-1]
        self.assertEqual(("update", [self.eva], [self.luis]),
                         (row.action, row.before["equipo_ids"], row.after["equipo_ids"]))
        self.update_audit(found.id, {"equipo_ids": [self.luis, self.luis]})
        self.assertEqual(2, len(self.rows("auditorias")))  # the same team writes nothing
        self.update_audit(found.id, {"equipo_ids": None})
        self.assertEqual([], found.equipo_ids)
        self.assertEqual([], self.rows("auditorias")[-1].after["equipo_ids"])

    def test_unknown_or_malformed_references_are_refused_and_nothing_is_written(self) -> None:
        cases = {
            "unknown programme": {"programa_id": 999},
            "programme as text": {"programa_id": str(self.plan.id)},
            "unknown owner": {"responsable_area_id": 999},
            "unknown team member": {"equipo_ids": [self.eva, 999]},
            "team is not a list": {"equipo_ids": self.eva},
            "team of text": {"equipo_ids": [str(self.eva)]},
            "team with a flag": {"equipo_ids": [True]},
            "scope as a number": {"alcance": 5},
            "criteria as a number": {"criterios": 5},
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                self.refused(errors().ValidationError, audits().create, actor(AUDITOR),
                             AUDIT | extra)
        message = self.refused(errors().ValidationError, audits().create, actor(AUDITOR),
                               AUDIT | {"equipo_ids": [self.eva, 999, 998]})
        self.assertIn("998, 999", message)
        self.assertEqual(0, db.session.query(Auditoria).count())
        self.assertEqual([], self.rows("auditorias"))

    def test_inactive_people_are_refused_only_when_newly_chosen(self) -> None:
        found = self.new_audit(responsable_area_id=self.ana, equipo_ids=[self.eva])
        self.deactivate(self.ana)
        self.deactivate(self.eva)
        self.update_audit(found.id, {"resultado": "Revisado", "responsable_area_id": self.ana,
                                     "equipo_ids": [self.eva, self.luis]})
        self.assertEqual(sorted([self.eva, self.luis]), found.equipo_ids)
        for extra in ({"responsable_area_id": self.ana}, {"equipo_ids": [self.eva]}):
            with self.subTest(data=extra):
                message = self.refused(errors().ValidationError, audits().create, CLI,
                                       AUDIT | extra)
                self.assertIn("desactivad", message)

    def test_the_lead_auditor_cannot_own_the_audited_area(self) -> None:
        message = self.refused(errors().ValidationError, audits().create, actor(AUDITOR),
                               AUDIT | {"auditor_id": self.ana, "responsable_area_id": self.ana})
        self.assertIn("auditor", message)
        self.assertIn("responsable del área", message)
        found = self.new_audit(auditor_id=self.luis, responsable_area_id=self.ana)
        for data in ({"auditor_id": self.ana}, {"responsable_area_id": self.luis}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, audits().update, actor(AUDITOR),
                             found.id, data)
        db.session.refresh(found)
        self.assertEqual((self.luis, self.ana), (found.auditor_id, found.responsable_area_id))

    def test_no_team_member_can_own_the_audited_area(self) -> None:
        message = self.refused(errors().ValidationError, audits().create, actor(AUDITOR),
                               AUDIT | {"equipo_ids": [self.eva, self.ana],
                                        "responsable_area_id": self.ana})
        self.assertIn("equipo", message)
        found = self.new_audit(equipo_ids=[self.eva], responsable_area_id=self.ana)
        for data in ({"equipo_ids": [self.ana]}, {"responsable_area_id": self.eva}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, audits().update, actor(AUDITOR),
                             found.id, data)
        db.session.refresh(found)
        self.assertEqual(([self.eva], self.ana), (found.equipo_ids, found.responsable_area_id))
        self.assertEqual(1, len(self.rows("auditorias")))

    def test_the_audit_date_falls_in_its_programme_year(self) -> None:
        message = self.refused(errors().ValidationError, audits().create, actor(AUDITOR),
                               AUDIT | {"programa_id": self.plan.id, "fecha": date(2025, 12, 31)})
        self.assertIn("2026", message)
        found = self.new_audit(programa_id=self.plan.id)
        later = self.programme(anio=2027)
        for data in ({"fecha": date(2027, 1, 4)}, {"programa_id": later.id}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, audits().update, actor(AUDITOR),
                             found.id, data)
        self.update_audit(found.id, {"programa_id": later.id, "fecha": date(2027, 1, 4)})
        self.assertEqual((later.id, date(2027, 1, 4)), (found.programa_id, found.fecha))
        self.update_audit(found.id, {"programa_id": None, "fecha": date(2030, 1, 1)})
        self.assertIsNone(found.programa_id)

    def test_audits_filter_by_programme(self) -> None:
        linked = self.new_audit(programa_id=self.plan.id)
        self.new_audit()
        self.assertEqual(([linked], 1), audits().list_page(db.session, actor(AUDITOR),
                                                           programa_id=self.plan.id))

    def test_an_audit_without_planning_fields_keeps_working(self) -> None:
        found = self.new_audit()
        self.assertEqual((None, None, None, None, []),
                         (found.programa_id, found.alcance, found.criterios,
                          found.responsable_area_id, found.equipo_ids))
        self.assertEqual([], self.rows("auditorias")[-1].after["equipo_ids"])
        self.update_audit(found.id, {"resultado": "Revisado"})
        self.assertEqual("Revisado", found.resultado)

    def test_deleting_an_audit_removes_its_team(self) -> None:
        found = self.new_audit(equipo_ids=[self.eva])
        audits().delete(db.session, actor(ADMIN), found.id)
        db.session.commit()
        team = db.session.execute(text("SELECT count(*) FROM auditoria_equipo")).scalar_one()
        self.assertEqual(0, team)
        self.assertEqual([self.eva], self.rows("auditorias")[-1].before["equipo_ids"])


class PersonProtectionCases:
    """Deleting a person cited the new ways; shared by the SQLite and PostgreSQL cases."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def citations(self):
        """(name, cite(person_id), release()) for each new way an audit record cites a person."""
        plan = self.programme(anio=2030)

        def owner(person_id):
            found = self.new_audit(responsable_area_id=person_id)
            return lambda: self.update_audit(found.id, {"responsable_area_id": None})

        def team(person_id):
            found = self.new_audit(equipo_ids=[person_id])
            return lambda: self.update_audit(found.id, {"equipo_ids": []})

        def approver(person_id):
            self.approve(plan.id, person_id)

            def release():
                programmes().delete(db.session, CLI, plan.id)
                db.session.commit()
            return release

        return (("owner", owner), ("team", team), ("approver", approver))

    def test_a_person_cited_as_owner_team_member_or_approver_cannot_be_deleted(self) -> None:
        from app.services import people

        for name, cite in self.citations():
            with self.subTest(citation=name):
                ana = self.person()
                release = cite(ana)
                message = self.refused(errors().Conflict, people.delete, CLI, ana)
                self.assertIn("desactívala en su lugar", message)
                self.assertIsNotNone(db.session.get(Person, ana))
                release()
                people.delete(db.session, CLI, ana)
                db.session.commit()
                self.assertIsNone(db.session.get(Person, ana))

    def test_the_foreign_keys_refuse_a_citation_the_check_did_not_see(self) -> None:
        from app.services import people

        self.enforce_foreign_keys()
        for name, cite in self.citations():
            with self.subTest(citation=name):
                ana = self.person()
                release = cite(ana)
                # As if the citation were committed after the explicit check ran.
                with patch.object(people, "_is_referenced", return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        people.delete(db.session, CLI, ana)
                    self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                    db.session.rollback()
                    release()
                    people.delete(db.session, CLI, ana)  # control: nothing cites it now
                    db.session.commit()
                self.assertIsNone(db.session.get(Person, ana))

    def test_the_programme_foreign_key_refuses_an_audit_the_check_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        plan = self.programme()
        self.new_audit(programa_id=plan.id)
        with patch.object(programmes(), "_has_audits", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                programmes().delete(db.session, CLI, plan.id)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        db.session.rollback()
        self.assertEqual([plan.id], [p.id for p in programmes().list_(db.session, CLI)])


class SqlitePersonProtectionTestCase(PersonProtectionCases, Helpers, ServiceBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresPersonProtectionTestCase(PersonProtectionCases, Helpers, unittest.TestCase):
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
    async def test_programmes_round_trip_but_are_never_approved_through_mcp(self) -> None:
        who = mcp_actor(AUDITOR)
        created = await self.call(who, "qms_create", {"module": "programas_auditoria", "data": {
            "anio": 2026, "objetivos": "Auditar todo", "alcance": "SGC"}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual(("Borrador", None, None),
                         (record["estado"], record["aprobado_en"], record["aprobado_por_id"]))
        for filters, total in (({"estado": "borrador"}, 1), ({"anio": 2027}, 0)):
            listed = await self.call(who, "qms_list", {"module": "programas_auditoria",
                                                       "filters": filters})
            self.assertFalse(listed.is_error, listed.content)
            self.assertEqual(total, listed.structured_content["total"])
        for data in ({"estado": "aprobado"}, {"aprobado_por_id": 1}, {"aprobado_en": "2026-10-10"}):
            with self.subTest(data=data):
                refused = await self.call(mcp_actor(ADMIN), "qms_update", {
                    "module": "programas_auditoria", "id": record["id"], "data": data})
                self.assertTrue(refused.is_error)
                self.assertIn("Campos no permitidos", refused.content[0].text)
        hidden = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                 {"module": "programas_auditoria"})
        self.assertTrue(hidden.is_error)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}["programas_auditoria"]
        self.assertEqual({"anio", "objetivos", "alcance"}, {f["name"] for f in described["fields"]})
        self.assertEqual({"anio", "estado"}, {f["name"] for f in described["filters"]})

    async def test_programmes_update_and_get_through_mcp(self) -> None:
        plan = self.seed("programas_auditoria")
        who = mcp_actor(AUDITOR)
        updated = await self.call(who, "qms_update", {
            "module": "programas_auditoria", "id": plan,
            "data": {"anio": 2027, "objetivos": "Auditar compras"}})
        self.assertFalse(updated.is_error, updated.content)
        self.assertEqual((plan, 2027, "Auditar compras", "Borrador"),
                         tuple(updated.structured_content[k]
                               for k in ("id", "anio", "objetivos", "estado")))
        got = await self.call(who, "qms_get", {"module": "programas_auditoria", "id": plan})
        self.assertFalse(got.is_error, got.content)
        self.assertEqual((2027, "Auditar compras", "Todo el SGC"),
                         tuple(got.structured_content[k]
                               for k in ("anio", "objetivos", "alcance")))
        for arguments, word in (({"id": plan, "data": {"anio": 1999}}, "2000"),
                                ({"id": 999, "data": {"anio": 2028}}, "no encontrado")):
            with self.subTest(arguments=arguments):
                refused = await self.call(who, "qms_update",
                                          {"module": "programas_auditoria"} | arguments)
                self.assertTrue(refused.is_error)
                self.assertIn(word, refused.content[0].text)
        missing = await self.call(who, "qms_get", {"module": "programas_auditoria", "id": 999})
        self.assertTrue(missing.is_error)
        self.assertIn("Programa de auditoría no encontrado.", missing.content[0].text)
        hidden = await self.call(mcp_actor(OPERATIVO), "qms_get",
                                 {"module": "programas_auditoria", "id": plan})
        self.assertTrue(hidden.is_error)

    async def test_audits_carry_planning_fields_and_filter_by_programme(self) -> None:
        ana = self.seed("personas")
        luis = self.seed("personas", nombre="Luis")
        plan = self.seed("programas_auditoria")
        self.seed("auditorias")
        who = mcp_actor(AUDITOR)
        created = await self.call(who, "qms_create", {"module": "auditorias", "data": {
            "area_auditada": "Compras", "fecha": "2026-10-01", "auditor": "Luis",
            "resultado": "Sin hallazgos", "programa_id": plan, "alcance": "Compras",
            "criterios": "ISO 9001:2026", "responsable_area_id": ana, "equipo_ids": [luis]}})
        self.assertFalse(created.is_error, created.content)
        record = created.structured_content
        self.assertEqual((plan, "ISO 9001:2026", ana, [luis]),
                         (record["programa_id"], record["criterios"],
                          record["responsable_area_id"], record["equipo_ids"]))
        listed = await self.call(who, "qms_list", {"module": "auditorias",
                                                   "filters": {"programa_id": plan}})
        self.assertFalse(listed.is_error, listed.content)
        self.assertEqual((1, [luis]), (listed.structured_content["total"],
                                       listed.structured_content["items"][0]["equipo_ids"]))
        refused = await self.call(who, "qms_update", {"module": "auditorias", "id": record["id"],
                                                      "data": {"equipo_ids": [ana]}})
        self.assertTrue(refused.is_error)
        self.assertIn("responsable del área", refused.content[0].text)
        got = await self.call(who, "qms_get", {"module": "auditorias", "id": record["id"]})
        self.assertEqual([luis], got.structured_content["equipo_ids"])


class AuditFormTestCase(RegisterRoutesBase):
    BASE = "/auditorias"
    FORM = {"area_auditada": "Compras", "fecha": "2026-10-05", "auditor": "Luis",
            "resultado": "Sin hallazgos", "accion_correctiva": "", "estado": "PENDIENTE"}

    def setUp(self) -> None:
        super().setUp()
        with self.app.app_context():
            table = Person.__table__
            self.ana, self.eva, self.olga = (
                db.session.execute(table.insert().values(nombre=name, activo=active))
                .inserted_primary_key[0]
                for name, active in (("Ana Pérez", True), ("Eva Gil", True), ("Olga Ruiz", False)))
            db.session.commit()
        self.plan = self.seed_with(programmes(), PROGRAMME)

    def test_the_audit_form_offers_the_planning_fields(self) -> None:
        self.login(AUDITOR)
        page = self.client.get(f"{self.BASE}/nueva").get_data(as_text=True)
        for name in ("programa_id", "alcance", "criterios", "responsable_area_id", "equipo_ids"):
            self.assertIn(f'name="{name}"', page)
        self.assertIn("multiple", page)
        self.assertIn("2026", page)
        self.assertIn("Eva Gil", page)
        self.assertNotIn("Olga Ruiz", page)

    def test_a_new_audit_saves_its_planning_fields(self) -> None:
        self.login(AUDITOR)
        response = self.client.post(f"{self.BASE}/nueva", data=self.FORM | {
            "programa_id": self.plan, "alcance": "Compras", "criterios": "ISO 9001:2026",
            "responsable_area_id": self.ana, "equipo_ids": [self.eva]})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            found = db.session.query(Auditoria).one()
            self.assertEqual((self.plan, "ISO 9001:2026", self.ana, [self.eva]),
                             (found.programa_id, found.criterios, found.responsable_area_id,
                              found.equipo_ids))

    def test_an_independence_error_shows_the_form_again_with_the_input_kept(self) -> None:
        self.login(AUDITOR)
        response = self.client.post(f"{self.BASE}/nueva", data=self.FORM | {
            "alcance": "Alcance tecleado", "responsable_area_id": self.ana,
            "equipo_ids": [self.ana, self.eva]})
        self.assertEqual(200, response.status_code)
        page = response.get_data(as_text=True)
        self.assertIn("responsable del área", page)
        self.assertIn("Alcance tecleado", page)
        self.assertEqual(0, self.count(Auditoria))

    def test_editing_shows_and_replaces_the_team(self) -> None:
        from app.services import audits as service

        audit_id = self.seed_with(service, {
            "area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Luis",
            "resultado": "OK", "equipo_ids": [self.eva]})
        self.login(AUDITOR)
        page = self.client.get(f"{self.BASE}/editar/{audit_id}").get_data(as_text=True)
        self.assertRegex(page, rf'<option selected value="{self.eva}">|'
                               rf'<option value="{self.eva}" selected>')
        response = self.client.post(f"{self.BASE}/editar/{audit_id}",
                                    data=self.FORM | {"equipo_ids": [self.ana, self.eva]})
        self.assertEqual(302, response.status_code)
        self.assertEqual(sorted([self.ana, self.eva]), self.fetch(Auditoria, audit_id).equipo_ids)

    def test_editing_keeps_a_team_member_who_became_inactive(self) -> None:
        from app.services import audits as service

        audit_id = self.seed_with(service, {
            "area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Luis",
            "resultado": "OK", "equipo_ids": [self.eva]})
        with self.app.app_context():
            table = Person.__table__
            db.session.execute(table.update().where(table.c.id == self.eva).values(activo=False))
            db.session.commit()
        self.login(AUDITOR)
        page = self.client.get(f"{self.BASE}/editar/{audit_id}").get_data(as_text=True)
        self.assertRegex(page, rf'<option selected value="{self.eva}">|'
                               rf'<option value="{self.eva}" selected>')
        self.assertNotIn("Olga Ruiz", page)
        response = self.client.post(f"{self.BASE}/editar/{audit_id}", data=self.FORM | {
            "resultado": "Revisado", "equipo_ids": [self.eva]})
        self.assertEqual(302, response.status_code)
        found = self.fetch(Auditoria, audit_id)
        self.assertEqual(([self.eva], "Revisado"), (found.equipo_ids, found.resultado))
        response = self.client.post(f"{self.BASE}/editar/{audit_id}", data=self.FORM | {
            "equipo_ids": [self.eva, self.olga]})
        self.assertEqual(200, response.status_code)
        self.assertEqual([self.eva], self.fetch(Auditoria, audit_id).equipo_ids)


if __name__ == "__main__":
    unittest.main()
