"""Complaint transitions, audit history and database backstops (SQLite/PostgreSQL)."""

import os
import sys
from pathlib import Path
import unittest

# Existing shared fixtures use top-level imports under unittest discovery.
if __package__ == 'tests':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from datetime import date, timedelta

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, actor, errors
from app.extensions import db
from app.models import Cliente, EstadoReclamacion as E, NoConformidad, Reclamacion, RoleEnum
from app.services import reclamaciones

TODAY = date(2026, 10, 18)
POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")


class ComplaintTestCase(ServiceBase):
    def setUp(self):
        super().setUp()
        self.customer_id = db.session.execute(Cliente.__table__.insert().values(
            nombre="Farmacia Sol")).inserted_primary_key[0]
        db.session.commit()

    def create(self, **values):
        found = reclamaciones.create(db.session, actor(),
            {"cliente_id": self.customer_id, "descripcion": "Retraso"} | values, today=TODAY)
        db.session.commit()
        return found

    def test_response_close_reopen_preserve_the_response_and_history(self):
        found = self.create()
        self.assertEqual((E.recibida, TODAY), (found.estado, found.fecha_recepcion))
        reclamaciones.respond(db.session, actor(), found.id, "  Enviado  ", today=TODAY)
        db.session.commit()
        self.assertEqual((E.respondida, "Enviado", TODAY),
                         (found.estado, found.respuesta, found.fecha_respuesta))
        reclamaciones.close(db.session, actor(), found.id, today=TODAY)
        db.session.commit()
        self.assertEqual((E.cerrada, TODAY), (found.estado, found.fecha_cierre))
        reclamaciones.reopen(db.session, actor(RoleEnum.ADMINISTRADOR), found.id)
        db.session.commit()
        self.assertEqual((E.respondida, None, "Enviado"),
                         (found.estado, found.fecha_cierre, found.respuesta))
        history = self.audit_rows()
        self.assertEqual(["create", "update", "update", "update"], [r.action for r in history])
        self.assertEqual(TODAY.isoformat(), history[-1].before["fecha_cierre"])

    def test_cancel_only_received_and_admin_reopen_clears_reason(self):
        found = self.create()
        reclamaciones.cancel(db.session, actor(), found.id, " Duplicada ")
        db.session.commit()
        self.assertEqual((E.cancelada, "Duplicada", None),
                         (found.estado, found.motivo_cancelacion, found.fecha_cierre))
        for role in (RoleEnum.OPERATIVO, RoleEnum.AUDITOR):
            with self.assertRaises(errors().PermissionDenied):
                reclamaciones.reopen(db.session, actor(role), found.id)
        reclamaciones.reopen(db.session, actor(RoleEnum.ADMINISTRADOR), found.id)
        db.session.commit()
        self.assertEqual((E.recibida, None), (found.estado, found.motivo_cancelacion))
        self.assertEqual("Duplicada", self.audit_rows()[-1].before["motivo_cancelacion"])

    def test_guards_on_every_state_and_required_text(self):
        found = self.create()
        for call in (
            lambda: reclamaciones.close(db.session, actor(), found.id, today=TODAY),
            lambda: reclamaciones.reopen(db.session, actor(RoleEnum.ADMINISTRADOR), found.id),
            lambda: reclamaciones.respond(db.session, actor(), found.id, " ", today=TODAY),
            lambda: reclamaciones.cancel(db.session, actor(), found.id, " "),
        ):
            with self.assertRaises(errors().ValidationError):
                call()
        reclamaciones.respond(db.session, actor(), found.id, "Enviado", today=TODAY)
        db.session.commit()
        with self.assertRaises(errors().ValidationError):
            reclamaciones.cancel(db.session, actor(), found.id, "Duplicada")
        with self.assertRaises(errors().ValidationError):
            reclamaciones.respond(db.session, actor(), found.id, "Otra", today=TODAY)
        reclamaciones.close(db.session, actor(), found.id, today=TODAY)
        db.session.commit()
        for call in (
            lambda: reclamaciones.close(db.session, actor(), found.id, today=TODAY),
            lambda: reclamaciones.cancel(db.session, actor(), found.id, "Duplicada"),
            lambda: reclamaciones.update(db.session, actor(), found.id,
                                         {"descripcion": "Otra"}, today=TODAY),
        ):
            with self.assertRaises(errors().ValidationError):
                call()

    def test_dates_and_read_only_lifecycle_columns(self):
        for values in ({"fecha_recepcion": TODAY + timedelta(days=1)},
                       {"fecha_recepcion": "2026-10-18"}, {"cliente_id": 999},
                       {"cliente_id": True}, {"canal": "Web"}, {"estado": "cerrada"},
                       {"respuesta": "Bypass"}, {"fecha_cierre": TODAY},
                       {"descripcion": " "}, {"no_conformidad_id": 999}):
            with self.subTest(values=values), self.assertRaises(errors().ValidationError):
                self.create(**values)
            db.session.rollback()
        found = self.create()
        with self.assertRaises(errors().ValidationError):
            reclamaciones.respond(db.session, actor(), found.id, "Enviado",
                                   today=TODAY - timedelta(days=1))
        reclamaciones.respond(db.session, actor(), found.id, "Enviado", today=TODAY)
        db.session.commit()
        with self.assertRaises(errors().ValidationError):
            reclamaciones.close(db.session, actor(), found.id, today=TODAY - timedelta(days=1))
        with self.assertRaises(errors().ValidationError):
            reclamaciones.update(db.session, actor(), found.id,
                {"fecha_recepcion": TODAY + timedelta(days=1)}, today=TODAY + timedelta(days=1))

    def test_mcp_cannot_transition_even_with_write_scope(self):
        found = self.create()
        for call, args, kwargs in (
            (reclamaciones.respond, ("Respuesta",), {"today": TODAY}),
            (reclamaciones.close, (), {"today": TODAY}),
            (reclamaciones.cancel, ("Motivo",), {}),
            (reclamaciones.reopen, (), {}),
        ):
            with self.assertRaises(errors().PermissionDenied):
                call(db.session, actor(RoleEnum.ADMINISTRADOR, channel="mcp"), found.id,
                     *args, **kwargs)

    def test_optional_nonconformity_link_noop_and_filters(self):
        nc_id = db.session.execute(NoConformidad.__table__.insert().values(
            descripcion="Retraso", fecha_detectada=TODAY)).inserted_primary_key[0]
        db.session.commit()
        found = self.create(no_conformidad_id=nc_id, canal="web")
        other = self.create(fecha_recepcion=TODAY - timedelta(days=1))
        self.assertEqual(nc_id, found.no_conformidad_id)
        reclamaciones.update(db.session, actor(), found.id, {"descripcion": "Retraso"}, today=TODAY)
        db.session.commit()
        self.assertEqual(2, len(self.audit_rows()))
        self.assertEqual(([found], 1), reclamaciones.list_page(db.session, actor(),
                         cliente_id=self.customer_id, canal="web", estado=E.recibida, per_page=1))
        self.assertEqual([found, other], reclamaciones.list_(db.session, actor()))
        self.assertEqual([], reclamaciones.list_(db.session, actor(), cliente_id=2 ** 40))
        with self.assertRaises(errors().ValidationError):
            reclamaciones.list_(db.session, actor(), estado="Recibida")
        with self.assertRaises(errors().NotFound):
            reclamaciones.respond(db.session, actor(), 999, "Respuesta", today=TODAY)


