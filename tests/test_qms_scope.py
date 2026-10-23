"""Scope of the quality management system (OC-2 of ``organization-context``).

ISO 9001 clause 4.3, decisions OC3 and OC4. The scope is versioned data: a
statement, the products and services, the sites, notes and one applicability
entry per requirement of clauses 4 to 10 (``qms_scope.CATALOGUE``), with a
justification when a requirement does not apply. A version moves from
``borrador`` to ``vigente`` to ``obsoleto``; there is at most one draft and one
current version (partial unique indexes), only a draft changes, a new draft
starts as a copy of the current version, and only an administrator approves,
never on the ``mcp`` channel; approving makes the current version obsolete.
People who approved a version cannot be deleted. The SQLite cases run with the
audit flush guard; the database cases also run on PostgreSQL.
"""

from __future__ import annotations

import os
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from mcp_support import ADMIN, AUDITOR, OPERATIVO, McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import AlcanceSGC, AuditLog, EstadoAlcance, Person, User, aplicabilidad_alcance
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
GAP_ANALYSIS = Path(__file__).resolve().parents[1] / "docs" / "analisis-brechas-iso-9001.md"
TODAY = date(2026, 10, 22)
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
SCOPE = {"declaracion": "Diseño y fabricación de envases metálicos para alimentación.",
         "productos_servicios": "Latas y tapas", "centros": "Planta de Burgos"}
CODES = ["4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3", "6.1", "6.2", "6.3",
         *(f"7.1.{n}" for n in range(1, 7)), "7.2", "7.3", "7.4", "7.5",
         "8.1", "8.2", "8.3", "8.4", *(f"8.5.{n}" for n in range(1, 7)), "8.6", "8.7",
         "9.1", "9.2", "9.3", "10.1", "10.2", "10.3"]
EXCLUDED = {"aplica": False, "justificacion": "No diseñamos: fabricamos según plano del cliente."}


def qs():
    from app.services import qms_scope

    return qms_scope


def people():
    from app.services import people as module

    return module


def applies() -> dict:
    """Every requirement applying, as a first draft starts."""
    return {code: {"aplica": True, "justificacion": None} for code in CODES}


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", **values) -> int:
        return self.insert(Person.__table__, nombre=nombre, activo=True, **values)

    def admin(self, nombre: str = "Dirección") -> tuple[Actor, int]:
        """A web administrator whose user account is linked to a person; returns both."""
        user_id = self.insert(User.__table__, username=f"admin{nombre}", password="x",
                              role=ADMIN)
        return (Actor(user_id=user_id, label="admin", role=ADMIN, channel="web"),
                self.person(nombre, user_id=user_id))

    def draft(self, who: Actor = CLI, data: dict | None = None) -> AlcanceSGC:
        created = qs().new_draft(db.session, who, SCOPE if data is None else data)
        db.session.commit()
        return created

    def edit(self, scope_id: int, data: dict, who: Actor = CLI) -> AlcanceSGC:
        found = qs().update_draft(db.session, who, scope_id, data)
        db.session.commit()
        return found

    def approve(self, scope_id: int, who: Actor, today: date = TODAY) -> AlcanceSGC:
        found = qs().approve(db.session, who, scope_id, today=today)
        db.session.commit()
        return found

    def entries(self, scope_id: int) -> dict:
        return qs().applicability_of(db.session, scope_id)

    def rows(self) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type="alcances_sgc")
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message


