"""Nonconformity fields and states (task NC-1 of ``nc-capa-loop``).

The state is an enum that plain writes cannot set: a new nonconformity starts
``abierta``, ``cancel`` and ``reopen`` are the only explicit transitions here,
and a closed or cancelled nonconformity is read-only until an administrator
reopens it. Corrective actions and the moves they drive come in NC-2.
"""

from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import update

from test_nonconformity_service import (
    ADMIN, AUDITOR, OPERATIVO, VALID, WriteBase, actor, errors, service,
)

from app.extensions import db
from app.models import (
    EstadoNoConformidad, GravedadNoConformidad, NoConformidad, OrigenNoConformidad,
)

E = EstadoNoConformidad
TODAY = date(2026, 10, 9)


class StateEnumTestCase(unittest.TestCase):
    def test_states_are_stored_by_name_and_shown_in_spanish(self) -> None:
        self.assertEqual(
            [("abierta", "Abierta"), ("accion_planificada", "Acción planificada"),
             ("en_verificacion", "En verificación"), ("cerrada", "Cerrada"),
             ("cancelada", "Cancelada")],
            [(m.name, m.value) for m in E],
        )
        self.assertEqual((E.abierta, E.accion_planificada, E.en_verificacion),
                         service().OPEN_STATES)
        self.assertEqual((E.cerrada, E.cancelada), service().TERMINAL_STATES)

    def test_origin_and_severity_members(self) -> None:
        self.assertEqual(["auditoria", "cliente", "proceso", "proveedor", "otro"],
                         list(OrigenNoConformidad.__members__))
        self.assertEqual(["mayor", "menor", "observacion"],
                         list(GravedadNoConformidad.__members__))
        self.assertEqual("Observación", GravedadNoConformidad.observacion.value)


class StatesBase(WriteBase):
    def force_state(self, nc: NoConformidad, estado: EstadoNoConformidad, **values) -> None:
        """Put a record in a state the NC-1 API cannot reach (closing comes in NC-2)."""
        db.session.execute(
            update(NoConformidad).where(NoConformidad.id == nc.id)
            .values(estado=estado, **values)
        )
        db.session.commit()
        db.session.refresh(nc)


class NewFieldsTestCase(StatesBase):
    def test_create_starts_open_and_stores_the_new_fields(self) -> None:
        nc = self.create(origen="cliente", gravedad=GravedadNoConformidad.mayor,
                         contencion="  Lote retenido ", causa_raiz="Molde gastado")
        self.assertEqual(
            (E.abierta, OrigenNoConformidad.cliente, GravedadNoConformidad.mayor,
             "Lote retenido", "Molde gastado", None, None),
            (nc.estado, nc.origen, nc.gravedad, nc.contencion, nc.causa_raiz,
             nc.motivo_cancelacion, nc.fecha_cierre),
        )
        row = self.audit_rows()[-1]
        self.assertEqual(("Abierta", "Cliente", "Mayor"),
                         (row.after["estado"], row.after["origen"], row.after["gravedad"]))

    def test_new_fields_are_optional_and_blank_selects_mean_none(self) -> None:
        nc = self.create(origen="", gravedad=None, contencion=" ", causa_raiz="")
        self.assertEqual((None, None, None, None),
                         (nc.origen, nc.gravedad, nc.contencion, nc.causa_raiz))

    def test_update_changes_origin_and_severity(self) -> None:
        nc = self.create()
        service().update(db.session, actor(), nc.id,
                         {"origen": "proveedor", "gravedad": "observacion"})
        db.session.commit()
        self.assertEqual((OrigenNoConformidad.proveedor, GravedadNoConformidad.observacion),
                         (nc.origen, nc.gravedad))

    def test_state_and_its_companions_are_not_writable(self) -> None:
        cases = {
            "state on create": VALID | {"estado": "abierta"},
            "unknown origin": VALID | {"origen": "Auditoría"},
            "unknown severity": VALID | {"gravedad": "critica"},
            "close date": VALID | {"fecha_cierre": TODAY},
            "cancel reason": VALID | {"motivo_cancelacion": "x"},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        nc = self.create()
        for data in ({"estado": "cerrada"}, {"estado": E.cerrada},
                     {"fecha_cierre": TODAY}, {"motivo_cancelacion": "x"}):
            with self.subTest(update=data):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(ADMIN), nc.id, data)
                db.session.rollback()
        self.assertEqual(E.abierta, nc.estado)
        self.assertEqual(1, len(self.audit_rows()))


