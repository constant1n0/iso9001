"""Interested parties service: the rules specific to ``ParteInteresada``."""

from __future__ import annotations

import unittest

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import ParteInteresada

VALID = {"nombre": "Clientes", "necesidades_expectativas": "Plazos", "requisitos_identificados": "ISO",
         "objetivo_estrategico": "Fidelizar"}


def service():
    from app.services import stakeholders

    return stakeholders


class StakeholderServiceTestCase(ServiceBase):
    def seed(self, **overrides) -> ParteInteresada:
        result = db.session.execute(ParteInteresada.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(ParteInteresada, result.inserted_primary_key[0])

    def test_name_is_required_and_limited_while_the_texts_are_optional(self) -> None:
        for data in ({"nombre": " "}, {"nombre": "x" * 51}, {}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        record = service().create(db.session, actor(), {"nombre": " Proveedores ", "objetivo_estrategico": ""})
        self.assertEqual(("Proveedores", None), (record.nombre, record.objetivo_estrategico))

    def test_a_duplicate_name_raises_conflict_with_a_spanish_message(self) -> None:
        self.seed()
        with self.assertRaises(errors().Conflict) as caught:
            service().create(db.session, actor(), VALID)
        self.assertEqual("Ya existe una parte interesada con ese nombre.", caught.exception.message)
        db.session.rollback()
        other = self.seed(nombre="Proveedores")
        with self.assertRaises(errors().Conflict) as caught:
            service().update(db.session, actor(), other.id_interesado, {"nombre": "Clientes"})
        self.assertEqual("Ya existe una parte interesada con ese nombre.", caught.exception.message)

    def test_list_is_ordered_by_name(self) -> None:
        b = self.seed(nombre="Bancos")
        a = self.seed(nombre="Aseguradoras")
        self.assertEqual([a.id_interesado, b.id_interesado], [x.id_interesado for x in service().list_(db.session, actor())])

    def test_unrelated_columns_are_not_writable_and_every_role_may_write(self) -> None:
        record = service().create(db.session, actor(), VALID)
        self.assertEqual(7, record.created_by_id)
        with self.assertRaises(errors().ValidationError):
            service().update(db.session, actor(), record.id_interesado, {"id_interesado": 5})


if __name__ == "__main__":
    unittest.main()
