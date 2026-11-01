"""Improvement origins, lifecycle and database invariants (ISO 9001 10.1/10.3)."""

from datetime import date, timedelta
import os
import unittest

import importlib.util
from pathlib import Path
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import test_auth_bootstrap as bootstrap
from test_nonconformity_service import ServiceBase, actor
from app.extensions import db
from app.models import (
    AccionRevision, Auditoria, AuditLog, EstadoMejora, HallazgoAuditoria, Mejora,
    MEJORA_LINKS, ObjetivoCalidad, Oportunidad, Person, RevisionDireccion, RoleEnum, User,
)
from app.services import improvements as service, people
from app.services.errors import Conflict, PermissionDenied, ValidationError

DAY = date(2026, 10, 5)
DATA = {"descripcion": "Automatizar el etiquetado", "fecha_identificacion": DAY}
POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")


class ImprovementCases:
    def insert(self, model, **values):
        result = db.session.execute(model.__table__.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def user(self, name):
        uid = self.insert(User, username=name, password="x", role=RoleEnum.OPERATIVO)
        pid = self.insert(Person, nombre=name, user_id=uid, activo=True)
        return actor(user_id=uid), pid

    def create(self, **values):
        found = service.create(db.session, actor(user_id=None), DATA | values, today=DAY)
        db.session.commit()
        return found

    def refused(self, error, call, *args, **kwargs):
        before = [service.audit.snapshot(row) for row in service.list_(db.session, actor())]
        logs = db.session.query(AuditLog).count()
        with self.assertRaises(error):
            call(*args, **kwargs)
        # Validation must not change either records or audit history, even before rollback.
        self.assertEqual(before, [service.audit.snapshot(row)
                                  for row in service.list_(db.session, actor())])
        self.assertEqual(logs, db.session.query(AuditLog).count())
        db.session.rollback()

    def test_required_fields_dates_and_readonly_columns_leave_no_trace(self):
        bad = ({}, DATA | {"descripcion": " "}, DATA | {"fecha_identificacion": DAY + timedelta(days=1)},
               DATA | {"fecha_identificacion": "2026-10-05"}, DATA | {"responsable_id": 999},
               DATA | {"no_conformidad": "legacy"}, DATA | {"estado": "aprobada"},
               DATA | {"fecha_implementacion": DAY})
        for data in bad:
            with self.subTest(data=data):
                self.refused(ValidationError, service.create, db.session, actor(), data, today=DAY)
        row = self.create(beneficio=" ")
        self.assertEqual(("otro", "propuesta", None, None),
                         (row.origen.name, row.estado.name, row.beneficio, row.fecha_implementacion))
        logs = db.session.query(AuditLog).count()
        service.update(db.session, actor(), row.id_mejora, {"beneficio": ""}, today=DAY)
        self.assertEqual(logs, db.session.query(AuditLog).count())
        self.refused(ValidationError, service.update, db.session, actor(), row.id_mejora,
                     {"fecha_identificacion": DAY + timedelta(days=1)}, today=DAY)

    def test_each_origin_refuses_wrong_and_unknown_links(self):
        for column, (origin, _) in MEJORA_LINKS.items():
            with self.subTest(column=column):
                self.refused(ValidationError, service.create, db.session, actor(),
                             DATA | {column: 999}, today=DAY)
                self.refused(ValidationError, service.create, db.session, actor(),
                             DATA | {"origen": origin.name, column: 999}, today=DAY)
        nc_id = self.insert(service.NoConformidad, descripcion="NC", fecha_detectada=DAY)
        row = self.create(origen="no_conformidad", no_conformidad_id=nc_id)
        self.refused(ValidationError, service.update, db.session, actor(), row.id_mejora,
                     {"origen": "otro"}, today=DAY)
        service.update(db.session, actor(user_id=None), row.id_mejora,
                       {"origen": "otro", "no_conformidad_id": None}, today=DAY)
        self.assertIsNone(row.no_conformidad_id)

    def test_all_five_origin_links_round_trip_and_database_refuses_a_mismatched_origin(self):
        owner = self.insert(Person, nombre='Owner', activo=True)
        audit_id = self.insert(Auditoria, area_auditada='Calidad', fecha=DAY, auditor='Ana', resultado='OK')
        review_id = self.insert(RevisionDireccion, fecha=DAY, periodo_desde=DAY, periodo_hasta=DAY)
        links = {
            'no_conformidad_id': self.insert(service.NoConformidad, descripcion='NC', fecha_detectada=DAY),
            'hallazgo_id': self.insert(HallazgoAuditoria, auditoria_id=audit_id,
                                      tipo='oportunidad_mejora', descripcion='Mejorar'),
            'accion_revision_id': self.insert(AccionRevision, revision_id=review_id, tipo='mejora',
                                             descripcion='Mejorar', responsable_id=owner, fecha_limite=DAY),
            'oportunidad_id': self.insert(Oportunidad, titulo='Oportunidad', descripcion='Mejorar',
                                          origen='interno', responsable_id=owner, fecha_identificacion=DAY),
            'objetivo_id': self.insert(ObjetivoCalidad, titulo='Objetivo', indicador='Errores', meta=1,
                                       sentido='menor_igual', responsable_id=owner, anio=2026,
                                       fecha_inicio=DAY, fecha_limite=DAY, frecuencia='mensual'),
        }
        for column, value in links.items():
            origin = MEJORA_LINKS[column][0]
            row = self.create(origen=origin.name, **{column: value})
            self.assertEqual(value, getattr(service.get(db.session, actor(), row.id_mejora), column))
            with self.assertRaises(IntegrityError):
                db.session.execute(Mejora.__table__.update().where(
                    Mejora.id_mejora == row.id_mejora).values(origen='otro'))
                db.session.commit()
            db.session.rollback()

    def test_lifecycle_is_audited_and_independent_and_dates_are_real(self):
        owner, owner_id = self.user("owner")
        reviewer, reviewer_id = self.user("reviewer")
        row = self.create(responsable_id=owner_id)
        self.refused(ValidationError, service.implement, db.session, owner, row.id_mejora,
                     fecha=DAY, today=DAY)
        service.approve(db.session, owner, row.id_mejora, today=DAY)
        db.session.commit()
        self.assertEqual((owner_id, DAY), (row.aprobado_por_id, row.fecha_aprobacion))
        self.refused(ValidationError, service.approve, db.session, owner, row.id_mejora, today=DAY)
        for when in (DAY - timedelta(days=1), DAY + timedelta(days=1), None):
            self.refused(ValidationError, service.implement, db.session, owner, row.id_mejora,
                         fecha=when, today=DAY)
        service.implement(db.session, owner, row.id_mejora, fecha=DAY, today=DAY)
        db.session.commit()
        self.assertEqual(DAY, row.fecha_implementacion)
        for who, result, comment, when in (
                (owner, "eficaz", None, DAY), (reviewer, "no_eficaz", " ", DAY),
                (reviewer, "eficaz", None, DAY - timedelta(days=1)),
                (reviewer, "eficaz", None, DAY + timedelta(days=1))):
            self.refused(ValidationError, service.review_effectiveness, db.session, who,
                         row.id_mejora, eficacia=result, comentario=comment, fecha=when, today=DAY)
        service.review_effectiveness(db.session, reviewer, row.id_mejora, eficacia="no_eficaz",
                                     comentario="  Sigue habiendo errores  ", fecha=DAY, today=DAY)
        db.session.commit()
        self.assertEqual((EstadoMejora.eficacia_revisada, reviewer_id, DAY,
                          "Sigue habiendo errores"),
                         (row.estado, row.revisor_eficacia_id, row.fecha_eficacia,
                          row.comentario_eficacia))
        self.assertEqual(["create", "update", "update", "update"],
                         [log.action for log in db.session.query(AuditLog).filter_by(entity_type="mejoras")])
        self.refused(Conflict, people.delete, db.session,
                     actor(RoleEnum.ADMINISTRADOR, user_id=None), reviewer_id)

    def test_transition_matrix_and_web_only_policy(self):
        who, _ = self.user("operator")
        for state in EstadoMejora:
            row = self.create()
            if state is not EstadoMejora.propuesta:
                service.approve(db.session, who, row.id_mejora, today=DAY)
            if state in (EstadoMejora.implantada, EstadoMejora.eficacia_revisada):
                service.implement(db.session, who, row.id_mejora, fecha=DAY, today=DAY)
            if state is EstadoMejora.eficacia_revisada:
                service.review_effectiveness(db.session, who, row.id_mejora,
                                             eficacia="eficaz", fecha=DAY, today=DAY)
            if state is EstadoMejora.cancelada:
                service.cancel(db.session, who, row.id_mejora, motivo="Duplicada", today=DAY)
            db.session.commit()
            calls = (
                (service.approve, {"today": DAY}, {EstadoMejora.propuesta}),
                (service.implement, {"fecha": DAY, "today": DAY}, {EstadoMejora.aprobada}),
                (service.review_effectiveness, {"eficacia": "eficaz", "fecha": DAY, "today": DAY},
                 {EstadoMejora.implantada}),
                (service.cancel, {"motivo": "Duplicada", "today": DAY},
                 {EstadoMejora.propuesta, EstadoMejora.aprobada}),
            )
            for call, kwargs, allowed in calls:
                with self.subTest(state=state, call=call.__name__):
                    if state not in allowed:
                        self.refused(ValidationError, call, db.session, who, row.id_mejora, **kwargs)
                    for denied in (actor(channel="mcp"), actor(scopes={"read"})):
                        self.refused(PermissionDenied, call, db.session, denied, row.id_mejora, **kwargs)
        for approved in (False, True):
            row = self.create()
            if approved:
                service.approve(db.session, who, row.id_mejora, today=DAY)
                db.session.commit()
            self.refused(ValidationError, service.cancel, db.session, who, row.id_mejora,
                         motivo=" ", today=DAY)
            service.cancel(db.session, who, row.id_mejora, motivo="Duplicada", today=DAY)
            db.session.commit()
            self.assertEqual((EstadoMejora.cancelada, "Duplicada", DAY),
                             (row.estado, row.motivo_cancelacion, row.fecha_cancelacion))
            self.assertEqual(DAY if approved else None, row.fecha_aprobacion)

    def test_missing_user_person_and_inactive_owner_are_friendly_atomic_errors(self):
        row = self.create()
        self.refused(ValidationError, service.approve, db.session, actor(user_id=None),
                     row.id_mejora, today=DAY)
        inactive = self.insert(Person, nombre='Inactive', activo=False)
        self.refused(ValidationError, service.update, db.session, actor(user_id=None), row.id_mejora,
                     {'responsable_id': inactive}, today=DAY)
        who, owner_id = self.user('Owner')
        reviewer, reviewer_id = self.user('Reviewer')
        service.update(db.session, who, row.id_mejora, {'responsable_id': owner_id}, today=DAY)
        service.approve(db.session, who, row.id_mejora, today=DAY)
        service.implement(db.session, who, row.id_mejora, fecha=DAY, today=DAY)
        db.session.commit()
        self.refused(ValidationError, service.review_effectiveness, db.session, actor(user_id=None),
                     row.id_mejora, eficacia='eficaz', fecha=DAY, today=DAY)
        service.review_effectiveness(db.session, reviewer, row.id_mejora, eficacia='eficaz',
                                     fecha=DAY, today=DAY)
        db.session.commit()
        self.refused(ValidationError, service.update, db.session, who, row.id_mejora,
                     {'responsable_id': reviewer_id}, today=DAY)

    def test_filters_and_existing_open_register_permissions(self):
        owner = self.insert(Person, nombre="Ana", activo=True)
        row = self.create(responsable_id=owner)
        self.create(origen="oportunidad")
        for role in RoleEnum:
            who = actor(role, user_id=None)
            self.assertEqual([row], service.list_(db.session, who, estado="propuesta",
                                                  origen="otro", responsable_id=owner))
            self.assertEqual(([row], 1), service.list_page(db.session, who, responsable_id=owner))
            service.update(db.session, who, row.id_mejora, {"beneficio": role.name}, today=DAY)
            db.session.commit()
        self.refused(Conflict, people.delete, db.session,
                     actor(RoleEnum.ADMINISTRADOR, user_id=None), owner)

    def test_database_checks_cover_every_link_and_lifecycle_column(self):
        row = self.create()
        invalid = [{column: 1} for column in MEJORA_LINKS]
        invalid += [{"estado": state.name} for state in EstadoMejora if state is not EstadoMejora.propuesta]
        invalid += [{key: value} for key, value in (
            ("aprobado_por_id", 1), ("fecha_aprobacion", DAY), ("fecha_implementacion", DAY),
            ("eficacia", "eficaz"), ("fecha_eficacia", DAY), ("revisor_eficacia_id", 1),
            ("motivo_cancelacion", "Motivo"), ("fecha_cancelacion", DAY))]
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError):
                    db.session.execute(Mejora.__table__.update().where(
                        Mejora.id_mejora == row.id_mejora).values(**values))
                    db.session.commit()
                db.session.rollback()
        # CHECKs must reject an owner reviewing and an unexplained ineffective result.
        owner = self.insert(Person, nombre="Owner", activo=True)
        reviewer = self.insert(Person, nombre="Reviewer", activo=True)
        reviewed = dict(estado="eficacia_revisada", responsable_id=owner,
                        aprobado_por_id=owner, fecha_aprobacion=DAY, fecha_implementacion=DAY,
                        eficacia="no_eficaz", fecha_eficacia=DAY, revisor_eficacia_id=reviewer)
        for values in (reviewed, reviewed | {"comentario_eficacia": "No", "revisor_eficacia_id": owner}):
            with self.assertRaises(IntegrityError):
                db.session.execute(Mejora.__table__.update().where(
                    Mejora.id_mejora == row.id_mejora).values(**values))
                db.session.commit()
            db.session.rollback()


