"""Nonconformity service: the pilot every other module's service copies.

Runs against an in-memory database with the audit flush guard installed, so a
write that forgets its audit row fails the test instead of passing silently.
"""

from __future__ import annotations

import unittest
from datetime import date


from test_audit import AuditDbBase

from app.extensions import db
from app.models import AuditLog, NoConformidad, RoleEnum
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)


def actor(role=OPERATIVO, channel="web", user_id=7, scopes=None) -> Actor:
    return Actor(
        user_id=user_id,
        label=f"user{user_id}",
        role=role,
        channel=channel,
        scopes=None if scopes is None else frozenset(scopes),
    )


def service():
    from app.services import nonconformities

    return nonconformities


def errors():
    from app.services import errors as domain_errors

    return domain_errors


VALID = {
    "descripcion": "Pieza fuera de tolerancia",
    "fecha_detectada": date(2026, 10, 1),
    "responsable": "Ana",
    "accion_correctiva": "Reajustar la maquina",
}


class ServiceBase(AuditDbBase):
    def setUp(self) -> None:
        super().setUp()
        from app.services import audit

        self.audit = audit
        self.addCleanup(audit.install_audit_guard(db.session, audit.AUDITED_MODELS))

    def seed(self, **overrides) -> NoConformidad:
        # Core insert: read tests must not depend on the write API, and core
        # statements bypass the ORM flush guard installed above.
        result = db.session.execute(
            NoConformidad.__table__.insert().values(**(VALID | overrides))
        )
        db.session.commit()
        return db.session.get(NoConformidad, result.inserted_primary_key[0])

    def audit_rows(self):
        return db.session.query(AuditLog).order_by(AuditLog.id).all()


class StateConstantTestCase(unittest.TestCase):
    def test_states_are_the_three_spanish_values(self) -> None:
        self.assertEqual(
            ("Abierta", "En proceso", "Cerrada"), service().ESTADOS_NO_CONFORMIDAD
        )
        self.assertEqual("Abierta", service().ESTADO_ABIERTA)
        self.assertEqual("Cerrada", service().ESTADO_CERRADA)

    def test_forms_reuse_the_service_constant(self) -> None:
        from app import forms

        self.assertIs(service().ESTADOS_NO_CONFORMIDAD, forms.ESTADOS_NO_CONFORMIDAD)


class ReadTestCase(ServiceBase):
    def test_get_returns_the_record_for_every_role(self) -> None:
        nc = self.seed()
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.assertIs(nc, service().get(db.session, actor(role), nc.id))

    def test_get_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(), 999)

    def test_read_requires_the_read_scope(self) -> None:
        nc = self.seed()
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(scopes={"write"}), nc.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_(db.session, actor(scopes={"write"}))

    def test_list_orders_newest_first_and_filters_like_the_web_list(self) -> None:
        old = self.seed(descripcion="Ruido en Linea", fecha_detectada=date(2026, 9, 1))
        new = self.seed(
            descripcion="Fuga de aceite", fecha_detectada=date(2026, 10, 1), estado="Cerrada"
        )
        who = actor()
        self.assertEqual([new, old], service().list_(db.session, who))
        self.assertEqual([old], service().list_(db.session, who, descripcion="linea"))
        self.assertEqual([new], service().list_(db.session, who, estado="Cerrada"))
        self.assertEqual([old], service().list_(db.session, who, fecha_detectada=date(2026, 9, 1)))
        self.assertEqual([], service().list_(db.session, who, descripcion="x", estado="Abierta"))

    def test_available_states_append_legacy_values_after_the_fixed_ones(self) -> None:
        self.seed(estado="Abierta")
        self.seed_legacy_states()
        self.assertEqual(
            ["Abierta", "En proceso", "Cerrada", "Pendiente", "Zeta"],
            service().available_states(db.session, actor()),
        )

    def seed_legacy_states(self) -> None:
        # Legacy rows predate the service; a core insert bypasses ORM flush hooks.
        db.session.execute(
            NoConformidad.__table__.insert(),
            [
                {"descripcion": "old", "fecha_detectada": date(2026, 1, 1), "estado": "Pendiente"},
                {"descripcion": "old", "fecha_detectada": date(2026, 1, 1), "estado": "Zeta"},
            ],
        )
        db.session.commit()