class DatabaseCases:
    def test_check_constraints_and_restrict_links(self):
        with self.app.app_context():
            if db.engine.dialect.name == "sqlite":
                db.session.execute(text("PRAGMA foreign_keys=ON"))
            customer = db.session.execute(Cliente.__table__.insert().values(
                nombre="ACME")).inserted_primary_key[0]
            nc = db.session.execute(NoConformidad.__table__.insert().values(
                descripcion="Retraso", fecha_detectada=TODAY)).inserted_primary_key[0]
            complaint = db.session.execute(Reclamacion.__table__.insert().values(
                cliente_id=customer, descripcion="Retraso", fecha_recepcion=TODAY,
                no_conformidad_id=nc)).inserted_primary_key[0]
            db.session.commit()
            for statement in (
                "UPDATE reclamaciones SET estado='respondida'",
                "UPDATE reclamaciones SET respuesta='Sin fecha'",
                "UPDATE reclamaciones SET fecha_respuesta='2026-10-18'",
                "UPDATE reclamaciones SET fecha_cierre='2026-10-18'",
                "UPDATE reclamaciones SET estado='cancelada'",
                "UPDATE reclamaciones SET motivo_cancelacion='Sin cancelar'",
                "UPDATE reclamaciones SET estado='Recibida'",
                "UPDATE reclamaciones SET canal='Web'",
                "UPDATE reclamaciones SET cliente_id=999",
                "UPDATE reclamaciones SET no_conformidad_id=999",
                "DELETE FROM clientes", "DELETE FROM no_conformidades",
            ):
                with self.subTest(statement=statement), self.assertRaises(IntegrityError):
                    db.session.execute(text(statement))
                    db.session.commit()
                db.session.rollback()
            db.session.execute(text("UPDATE reclamaciones SET estado='respondida', "
                "respuesta='Enviado', fecha_respuesta='2026-10-18' WHERE id=:id"), {"id": complaint})
            db.session.commit()
            with self.assertRaises(IntegrityError):
                db.session.execute(text("UPDATE reclamaciones SET estado='cerrada'"))
                db.session.commit()
            db.session.rollback()
            db.session.execute(text("UPDATE reclamaciones SET estado='cerrada', "
                "fecha_cierre='2026-10-18'"))
            db.session.commit()
            self.assertEqual(E.cerrada, db.session.get(Reclamacion, complaint).estado)


class SQLiteComplaintConstraintsTestCase(DatabaseCases, unittest.TestCase):
    def setUp(self):
        self.app = bootstrap.build_app()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()


@unittest.skipUnless(POSTGRES_URI, "TEST_POSTGRES_URI is not set")
class PostgresComplaintConstraintsTestCase(SQLiteComplaintConstraintsTestCase):
    def setUp(self):
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        with self.app.app_context():
            db.create_all()