class SqliteImprovementsTestCase(ImprovementCases, ServiceBase):
    pass


class ImprovementMigrationSqliteTestCase(unittest.TestCase):
    def test_empty_legacy_table_upgrades_and_downgrades_with_named_constraints(self):
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/c6e8f0a2b4d6_restructure_improvements.py'
        spec = importlib.util.spec_from_file_location('improvement_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual('ab4c6e8f0a12', migration.down_revision)
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        metadata = sa.MetaData()
        for table in ('users', 'personas', *(target.split('.')[0] for _, target in MEJORA_LINKS.values())):
            sa.Table(table, metadata, sa.Column('id', sa.Integer(), primary_key=True))
        sa.Table('mejoras', metadata,
                 sa.Column('id_mejora', sa.Integer(), primary_key=True),
                 sa.Column('no_conformidad', sa.Text(), nullable=False),
                 sa.Column('accion_correctiva', sa.Text()), sa.Column('accion_preventiva', sa.Text()),
                 sa.Column('fecha_implementacion', sa.DateTime()))
        metadata.create_all(engine)
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                inspector = sa.inspect(connection)
                columns = {c['name']: c for c in inspector.get_columns('mejoras')}
                self.assertIsInstance(columns['fecha_implementacion']['type'], sa.Date)
                self.assertFalse({'no_conformidad', 'accion_correctiva', 'accion_preventiva'} & columns.keys())
                expected = {c.name for c in Mejora.__table__.constraints if isinstance(c, sa.CheckConstraint)}
                self.assertEqual(expected, {c['name'] for c in inspector.get_check_constraints('mejoras')})
                self.assertEqual(8, len(inspector.get_foreign_keys('mejoras')))
                self.assertEqual(8, len(inspector.get_indexes('mejoras')))
                migration.downgrade()
                columns = {c['name'] for c in sa.inspect(connection).get_columns('mejoras')}
                self.assertEqual({'id_mejora', 'no_conformidad', 'accion_correctiva',
                                  'accion_preventiva', 'fecha_implementacion'}, columns)


@unittest.skipUnless(POSTGRES_URI, "TEST_POSTGRES_URI is not set")
class PostgresImprovementsTestCase(ImprovementCases, unittest.TestCase):
    """Use the explicitly configured disposable test database, like test_migrations."""
    def setUp(self):
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        # The existing Postgres fixture contract uses an isolated disposable database.
        with db.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
