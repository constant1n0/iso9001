"""Risks and training resources services on the CRUD helper."""

from __future__ import annotations

import unittest

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import RiesgoOportunidad, TipoEnum


def risks():
    from app.services import risks_opportunities

    return risks_opportunities


def resources():
    from app.services import training_resources

    return training_resources


class RiskServiceTestCase(ServiceBase):
    def test_the_type_accepts_the_enum_value_and_rejects_anything_else(self) -> None:
        record = risks().create(db.session, actor(), {"tipo": "Oportunidad", "descripcion": "d"})
        self.assertIs(TipoEnum.Oportunidad, record.tipo)
        for bad in ("Amenaza", None, 1):
            with self.assertRaises(errors().ValidationError):
                risks().create(db.session, actor(), {"tipo": bad, "descripcion": "d"})
            db.session.rollback()

    def test_the_description_is_required_and_the_other_texts_are_optional(self) -> None:
        for data in ({"tipo": "Riesgo"}, {"tipo": "Riesgo", "descripcion": " "}):
            with self.assertRaises(errors().ValidationError):
                risks().create(db.session, actor(), data)
            db.session.rollback()
        record = risks().create(db.session, actor(), {"tipo": "Riesgo", "descripcion": "d", "plan_accion": ""})
        self.assertEqual((None, None), (record.plan_accion, record.objetivo_calidad))

    def test_updates_audit_only_changes_and_missing_records_raise_not_found(self) -> None:
        record = risks().create(db.session, actor(), {"tipo": "Riesgo", "descripcion": "d"})
        risks().update(db.session, actor(), record.id_riesgo, {"descripcion": "d"})
        risks().update(db.session, actor(), record.id_riesgo, {"plan_accion": "p"})
        self.assertEqual(["create", "update"], [row.action for row in self.audit_rows()])
        with self.assertRaises(errors().NotFound) as caught:
            risks().delete(db.session, actor(), 999)
        self.assertEqual("Riesgo u oportunidad no encontrado.", caught.exception.message)
        self.assertIsNone(db.session.query(RiesgoOportunidad).filter_by(descripcion="x").first())


class ResourceServiceTestCase(ServiceBase):
    def test_the_needed_resource_is_required_and_the_flag_a_real_boolean(self) -> None:
        for data in ({}, {"recurso_necesario": " "}, {"recurso_necesario": "R", "capacitacion_personal": "si"}):
            with self.assertRaises(errors().ValidationError):
                resources().create(db.session, actor(), data)
            db.session.rollback()
        record = resources().create(db.session, actor(), {"recurso_necesario": "R", "descripcion_documentacion": ""})
        self.assertEqual((False, None), (record.capacitacion_personal, record.descripcion_documentacion))

    def test_writes_are_stamped_audited_and_listed_in_id_order(self) -> None:
        first = resources().create(db.session, actor(), {"recurso_necesario": "A"})
        second = resources().create(db.session, actor(), {"recurso_necesario": "B"})
        resources().update(db.session, actor(), first.id_recurso, {"capacitacion_personal": True})
        resources().update(db.session, actor(), first.id_recurso, {"capacitacion_personal": True})  # no-op
        self.assertEqual(["create", "create", "update"], [row.action for row in self.audit_rows()])
        self.assertEqual(7, first.updated_by_id)
        items, total = resources().list_page(db.session, actor(), page=2, per_page=1)
        self.assertEqual((2, [second.id_recurso]), (total, [r.id_recurso for r in items]))
        with self.assertRaises(errors().NotFound) as caught:
            resources().get(db.session, actor(), 999)
        self.assertEqual("Recurso de capacitación no encontrado.", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
