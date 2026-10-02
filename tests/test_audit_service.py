"""Audit (Auditoria) service: authorization, validation, attribution and audit rows.

Read tests seed with a core insert; write tests go through the service with the
audit flush guard installed, so a write that forgets its audit row fails.
"""

from __future__ import annotations

from datetime import date


from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import Auditoria, EstadoAuditoriaEnum, RoleEnum

ADMIN, AUDITOR, OPERATIVO = (
    RoleEnum.ADMINISTRADOR,
    RoleEnum.AUDITOR,
    RoleEnum.OPERATIVO,
)
PENDIENTE = EstadoAuditoriaEnum.PENDIENTE
COMPLETADA = EstadoAuditoriaEnum.COMPLETADA


def service():
    from app.services import audits

    return audits


VALID = {
    "area_auditada": "Compras",
    "fecha": date(2026, 10, 1),
    "auditor": "Luis",
    "resultado": "Sin hallazgos",
    "accion_correctiva": "Ninguna",
}


class AuditServiceBase(ServiceBase):
    def seed(self, **overrides) -> Auditoria:
        values = {"estado": PENDIENTE} | VALID | overrides
        result = db.session.execute(Auditoria.__table__.insert().values(**values))
        db.session.commit()
        return db.session.get(Auditoria, result.inserted_primary_key[0])


class ReadTestCase(AuditServiceBase):
    def test_admin_and_auditor_read_and_operativo_is_denied(self) -> None:
        audit = self.seed()
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                self.assertIs(audit, service().get(db.session, actor(role), audit.id))
                service().list_page(db.session, actor(role))
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(OPERATIVO), audit.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_page(db.session, actor(OPERATIVO))

    def test_get_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().get(db.session, actor(ADMIN), 999)

    def test_read_requires_the_read_scope(self) -> None:
        audit = self.seed()
        with self.assertRaises(errors().PermissionDenied):
            service().get(db.session, actor(ADMIN, scopes={"write"}), audit.id)
        with self.assertRaises(errors().PermissionDenied):
            service().list_page(db.session, actor(ADMIN, scopes={"write"}))

    def test_filters_combine_with_and(self) -> None:
        a = self.seed(area_auditada="Compras", auditor="Luis", fecha=date(2026, 9, 1))
        b = self.seed(
            area_auditada="Ventas", auditor="Eva", fecha=date(2026, 10, 1),
            estado=COMPLETADA,
        )
        who = actor(AUDITOR)

        def ids(**filters):
            return [x.id for x in service().list_page(db.session, who, **filters)[0]]

        self.assertEqual([a.id, b.id], ids())
        self.assertEqual([a.id], ids(area="compr"))
        self.assertEqual([b.id], ids(auditor="EVA"))
        self.assertEqual([b.id], ids(estado=COMPLETADA))
        self.assertEqual([b.id], ids(fecha_inicio=date(2026, 9, 15)))
        self.assertEqual([a.id], ids(fecha_fin=date(2026, 9, 15)))
        self.assertEqual([a.id], ids(fecha_inicio=date(2026, 8, 1), fecha_fin=date(2026, 9, 30)))
        self.assertEqual([], ids(area="compr", auditor="eva"))

    def test_pagination_returns_the_page_and_the_filtered_total(self) -> None:
        for n in range(5):
            self.seed(area_auditada=f"Area {n}")
        items, total = service().list_page(
            db.session, actor(ADMIN), page=2, per_page=2
        )
        self.assertEqual(5, total)
        self.assertEqual(["Area 2", "Area 3"], [a.area_auditada for a in items])
        items, total = service().list_page(
            db.session, actor(ADMIN), page=9, per_page=2
        )
        self.assertEqual((5, []), (total, items))
        items, _ = service().list_page(db.session, actor(ADMIN), page=0, per_page=2)
        self.assertEqual(["Area 0", "Area 1"], [a.area_auditada for a in items])
