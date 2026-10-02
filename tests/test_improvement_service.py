"""Improvements service: the rules specific to ``Mejora`` (HTML register side)."""

from __future__ import annotations

import unittest

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import Mejora

VALID = {"no_conformidad": "Etiqueta ilegible", "accion_correctiva": "Cambiar impresora",
         "accion_preventiva": "Mantenimiento"}


def service():
    from app.services import improvements

    return improvements


class ImprovementServiceTestCase(ServiceBase):
    def seed(self, **overrides) -> Mejora:
        result = db.session.execute(Mejora.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(Mejora, result.inserted_primary_key[0])

    def test_the_nonconformity_text_is_required_and_the_actions_are_optional(self) -> None:
        for data in ({"no_conformidad": "  "}, {"accion_correctiva": "x"}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        record = service().create(db.session, actor(), {"no_conformidad": "NC", "accion_preventiva": ""})
        self.assertEqual((None, None), (record.accion_correctiva, record.accion_preventiva))

    def test_the_implementation_date_is_not_writable_but_defaults_on_create(self) -> None:
        record = service().create(db.session, actor(), VALID)
        self.assertIsNotNone(record.fecha_implementacion)
        with self.assertRaises(errors().ValidationError):
            service().update(db.session, actor(), record.id_mejora, {"fecha_implementacion": None})

    def test_pages_are_in_id_order_with_the_total(self) -> None:
        for n in range(5):
            self.seed(no_conformidad=f"NC {n}")
        items, total = service().list_page(db.session, actor(), page=2, per_page=2)
        self.assertEqual((5, ["NC 2", "NC 3"]), (total, [m.no_conformidad for m in items]))

    def test_missing_records_raise_not_found_with_the_spec_message(self) -> None:
        with self.assertRaises(errors().NotFound) as caught:
            service().get(db.session, actor(), 999)
        self.assertEqual("Mejora no encontrada.", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
