"""Survey service: the rules specific to ``SatisfaccionCliente`` on the CRUD helper."""

from __future__ import annotations

import unittest
from datetime import date

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import SatisfaccionCliente

VALID = {"cliente": "Farmacia Sol", "fecha_encuesta": date(2026, 10, 1), "puntuacion": 9,
         "comentarios": "Muy bien"}


def service():
    from app.services import satisfaction

    return satisfaction


class SurveyServiceTestCase(ServiceBase):
    def seed(self, **overrides) -> SatisfaccionCliente:
        result = db.session.execute(SatisfaccionCliente.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(SatisfaccionCliente, result.inserted_primary_key[0])

    def test_the_score_must_be_a_whole_number_from_1_to_10(self) -> None:
        for bad in (0, 11, -3, "9", 9.5, None, True):
            with self.subTest(score=bad):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), VALID | {"puntuacion": bad})
                db.session.rollback()
        for good in (1, 10):
            record = service().create(db.session, actor(), VALID | {"puntuacion": good})
            self.assertEqual(good, record.puntuacion)

    def test_required_fields_and_lengths_mirror_the_form(self) -> None:
        for key in ("cliente", "fecha_encuesta", "puntuacion"):
            with self.subTest(missing=key):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), {k: v for k, v in VALID.items() if k != key})
        with self.assertRaises(errors().ValidationError):
            service().create(db.session, actor(), VALID | {"cliente": "x" * 101})
        record = service().create(db.session, actor(), VALID | {"comentarios": "  "})
        self.assertIsNone(record.comentarios)

    def test_list_orders_newest_first_and_filters_by_client_and_minimum_score(self) -> None:
        old = self.seed(cliente="Bar Luna", fecha_encuesta=date(2026, 9, 1), puntuacion=4)
        new = self.seed()
        who = actor()

        def ids(**filters):
            return [x.id for x in service().list_(db.session, who, **filters)]

        self.assertEqual([new.id, old.id], ids())
        self.assertEqual([old.id], ids(cliente="LUNA"))
        self.assertEqual([new.id], ids(puntuacion_minima=5))
        self.assertEqual([new.id, old.id], ids(puntuacion_minima=4))
        self.assertEqual([], ids(cliente="luna", puntuacion_minima=5))

    def test_the_resource_is_open_to_every_role_for_now(self) -> None:
        from app.models import RoleEnum

        record = service().create(db.session, actor(RoleEnum.OPERATIVO), VALID)
        self.assertEqual(7, record.created_by_id)


if __name__ == "__main__":
    unittest.main()