class CatalogueTestCase(unittest.TestCase):
    def test_the_catalogue_lists_every_requirement_of_clauses_4_to_10(self) -> None:
        catalogue = qs().catalogue()
        self.assertEqual(CODES, [code for code, _ in catalogue])
        self.assertEqual(38, len(catalogue))
        titles = dict(catalogue)
        self.assertEqual("Determinación del alcance del sistema de gestión de la calidad",
                         titles["4.3"])
        self.assertEqual("Diseño y desarrollo de los productos y servicios", titles["8.3"])
        self.assertEqual("Propiedad perteneciente a los clientes o proveedores externos",
                         titles["8.5.3"])
        self.assertEqual("Recursos de seguimiento y medición", titles["7.1.5"])

    def test_titles_use_the_wording_of_the_gap_analysis(self) -> None:
        rows = re.findall(r"^\| (\d+(?:\.\d+)+) ([^|]+?) \|", GAP_ANALYSIS.read_text("utf-8"),
                          re.MULTILINE)
        titles = dict(qs().catalogue())
        shared = [(code, title) for code, title in rows if code in titles]
        self.assertGreaterEqual(len(shared), 25)
        for code, title in shared:
            with self.subTest(code=code):
                self.assertEqual(title, titles[code])


class DraftTestCase(Helpers, ServiceBase):
    def test_the_first_draft_needs_a_statement_and_applies_every_requirement(self) -> None:
        self.assertEqual(qs().STATEMENT_REQUIRED, self.refused(
            errors().ValidationError, qs().new_draft, CLI, {"centros": "Burgos"}))
        self.refused(errors().ValidationError, qs().new_draft, CLI, {"declaracion": "  "})
        created = self.draft(actor(AUDITOR))
        self.assertEqual((1, EstadoAlcance.borrador, SCOPE["declaracion"], "Latas y tapas",
                          "Planta de Burgos", None, None, None, None, 7),
                         (created.version, created.estado, created.declaracion,
                          created.productos_servicios, created.centros, created.notas,
                          created.aprobado_por_id, created.fecha_aprobacion,
                          created.fecha_obsolescencia, created.created_by_id))
        self.assertEqual(applies(), self.entries(created.id))
        self.assertEqual(CODES, list(self.entries(created.id)))
        (row,) = self.rows()
        self.assertEqual(("create", created.id, 1, "Borrador", applies()),
                         (row.action, row.entity_id, row.after["version"], row.after["estado"],
                          row.after["aplicabilidad"]))

    def test_a_first_draft_may_already_exclude_requirements(self) -> None:
        for change, expected in (({"11.1": {"aplica": True}},
                                  qs().UNKNOWN_REQUIREMENTS.format(codes="11.1")),
                                 ({"8.3": {"aplica": False}}, "Justifica")):
            with self.subTest(change=change):
                self.assertIn(expected, self.refused(
                    errors().ValidationError, qs().new_draft, actor(AUDITOR),
                    SCOPE | {"aplicabilidad": change}))
        created = self.draft(data=SCOPE | {"aplicabilidad": {"8.3": EXCLUDED}})
        self.assertEqual(applies() | {"8.3": EXCLUDED}, self.entries(created.id))

    def test_there_is_only_one_draft_at_a_time(self) -> None:
        self.draft()
        self.assertEqual(qs().ONE_DRAFT, self.refused(
            errors().ValidationError, qs().new_draft, CLI, SCOPE))
        self.assertEqual(1, db.session.query(AlcanceSGC).count())

    def test_a_draft_takes_texts_and_applicability_and_a_no_op_writes_nothing(self) -> None:
        scope = self.draft()
        measuring = {"aplica": False, "justificacion": "  Sin equipos de medición.  "}
        change = {"centros": "Burgos y Miranda", "notas": "",
                  "aplicabilidad": {"8.3": EXCLUDED, "7.1.5": measuring}}
        self.edit(scope.id, change, actor(AUDITOR))
        expected = applies() | {"8.3": EXCLUDED, "7.1.5": {
            "aplica": False, "justificacion": "Sin equipos de medición."}}
        self.assertEqual(expected, self.entries(scope.id))
        self.assertEqual(("Burgos y Miranda", None, 7), (scope.centros, scope.notas,
                                                         scope.updated_by_id))
        self.edit(scope.id, change, actor(AUDITOR))  # nothing changes
        self.edit(scope.id, {"aplicabilidad": {"8.3": {"justificacion": "Otro motivo"}}})
        self.assertEqual({"aplica": False, "justificacion": "Otro motivo"},
                         self.entries(scope.id)["8.3"])
        self.edit(scope.id, {"aplicabilidad": {"8.3": {"aplica": True}}})
        self.assertEqual({"aplica": True, "justificacion": "Otro motivo"},
                         self.entries(scope.id)["8.3"])
        self.edit(scope.id, {"notas": "Revisado"})
        rows = self.rows()
        self.assertEqual(["create"] + ["update"] * 4, [r.action for r in rows])
        stamps = {"updated_at", "updated_by_id"}
        self.assertEqual({"centros": "Planta de Burgos", "aplicabilidad": applies()},
                         {k: v for k, v in rows[1].before.items() if k not in stamps})
        self.assertEqual({"centros": "Burgos y Miranda", "aplicabilidad": expected},
                         {k: v for k, v in rows[1].after.items() if k not in stamps})
        # An edit of the texts alone leaves the applicability out of the audit row.
        self.assertEqual({"notas": "Revisado"},
                         {k: v for k, v in rows[-1].after.items() if k not in stamps})

    def test_bad_applicability_and_unknown_fields_are_refused(self) -> None:
        scope = self.draft()
        no_reason = qs().JUSTIFICATION_REQUIRED.format(
            code="8.3", title="Diseño y desarrollo de los productos y servicios")
        cases = (
            ({"aplicabilidad": {"8.3": {"aplica": False}}}, no_reason),
            ({"aplicabilidad": {"8.3": {"aplica": False, "justificacion": "   "}}}, no_reason),
            ({"aplicabilidad": {"11.1": {"aplica": True}, "4.0": {"aplica": True}}},
             qs().UNKNOWN_REQUIREMENTS.format(codes="11.1, 4.0")),
            ({"aplicabilidad": ["8.3"]}, qs().APPLICABILITY_NOT_A_MAPPING),
            ({"aplicabilidad": None}, qs().APPLICABILITY_NOT_A_MAPPING),
            ({"aplicabilidad": {"8.3": False}}, qs().ENTRY_NOT_A_MAPPING.format(code="8.3")),
            ({"aplicabilidad": {"8.3": {"aplica": False, "motivo": "x"}}},
             qs().ENTRY_NOT_A_MAPPING.format(code="8.3")),
            ({"aplicabilidad": {"8.3": {"aplica": "no"}}}, qs().BAD_FLAG.format(code="8.3")),
            ({"aplicabilidad": {"8.3": {"aplica": 0}}}, qs().BAD_FLAG.format(code="8.3")),
            ({"aplicabilidad": {"8.3": {"aplica": False, "justificacion": 3}}},
             qs().BAD_JUSTIFICATION.format(code="8.3")),
            ({"declaracion": ""}, "El campo «declaracion» es obligatorio."),
            ({"centros": 3}, "El campo «centros» debe ser texto."),
            ({"version": 2}, "Campos no permitidos"),
            ({"estado": "vigente"}, "Campos no permitidos"),
            ("texto", qs().NOT_A_MAPPING),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertIn(expected, self.refused(errors().ValidationError,
                                                     qs().update_draft, CLI, scope.id, data))
        self.assertEqual(["create"], [r.action for r in self.rows()])
        self.assertEqual(applies(), self.entries(scope.id))

    def test_a_requirement_missing_from_a_draft_reads_as_applying_and_is_stored_on_edit(
            self) -> None:
        """OC-2 review: a catalogue code a draft does not store is backfilled, not a KeyError."""
        scope = self.draft()
        table = aplicabilidad_alcance
        db.session.execute(table.delete().where(table.c.alcance_id == scope.id,
                                                table.c.apartado.in_(("8.3", "10.3"))))
        db.session.commit()
        self.assertEqual(applies(), self.entries(scope.id))  # catalogue order, applying
        self.assertEqual(CODES, [r.apartado for r in qs().applicability(
            db.session, actor(OPERATIVO), scope.id)])
        self.edit(scope.id, {"aplicabilidad": {"8.3": {"aplica": True}}})  # nothing changes
        self.assertEqual(["create"], [r.action for r in self.rows()])
        stored = select(func.count()).select_from(table).where(table.c.alcance_id == scope.id)
        self.assertEqual(len(CODES) - 2, db.session.scalar(stored))
        self.edit(scope.id, {"aplicabilidad": {"8.3": EXCLUDED}})
        self.assertEqual(applies() | {"8.3": EXCLUDED}, self.entries(scope.id))
        self.assertEqual(len(CODES), db.session.scalar(stored))  # every missing code stored
        row = self.rows()[-1]
        self.assertEqual((applies(), applies() | {"8.3": EXCLUDED}),
                         (row.before["aplicabilidad"], row.after["aplicabilidad"]))
        db.session.execute(table.delete().where(table.c.alcance_id == scope.id,
                                                table.c.apartado == "4.1"))
        db.session.commit()
        self.edit(scope.id, {"notas": "Revisado"})  # a text edit stores the missing code too
        self.assertEqual(len(CODES), db.session.scalar(stored))
        who, _ = self.admin()
        self.approve(scope.id, who)
        self.assertEqual(applies() | {"8.3": EXCLUDED}, self.entries(self.draft(data={}).id))

    def test_a_draft_is_deleted_with_its_applicability_and_frees_its_number(self) -> None:
        scope = self.draft(data=SCOPE | {"aplicabilidad": {"8.3": EXCLUDED}})
        for who in (actor(OPERATIVO), mcp_actor(AUDITOR), actor(AUDITOR, scopes={"read"})):
            with self.subTest(denied=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, qs().delete_draft, who, scope.id)
        qs().delete_draft(db.session, actor(AUDITOR), scope.id)
        db.session.commit()
        self.assertEqual((0, 0), (db.session.query(AlcanceSGC).count(), db.session.scalar(
            select(func.count()).select_from(aplicabilidad_alcance))))
        row = self.rows()[-1]
        self.assertEqual(("delete", None, 1, applies() | {"8.3": EXCLUDED}),
                         (row.action, row.after, row.before["version"],
                          row.before["aplicabilidad"]))
        self.assertEqual(1, self.draft().version)
        self.refused(errors().NotFound, qs().delete_draft, CLI, 999)


class ApprovalTestCase(Helpers, ServiceBase):
    def test_approving_puts_the_draft_in_force_and_the_current_version_out(self) -> None:
        who, person = self.admin()
        first = self.draft(who, SCOPE | {"aplicabilidad": {"8.3": EXCLUDED}})
        # The administrator may approve the draft they wrote (small organizations).
        self.approve(first.id, who)
        self.assertEqual((EstadoAlcance.vigente, person, TODAY, None),
                         (first.estado, first.aprobado_por_id, first.fecha_aprobacion,
                          first.fecha_obsolescencia))
        self.assertEqual(first.id, qs().current(db.session, actor(OPERATIVO)).id)
        # A new draft starts as a copy of the current version, with the changes given.
        second = self.draft(actor(AUDITOR), {"notas": "Revisión anual"})
        self.assertEqual((2, SCOPE["declaracion"], "Planta de Burgos", "Revisión anual"),
                         (second.version, second.declaracion, second.centros, second.notas))
        self.assertEqual(applies() | {"8.3": EXCLUDED}, self.entries(second.id))
        later = TODAY + timedelta(days=30)
        self.approve(second.id, who, today=later)
        self.assertEqual((EstadoAlcance.obsoleto, later, TODAY),
                         (first.estado, first.fecha_obsolescencia, first.fecha_aprobacion))
        self.assertEqual((EstadoAlcance.vigente, later), (second.estado,
                                                          second.fecha_aprobacion))
        self.assertEqual([second.id, first.id],
                         [s.id for s in qs().history(db.session, actor(OPERATIVO))])
        self.assertEqual(
            [("create", first.id), ("update", first.id), ("create", second.id),
             ("update", first.id), ("update", second.id)],
            [(r.action, r.entity_id) for r in self.rows()])
        obsoleted = self.rows()[3]
        self.assertEqual(({"estado": "Vigente", "fecha_obsolescencia": None},
                          {"estado": "Obsoleto", "fecha_obsolescencia": later.isoformat()}),
                         ({k: obsoleted.before[k] for k in ("estado", "fecha_obsolescencia")},
                          {k: obsoleted.after[k] for k in ("estado", "fecha_obsolescencia")}))
        self.assertEqual({"estado": "Vigente", "aprobado_por_id": person,
                          "fecha_aprobacion": later.isoformat()},
                         {k: self.rows()[4].after[k] for k in ("estado", "aprobado_por_id",
                                                               "fecha_aprobacion")})

    def test_only_administrators_outside_mcp_approve_drafts(self) -> None:
        who, _ = self.admin()
        scope = self.draft()
        for denied in (actor(AUDITOR), actor(OPERATIVO), mcp_actor(ADMIN),
                       actor(ADMIN, scopes={"read"})):
            with self.subTest(denied=denied.role.name, channel=denied.channel):
                self.assertFalse(qs().may_approve(denied))
                self.refused(errors().PermissionDenied, qs().approve, denied, scope.id,
                             today=TODAY)
        self.assertTrue(qs().may_approve(who))
        self.assertEqual(qs().APPROVER_REQUIRED, self.refused(
            errors().ValidationError, qs().approve, actor(ADMIN, user_id=99), scope.id,
            today=TODAY))
        self.refused(errors().NotFound, qs().approve, who, 999, today=TODAY)
        with self.assertRaises(ValueError):
            qs().approve(db.session, who, scope.id, today=datetime(2026, 10, 22, 9))
        db.session.rollback()
        self.assertEqual(["create"], [r.action for r in self.rows()])
        self.approve(scope.id, who)
        self.assertEqual(qs().NOT_A_DRAFT, self.refused(
            errors().ValidationError, qs().approve, who, scope.id, today=TODAY))

    def test_current_and_obsolete_versions_never_change(self) -> None:
        who, _ = self.admin()
        old = self.draft()
        self.approve(old.id, who)
        current = self.draft()
        self.approve(current.id, who)
        for scope_id in (old.id, current.id):
            for data in ({"notas": "Cambio"}, {"aplicabilidad": {"8.3": EXCLUDED}}):
                with self.subTest(scope=scope_id, data=data):
                    self.assertEqual(qs().IMMUTABLE, self.refused(
                        errors().ValidationError, qs().update_draft, CLI, scope_id, data))
            with self.subTest(scope=scope_id, delete=True):
                self.assertEqual(qs().ONLY_DRAFTS_DELETED, self.refused(
                    errors().ValidationError, qs().delete_draft, CLI, scope_id))
        self.assertEqual(5, len(self.rows()))  # two creations, two approvals, one obsoleted

    def test_people_who_approved_a_version_are_not_deleted(self) -> None:
        who, person = self.admin()
        self.approve(self.draft().id, who)
        self.assertEqual(people().STILL_REFERENCED, self.refused(
            errors().Conflict, people().delete, CLI, person))


class ReadTestCase(Helpers, ServiceBase):
    def test_every_role_reads_every_version_and_the_applicability(self) -> None:
        who, _ = self.admin()
        for role in (ADMIN, AUDITOR, OPERATIVO):
            with self.subTest(empty=role.name):
                self.assertEqual((None, None, []), (
                    qs().current(db.session, actor(role)), qs().draft(db.session, actor(role)),
                    qs().history(db.session, actor(role))))
        current = self.draft()
        self.approve(current.id, who)
        pending = self.draft(data={"aplicabilidad": {"8.3": EXCLUDED}})
        for role in (ADMIN, AUDITOR, OPERATIVO):
            reader = actor(role)
            with self.subTest(read=role.name):
                self.assertEqual((current.id, pending.id, [pending.id, current.id]), (
                    qs().current(db.session, reader).id, qs().draft(db.session, reader).id,
                    [s.id for s in qs().history(db.session, reader)]))
                self.assertIs(pending, qs().get(db.session, reader, pending.id))
                rows, total = qs().list_page(db.session, reader, estado="borrador")
                self.assertEqual(([pending.id], 1), ([s.id for s in rows], total))
        requirements = qs().applicability(db.session, actor(OPERATIVO), pending.id)
        self.assertEqual(CODES, [r.apartado for r in requirements])
        design = requirements[CODES.index("8.3")]
        self.assertEqual(("8.3", "Diseño y desarrollo de los productos y servicios", False,
                          EXCLUDED["justificacion"]),
                         (design.apartado, design.titulo, design.aplica, design.justificacion))
        for unknown in (999, 2 ** 40, "1"):
            with self.subTest(unknown=unknown):
                self.refused(errors().NotFound, qs().get, CLI, unknown)
                self.refused(errors().NotFound, qs().applicability, CLI, unknown)
        self.refused(errors().ValidationError, qs().list_page, CLI, estado="aprobado")
        denied = actor(AUDITOR, scopes={"write"})
        for call, args in ((qs().current, ()), (qs().history, ()), (qs().get, (current.id,))):
            with self.subTest(denied=call.__name__):
                self.refused(errors().PermissionDenied, call, denied, *args)


class DatabaseCases:
    """The schema keeps one draft and one current version and the approval data itself."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def scopes(self) -> int:
        """An obsolete, a current and a draft version, approved by a person; returns the draft."""
        ana = self.person()
        table = AlcanceSGC.__table__
        self.insert(table, version=1, estado=EstadoAlcance.obsoleto, declaracion="Uno",
                    aprobado_por_id=ana, fecha_aprobacion=date(2025, 1, 1),
                    fecha_obsolescencia=date(2026, 1, 1))
        self.insert(table, version=2, estado=EstadoAlcance.vigente, declaracion="Dos",
                    aprobado_por_id=ana, fecha_aprobacion=date(2026, 1, 1))
        draft = self.insert(table, version=3, estado=EstadoAlcance.borrador, declaracion="Tres")
        self.insert(aplicabilidad_alcance, alcance_id=draft, apartado="4.1", aplica=True)
        return draft

    def test_the_schema_keeps_one_draft_one_current_version_and_the_approval(self) -> None:
        self.enforce_foreign_keys()
        draft = self.scopes()
        params = {"d": draft, "no": False, "yes": True}
        for statement in (
            "INSERT INTO alcances_sgc (version, estado, declaracion) "
            "VALUES (4, 'borrador', 'Otro')",  # a second draft
            "INSERT INTO alcances_sgc (version, estado, declaracion, aprobado_por_id, "
            "fecha_aprobacion) VALUES (4, 'vigente', 'Otro', 1, '2026-10-01')",  # second current
            "UPDATE alcances_sgc SET version = 2 WHERE version = 3",
            "UPDATE alcances_sgc SET version = 0 WHERE version = 3",
            "UPDATE alcances_sgc SET estado = 'Borrador' WHERE version = 3",
            "UPDATE alcances_sgc SET estado = 'aprobado' WHERE version = 3",
            "UPDATE alcances_sgc SET declaracion = NULL WHERE version = 3",
            "UPDATE alcances_sgc SET aprobado_por_id = NULL WHERE version = 2",
            "UPDATE alcances_sgc SET fecha_aprobacion = NULL WHERE version = 1",
            "UPDATE alcances_sgc SET fecha_aprobacion = '2026-10-01' WHERE version = 3",
            "UPDATE alcances_sgc SET fecha_obsolescencia = NULL WHERE version = 1",
            "UPDATE alcances_sgc SET fecha_obsolescencia = '2026-10-01' WHERE version = 2",
            "UPDATE alcances_sgc SET aprobado_por_id = 999 WHERE version = 2",
            "INSERT INTO aplicabilidad_alcance (alcance_id, apartado, aplica) "
            "VALUES (:d, '8.3', :no)",  # does not apply, without a justification
            "INSERT INTO aplicabilidad_alcance (alcance_id, apartado, aplica, justificacion) "
            "VALUES (:d, '8.3', :no, '   ')",
            "INSERT INTO aplicabilidad_alcance (alcance_id, apartado, aplica) "
            "VALUES (:d, '4.1', :yes)",  # the same requirement twice
            "INSERT INTO aplicabilidad_alcance (alcance_id, apartado, aplica) "
            "VALUES (999, '4.1', :yes)",
            "UPDATE aplicabilidad_alcance SET aplica = NULL",
            "DELETE FROM personas",  # approved two versions
        ):
            with self.subTest(refused=statement):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement), params)
                    db.session.commit()
                db.session.rollback()
        for statement in (
            "INSERT INTO alcances_sgc (version, estado, declaracion, aprobado_por_id, "
            "fecha_aprobacion, fecha_obsolescencia) VALUES (4, 'obsoleto', 'Cuatro', 1, "
            "'2024-01-01', '2025-01-01')",  # obsolete versions are many
            "INSERT INTO aplicabilidad_alcance (alcance_id, apartado, aplica, justificacion) "
            "VALUES (:d, '8.3', :no, 'No diseñamos')",
            "DELETE FROM alcances_sgc WHERE version = 3",  # its applicability goes with it
        ):
            with self.subTest(accepted=statement):
                db.session.execute(text(statement), params)
                db.session.commit()
        self.assertEqual(0, db.session.scalar(
            select(func.count()).select_from(aplicabilidad_alcance)))

    def test_a_concurrent_draft_meets_the_index_and_is_a_conflict(self) -> None:
        self.enforce_foreign_keys()
        self.draft()
        with patch.object(qs(), "_draft", return_value=None):  # the check missed it
            with self.assertRaises(errors().Conflict) as caught:
                qs().new_draft(db.session, CLI, SCOPE)
        db.session.rollback()
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        self.assertEqual(qs().CONFLICT, caught.exception.message)
        self.assertEqual(1, db.session.query(AlcanceSGC).count())

    def test_the_service_round_trip_runs_on_the_engine(self) -> None:
        self.enforce_foreign_keys()
        who, _ = self.admin()
        first = self.draft(data=SCOPE | {"aplicabilidad": {"8.3": EXCLUDED}})
        self.approve(first.id, who)
        second = self.draft(data={})
        self.edit(second.id, {"aplicabilidad": {"8.3": {"aplica": True}}})
        self.approve(second.id, who)
        third = self.draft(data={})
        qs().delete_draft(db.session, CLI, third.id)
        db.session.commit()
        self.assertEqual([(2, EstadoAlcance.vigente), (1, EstadoAlcance.obsoleto)],
                         [(s.version, s.estado) for s in qs().history(db.session, CLI)])
        self.assertEqual(applies() | {"8.3": EXCLUDED}, self.entries(first.id))
        self.assertEqual(applies() | {"8.3": EXCLUDED | {"aplica": True}},
                         self.entries(second.id))


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
    """The same cases against PostgreSQL, where the partial unique indexes matter most."""

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
    def admin(self) -> Actor:
        user = User(username="direccion", password="x", role=ADMIN)
        db.session.add(user)
        db.session.commit()
        db.session.add(Person(nombre="Dirección", user_id=user.id))
        db.session.commit()
        return Actor(user_id=user.id, label="admin", role=ADMIN, channel="web")

    async def test_drafts_round_trip_and_approval_stays_on_the_web(self) -> None:
        who = mcp_actor(AUDITOR)
        payload = SCOPE | {"aplicabilidad": {"8.3": EXCLUDED}}
        created = await self.call(who, "qms_create", {"module": "alcances_sgc", "data": payload})
        self.assertFalse(created.is_error, created.content)
        scope = created.structured_content
        self.assertEqual((1, "Borrador", SCOPE["declaracion"], None, 38, EXCLUDED),
                         (scope["version"], scope["estado"], scope["declaracion"],
                          scope["aprobado_por_id"], len(scope["aplicabilidad"]),
                          scope["aplicabilidad"]["8.3"]))
        updated = await self.call(who, "qms_update", {
            "module": "alcances_sgc", "id": scope["id"],
            "data": {"centros": "Burgos", "aplicabilidad": {"7.1.5": {
                "aplica": False, "justificacion": "Sin equipos de medición"}}}})
        self.assertFalse(updated.is_error, updated.content)
        self.assertEqual(("Burgos", False), (updated.structured_content["centros"],
                                             updated.structured_content["aplicabilidad"]
                                             ["7.1.5"]["aplica"]))
        for slug_call, data, word in (
                ("qms_update", {"aplicabilidad": {"8.2": {"aplica": False}}}, "Justifica"),
                ("qms_update", {"aplicabilidad": {"11.1": {"aplica": True}}}, "11.1"),
                ("qms_update", {"estado": "vigente"}, "Campos no permitidos"),
                ("qms_create", SCOPE, "borrador")):
            with self.subTest(refused=data):
                arguments = {"module": "alcances_sgc", "data": data}
                if slug_call == "qms_update":
                    arguments["id"] = scope["id"]
                result = await self.call(who, slug_call, arguments)
                self.assertTrue(result.is_error)
                self.assertIn(word, result.content[0].text)
        denied = await self.call(mcp_actor(OPERATIVO), "qms_update", {
            "module": "alcances_sgc", "id": scope["id"], "data": {"notas": "x"}})
        self.assertIn("No tienes permiso", denied.content[0].text)
        listed = await self.call(mcp_actor(OPERATIVO), "qms_list", {
            "module": "alcances_sgc", "filters": {"estado": "borrador"}})
        self.assertEqual(1, listed.structured_content["total"])
        # Approval is web-only: the administrator approves through the service.
        from app.services import qms_scope

        qms_scope.approve(db.session, self.admin(), scope["id"], today=TODAY)
        db.session.commit()
        frozen = await self.call(who, "qms_update", {
            "module": "alcances_sgc", "id": scope["id"], "data": {"notas": "Cambio"}})
        self.assertTrue(frozen.is_error)
        self.assertIn(qms_scope.IMMUTABLE, frozen.content[0].text)
        copied = await self.call(who, "qms_create", {"module": "alcances_sgc", "data": {}})
        self.assertFalse(copied.is_error, copied.content)
        self.assertEqual((2, "Burgos", EXCLUDED),
                         (copied.structured_content["version"],
                          copied.structured_content["centros"],
                          copied.structured_content["aplicabilidad"]["8.3"]))
        got = await self.call(mcp_actor(OPERATIVO), "qms_get",
                              {"module": "alcances_sgc", "id": scope["id"]})
        self.assertEqual(("Vigente", TODAY.isoformat()), (got.structured_content["estado"],
                                                          got.structured_content[
                                                              "fecha_aprobacion"]))
        modules = (await self.call(mcp_actor(OPERATIVO), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}["alcances_sgc"]
        self.assertEqual({"declaracion": "string", "productos_servicios": "string",
                          "centros": "string", "notas": "string", "aplicabilidad": "object"},
                         {f["name"]: f["type"] for f in described["fields"]})
        self.assertFalse(any(f["required"] for f in described["fields"]))
        self.assertEqual(["estado"], [f["name"] for f in described["filters"]])
        self.assertEqual(dict(read=True, create=False, update=False), described["permissions"])


if __name__ == "__main__":
    unittest.main()