class CancelTestCase(StatesBase):
    def test_admin_and_auditor_cancel_with_a_reason(self) -> None:
        for role in (ADMIN, AUDITOR):
            with self.subTest(role=role.name):
                nc = self.create()
                service().cancel(db.session, actor(role, user_id=8), nc.id,
                                 "  Registrada por error ", today=TODAY)
                db.session.commit()
                self.assertEqual((E.cancelada, "Registrada por error", TODAY, 8),
                                 (nc.estado, nc.motivo_cancelacion, nc.fecha_cierre,
                                  nc.updated_by_id))
                row = self.audit_rows()[-1]
                self.assertEqual("update", row.action)
                self.assertEqual("Abierta", row.before["estado"])
                self.assertEqual(
                    ("Cancelada", "Registrada por error", "2026-10-09"),
                    (row.after["estado"], row.after["motivo_cancelacion"],
                     row.after["fecha_cierre"]),
                )

    def test_any_open_state_can_be_cancelled(self) -> None:
        for estado in service().OPEN_STATES:
            with self.subTest(estado=estado.name):
                nc = self.create()
                self.force_state(nc, estado)
                service().cancel(db.session, actor(ADMIN), nc.id, "Duplicada", today=TODAY)
                db.session.commit()
                self.assertEqual(E.cancelada, nc.estado)
                self.assertIsNotNone(nc.fecha_cierre)

    def test_a_reason_is_required(self) -> None:
        nc = self.create()
        for motivo in ("", "   ", None):
            with self.subTest(motivo=motivo):
                with self.assertRaises(errors().ValidationError) as raised:
                    service().cancel(db.session, actor(ADMIN), nc.id, motivo, today=TODAY)
                self.assertEqual("El motivo de la cancelación es obligatorio.",
                                 raised.exception.message)
                db.session.rollback()
        self.assertEqual(E.abierta, nc.estado)
        self.assertEqual(1, len(self.audit_rows()))

    def test_operativo_and_read_only_tokens_cannot_cancel(self) -> None:
        nc = self.create()
        for who in (actor(OPERATIVO), actor(ADMIN, scopes={"read"}),
                    actor(AUDITOR, channel="mcp", scopes={"read"})):
            with self.subTest(actor=who):
                with self.assertRaises(errors().PermissionDenied):
                    service().cancel(db.session, who, nc.id, "Duplicada", today=TODAY)
        self.assertEqual(E.abierta, nc.estado)
        self.assertEqual(1, len(self.audit_rows()))

    def test_a_closed_or_cancelled_record_cannot_be_cancelled_again(self) -> None:
        for estado in service().TERMINAL_STATES:
            with self.subTest(estado=estado.name):
                nc = self.create()
                self.force_state(nc, estado)
                with self.assertRaises(errors().ValidationError):
                    service().cancel(db.session, actor(ADMIN), nc.id, "Otra vez", today=TODAY)
                db.session.rollback()

    def test_cancel_missing_raises_not_found(self) -> None:
        with self.assertRaises(errors().NotFound):
            service().cancel(db.session, actor(ADMIN), 999, "Duplicada", today=TODAY)


