"""The training resources service on the CRUD helper.

The risks register moved to ``tests/test_risks_service.py`` (RO-2 of
``risks-opportunities``).
"""

from __future__ import annotations

import unittest

from test_nonconformity_service import ServiceBase, actor as _actor, errors

from app.extensions import db
from app.models import RoleEnum


def actor(**kwargs):
    """Administrators may do everything, so these tests exercise the register rules."""
    return _actor(role=RoleEnum.ADMINISTRADOR, **kwargs)


def resources():
    from app.services import training_resources

    return training_resources


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
