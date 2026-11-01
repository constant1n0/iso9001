"""Improvement CRUD preserves register ordering, no-ops and error contracts."""

import unittest
from datetime import date
from test_nonconformity_service import ServiceBase, actor, errors
from app.extensions import db
from app.models import Mejora
from app.services import improvements

DAY = date(2026, 10, 5)
VALID = {'descripcion': 'Etiqueta ilegible', 'fecha_identificacion': DAY, 'beneficio': 'Mantenimiento'}


class ImprovementServiceTestCase(ServiceBase):
    def seed(self, **overrides):
        result = db.session.execute(Mejora.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(Mejora, result.inserted_primary_key[0])

    def test_description_and_identification_date_are_required_and_benefit_is_optional(self):
        for data in ({'descripcion': '  ', 'fecha_identificacion': DAY}, {'beneficio': 'x'},
                     {'descripcion': 'NC'}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    improvements.create(db.session, actor(), data, today=DAY)
                db.session.rollback()
        record = improvements.create(db.session, actor(), VALID | {'beneficio': ''}, today=DAY)
        self.assertIsNone(record.beneficio)

    def test_implementation_date_is_not_writable_and_starts_absent(self):
        record = improvements.create(db.session, actor(), VALID, today=DAY)
        self.assertIsNone(record.fecha_implementacion)
        with self.assertRaises(errors().ValidationError):
            improvements.update(db.session, actor(), record.id_mejora,
                                {'fecha_implementacion': None}, today=DAY)

    def test_pages_are_in_id_order_with_the_total(self):
        for n in range(5):
            self.seed(descripcion=f'NC {n}')
        items, total = improvements.list_page(db.session, actor(), page=2, per_page=2)
        self.assertEqual((5, ['NC 2', 'NC 3']), (total, [m.descripcion for m in items]))

    def test_missing_records_raise_not_found_with_the_spec_message(self):
        with self.assertRaises(errors().NotFound) as caught:
            improvements.get(db.session, actor(), 999)
        self.assertEqual('Mejora no encontrada.', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
