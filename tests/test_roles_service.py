"""Roles service: the rules specific to ``RolResponsabilidad`` on the CRUD helper."""

from __future__ import annotations

import unittest

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import RolResponsabilidad


def roles():
    from app.services import roles_responsibilities

    return roles_responsibilities


class RoleServiceTestCase(ServiceBase):
    def seed(self, **overrides) -> RolResponsabilidad:
        values = {"rol": "Dirección"} | overrides
        result = db.session.execute(RolResponsabilidad.__table__.insert().values(**values))
        db.session.commit()
        return db.session.get(RolResponsabilidad, result.inserted_primary_key[0])

    def test_the_role_name_is_required_non_blank_and_at_most_50_characters(self) -> None:
        for data in ({}, {"rol": " "}, {"rol": "x" * 51}, {"rol": 3}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    roles().create(db.session, actor(), data)
                db.session.rollback()
        self.assertEqual("x" * 50, roles().create(db.session, actor(), {"rol": " " + "x" * 50}).rol)

    def test_the_commitment_is_a_real_boolean_and_defaults_to_false(self) -> None:
        for bad in (1, "true"):
            with self.assertRaises(errors().ValidationError):
                roles().create(db.session, actor(), {"rol": "A", "compromiso_calidad": bad})
            db.session.rollback()
        record = roles().create(db.session, actor(), {"rol": "A"})
        self.assertIs(False, record.compromiso_calidad)
        self.assertIs(True, roles().update(db.session, actor(), record.id_rol, {"compromiso_calidad": True}).compromiso_calidad)

    def test_a_duplicate_name_raises_conflict_on_create_and_update(self) -> None:
        self.seed()
        other = self.seed(rol="Calidad")
        with self.assertRaises(errors().Conflict) as caught:
            roles().create(db.session, actor(), {"rol": "Dirección"})
        self.assertEqual("Ya existe un rol con ese nombre.", caught.exception.message)
        db.session.rollback()
        with self.assertRaises(errors().Conflict):
            roles().update(db.session, actor(), other.id_rol, {"rol": "Dirección"})

    def test_writes_are_stamped_and_audited_and_reads_are_in_id_order(self) -> None:
        first, second = self.seed(), self.seed(rol="Calidad")
        updated = roles().update(db.session, actor(), first.id_rol, {"descripcion_politica_calidad": "x"})
        self.assertEqual(7, updated.updated_by_id)
        self.assertEqual(["update"], [row.action for row in self.audit_rows()])
        items, total = roles().list_page(db.session, actor(), page=1, per_page=1)
        self.assertEqual((2, [first.id_rol]), (total, [r.id_rol for r in items]))
        self.assertEqual("Rol y responsabilidad no encontrado.", self._missing(roles().get).message)
        roles().delete(db.session, actor(), second.id_rol)
        self.assertEqual(["update", "delete"], [row.action for row in self.audit_rows()])

    def _missing(self, call):
        with self.assertRaises(errors().NotFound) as caught:
            call(db.session, actor(), 999)
        return caught.exception

if __name__ == "__main__":
    unittest.main()