class ReopenTestCase(StatesBase):
    def test_admin_reopens_a_cancelled_record_clearing_date_and_reason(self) -> None:
        nc = self.create()
        service().cancel(db.session, actor(ADMIN), nc.id, "Duplicada", today=TODAY)
        db.session.commit()
        service().reopen(db.session, actor(ADMIN, user_id=9), nc.id)
        db.session.commit()
        self.assertEqual((E.abierta, None, None, 9),
                         (nc.estado, nc.fecha_cierre, nc.motivo_cancelacion, nc.updated_by_id))
        row = self.audit_rows()[-1]
        self.assertEqual(("Cancelada", "Abierta"), (row.before["estado"], row.after["estado"]))
        self.assertIsNone(row.after["motivo_cancelacion"])

    def test_admin_reopens_a_closed_record(self) -> None:
        nc = self.create()
        self.force_state(nc, E.cerrada, fecha_cierre=TODAY)
        service().reopen(db.session, actor(ADMIN), nc.id)
        db.session.commit()
        self.assertEqual((E.abierta, None), (nc.estado, nc.fecha_cierre))

    def test_only_an_administrator_reopens(self) -> None:
        nc = self.create()
        self.force_state(nc, E.cerrada, fecha_cierre=TODAY)
        for who in (actor(AUDITOR), actor(OPERATIVO), actor(ADMIN, scopes={"read"})):
            with self.subTest(actor=who):
                with self.assertRaises(errors().PermissionDenied):
                    service().reopen(db.session, who, nc.id)
        self.assertEqual(E.cerrada, nc.estado)

    def test_an_open_record_cannot_be_reopened(self) -> None:
        nc = self.create()
        for estado in service().OPEN_STATES:
            with self.subTest(estado=estado.name):
                self.force_state(nc, estado)
                with self.assertRaises(errors().ValidationError):
                    service().reopen(db.session, actor(ADMIN), nc.id)
                db.session.rollback()
        self.assertEqual(1, len(self.audit_rows()))


class ReadOnlyTestCase(StatesBase):
    def test_closed_and_cancelled_records_refuse_plain_updates(self) -> None:
        for estado in service().TERMINAL_STATES:
            with self.subTest(estado=estado.name):
                nc = self.create()
                self.force_state(nc, estado, fecha_cierre=TODAY)
                rows = len(self.audit_rows())
                with self.assertRaises(errors().ValidationError) as raised:
                    service().update(db.session, actor(ADMIN), nc.id, {"responsable": "Eva"})
                self.assertIn("un administrador puede reabrirla", raised.exception.message)
                db.session.rollback()
                db.session.refresh(nc)
                self.assertEqual("Ana", nc.responsable)
                self.assertEqual(rows, len(self.audit_rows()))

    def test_open_states_still_accept_plain_updates(self) -> None:
        nc = self.create()
        self.force_state(nc, E.en_verificacion)
        service().update(db.session, actor(), nc.id, {"causa_raiz": "Formación"})
        db.session.commit()
        self.assertEqual((E.en_verificacion, "Formación"), (nc.estado, nc.causa_raiz))


class PermissionHelpersTestCase(StatesBase):
    def test_may_cancel_and_may_reopen_follow_role_and_state(self) -> None:
        nc = self.create()
        self.assertEqual(
            [True, True, False],
            [service().may_cancel(actor(r), nc) for r in (ADMIN, AUDITOR, OPERATIVO)],
        )
        self.assertFalse(service().may_reopen(actor(ADMIN), nc))
        self.force_state(nc, E.cancelada)
        self.assertFalse(service().may_cancel(actor(ADMIN), nc))
        self.assertEqual(
            [True, False, False],
            [service().may_reopen(actor(r), nc) for r in (ADMIN, AUDITOR, OPERATIVO)],
        )
        self.assertFalse(service().may_reopen(actor(ADMIN, scopes={"read"}), nc))


class StateFilterTestCase(StatesBase):
    def test_list_filters_by_member_or_name_and_rejects_other_text(self) -> None:
        open_nc = self.create(descripcion="Abierta")
        closed = self.create(descripcion="Cerrada")
        self.force_state(closed, E.cerrada)
        who = actor()
        self.assertEqual([closed], service().list_(db.session, who, estado=E.cerrada))
        self.assertEqual([open_nc], service().list_(db.session, who, estado="abierta"))
        self.assertEqual(([closed], 1),
                         service().list_page(db.session, who, estado="cerrada"))
        with self.assertRaises(errors().ValidationError):
            service().list_(db.session, who, estado="Cerrada")


if __name__ == "__main__":
    unittest.main()
